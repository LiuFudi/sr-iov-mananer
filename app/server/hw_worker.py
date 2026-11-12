#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 LiuFudi
#
# hw_worker.py -- privileged hardware worker.
# It only accepts fixed, structured operations over an inherited socketpair.

import argparse
import json
import os
import pwd
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sriovlib  # noqa: E402

MAX_REQUEST = 64 * 1024
ALLOWED_OPS = {"status", "set_count", "vm_inventory", "inject"}


class WorkerLog(object):
    def __init__(self, path):
        self.path = path

    def __call__(self, message):
        line = "%s - hw_worker: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            sys.stderr.write(line + "\n")


class HardwareWorker(object):
    def __init__(self, fd, config_path, log):
        self.sock = socket.socket(fileno=fd)
        self.reader = self.sock.makefile("rb")
        self.writer = self.sock.makefile("wb")
        self.config_path = config_path
        self.log = log

    def _send(self, payload):
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.writer.write(data)
        self.writer.flush()

    @staticmethod
    def _valid_int(value):
        return type(value) is int

    def _dispatch(self, operation, params):
        if operation not in ALLOWED_OPS:
            raise ValueError("不允许的操作")
        if operation == "status":
            return sriovlib.status()
        if operation == "set_count":
            count = params.get("count")
            if not self._valid_int(count):
                raise ValueError("数量必须是整数")
            ok, message = sriovlib.set_count(count)
            return {"ok": ok, "message": message}
        if operation == "vm_inventory":
            return sriovlib.vm_inventory()
        if operation == "inject":
            rebuild = params.get("rebuild_initramfs") is True
            ok, lines = sriovlib.inject(
                config_path=self.config_path,
                rebuild_initramfs=rebuild,
            )
            return {"ok": ok, "log": lines}
        raise ValueError("不允许的操作")

    def run(self):
        try:
            ok, message = sriovlib.apply_config(self.config_path)
            self.log("startup apply_config: %s (%s)" % (message, "ok" if ok else "failed"))
        except Exception as exc:  # noqa: BLE001
            self.log("startup apply_config crashed: %s" % exc)
        user = pwd.getpwuid(os.geteuid()).pw_name
        self._send({"event": "ready", "pid": os.getpid(), "user": user})
        self.log("ready pid=%d user=%s" % (os.getpid(), user))

        while True:
            raw = self.reader.readline(MAX_REQUEST + 1)
            if not raw:
                break
            if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                self._send({"ok": False, "error": "请求过长或格式错误"})
                continue
            try:
                request = json.loads(raw.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("请求必须是对象")
                operation = request.get("op")
                params = request.get("params") or {}
                if not isinstance(operation, str) or not isinstance(params, dict):
                    raise ValueError("请求格式错误")
                result = self._dispatch(operation, params)
                self._send({"ok": True, "result": result})
            except (UnicodeDecodeError, ValueError, TypeError) as exc:
                self._send({"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self.log("operation crashed: %s" % exc)
                self._send({"ok": False, "error": "硬件操作失败"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--etc", required=True)
    parser.add_argument("--var", required=True)
    args = parser.parse_args()

    os.umask(0o077)
    log = WorkerLog(os.path.join(args.var, "info.log"))
    worker = HardwareWorker(
        fd=args.fd,
        config_path=os.path.join(args.etc, "config.json"),
        log=log,
    )
    try:
        worker.run()
    finally:
        worker.log("exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
