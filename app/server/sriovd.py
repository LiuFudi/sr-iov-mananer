#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 LiuFudi
# 作者主页: https://github.com/LiuFudi
#
# sriovd.py -- package-user page/API process.
# A privileged child handles hardware operations over an inherited socketpair.

import argparse
import grp
import http.server
import json
import os
import pwd
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sriovlib  # noqa: E402

APP_NAME = "sriov-vfctl"
MAX_RESPONSE = 1024 * 1024


class Log(object):
    def __init__(self, path):
        self.path = path

    def __call__(self, message):
        line = "%s - %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        else:
            sys.stderr.write(line + "\n")


class WorkerError(Exception):
    pass


class WorkerClient(object):
    """Synchronous client for the fixed-command root worker."""

    def __init__(self, sock, log):
        self.sock = sock
        self.reader = self.sock.makefile("rb")
        self.writer = self.sock.makefile("wb")
        self.log = log
        self.lock = threading.Lock()
        self.broken = False
        self.worker_user = "-"
        self.worker_pid = "-"
        self._read_message(30)
        if self.last_message.get("event") != "ready":
            raise WorkerError("硬件 worker 未就绪")
        self.worker_user = str(self.last_message.get("user") or "-")
        self.worker_pid = str(self.last_message.get("pid") or "-")

    def _read_message(self, timeout):
        self.sock.settimeout(timeout)
        line = self.reader.readline(MAX_RESPONSE + 1)
        if not line:
            self.broken = True
            raise WorkerError("硬件 worker 已断开")
        if len(line) > MAX_RESPONSE or not line.endswith(b"\n"):
            self.broken = True
            raise WorkerError("硬件 worker 响应异常")
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            self.broken = True
            raise WorkerError("硬件 worker 响应格式错误") from exc
        if not isinstance(message, dict):
            self.broken = True
            raise WorkerError("硬件 worker 响应格式错误")
        self.last_message = message
        return message

    def request(self, operation, params=None, timeout=30):
        with self.lock:
            if self.broken:
                raise WorkerError("硬件 worker 不可用")
            request = {"op": operation, "params": params or {}}
            try:
                self.writer.write(
                    (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                )
                self.writer.flush()
                response = self._read_message(timeout)
            except (OSError, WorkerError) as exc:
                self.broken = True
                self.log("worker request failed: %s" % exc)
                raise WorkerError(str(exc)) from exc
            if not response.get("ok"):
                raise WorkerError(str(response.get("error") or "硬件操作失败"))
            return response.get("result")

    def close(self):
        for handle in (self.writer, self.reader, self.sock):
            try:
                handle.close()
            except OSError:
                pass


class InheritedUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """HTTP server using an already-bound listening Unix socket."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(self, bound_socket, handler):
        socketserver.BaseServer.__init__(self, bound_socket.getsockname(), handler)
        self.socket = bound_socket
        self.server_activate()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "%s/%s" % (APP_NAME, sriovlib.VERSION)
    protocol_version = "HTTP/1.1"

    def address_string(self):
        return "unix"

    def log_message(self, fmt, *args):
        self.server.log("http %s" % (fmt % args))

    def _relative(self):
        path = urllib.parse.urlparse(self.path).path
        prefix = self.server.gateway_prefix
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        if not path.startswith("/"):
            path = "/" + path
        return urllib.parse.unquote(path)

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or ()):
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False))

    def _error(self, message, code=400):
        self._json({"ok": False, "error": message}, code)

    def _is_admin(self):
        return (self.headers.get("X-Trim-Isadmin") or "").strip().lower() == "true"

    def _require_admin(self):
        if self._is_admin():
            return True
        self._error("需要管理员权限", 403)
        return False

    def _status_payload(self):
        data = self.server.worker.request("status", timeout=30)
        cfg = sriovlib.load_config(self.server.config_path)
        data["configured_count"] = cfg.get("count", 1)
        return sriovlib.decorate_vm_labels(data, cfg)

    @staticmethod
    def _parse_count(value):
        if isinstance(value, bool):
            raise ValueError
        return int(value)

    @staticmethod
    def _in_use_message(used):
        names = "、".join(item.get("vm_label") or item["vm"] for item in used)
        return "VF 正被虚拟机 %s 使用，这是正常占用状态；请关闭虚拟机后再保存 VF 设置" % names

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self):
        path = self._relative()
        if path.startswith("/api/") and not self._require_admin():
            return
        try:
            if path in ("/", "/index.html"):
                self._serve_index()
                return
            if path == "/api/status":
                self._json({"ok": True, "status": self._status_payload()})
                return
            if path == "/api/vms":
                self._json({"ok": True, "vms": self.server.worker.request("vm_inventory", timeout=60)})
                return
            if path == "/api/config":
                self._json({"ok": True, "config": sriovlib.load_config(self.server.config_path)})
                return
            if path.startswith("/api/"):
                self._error("未知接口", 404)
                return
        except WorkerError as exc:
            self.server.log("worker error on GET: %s" % exc)
            self._error("硬件状态不可用：%s" % exc, 503)
            return
        self._serve_static(path)

    def do_POST(self):
        if not self._require_admin():
            return
        path = self._relative()
        body = self._read_json()
        cfg_path = self.server.config_path
        if not self.server.operation_lock.acquire(False):
            self._error("另一个管理操作正在执行，请稍后再试", 409)
            return

        try:
            if path == "/api/set":
                try:
                    n = self._parse_count(body.get("count"))
                except (TypeError, ValueError):
                    self._error("数量必须是整数")
                    return
                status = self._status_payload()
                used = status["vfs_in_use"]
                if used:
                    message = self._in_use_message(used)
                    self.server.log("set deferred: %s" % message)
                    self._json({
                        "ok": False,
                        "kind": "in_use",
                        "severity": "info",
                        "message": message,
                        "status": status,
                    })
                    return
                result = self.server.worker.request("set_count", {"count": n}, timeout=30)
                ok = bool(result.get("ok"))
                message = str(result.get("message") or "")
                if ok:
                    cfg = sriovlib.load_config(cfg_path)
                    cfg["count"] = n
                    sriovlib.save_config(cfg_path, cfg)
                    self.server.log("set count=%d: %s" % (n, message))
                self._json({
                    "ok": ok,
                    "message": message,
                    "status": self._status_payload(),
                })
                return

            if path == "/api/persist":
                try:
                    n = self._parse_count(body.get("count"))
                except (TypeError, ValueError):
                    self._error("数量必须是整数")
                    return
                status = self._status_payload()
                maximum = max(int(status.get("totalvfs") or 0), sriovlib.DEFAULT_MAX_VFS)
                if n < 0 or n > maximum:
                    self._error("数量超出范围：0..%d" % maximum)
                    return
                used = status["vfs_in_use"]
                if used:
                    message = self._in_use_message(used)
                    self.server.log("persist deferred: %s" % message)
                    self._json({
                        "ok": False,
                        "kind": "in_use",
                        "severity": "info",
                        "message": message,
                        "status": status,
                    })
                    return
                cfg = sriovlib.load_config(cfg_path)
                cfg["count"] = n
                sriovlib.save_config(cfg_path, cfg)
                self.server.log("persist count=%d" % n)
                self._json({
                    "ok": True,
                    "message": "开机自动创建数量已设为 %d" % n,
                    "config": cfg,
                    "status": self._status_payload(),
                })
                return

            if path == "/api/vm-alias":
                domain = str(body.get("name") or "").strip()
                alias = str(body.get("alias") or "").strip()
                if not domain:
                    self._error("缺少虚拟机域名")
                    return
                if len(alias) > 40 or "\n" in alias or "\r" in alias:
                    self._error("名称最多 40 个字符且不能包含换行")
                    return
                inventory = self.server.worker.request("vm_inventory", timeout=60)
                target = inventory.get(domain)
                if not target:
                    self._error("虚拟机未运行或不存在", 404)
                    return
                cfg = sriovlib.load_config(cfg_path)
                aliases = cfg.get("vm_aliases", {})
                key = target.get("uuid") or ("name:" + domain)
                if alias and alias != domain:
                    aliases[key] = alias
                else:
                    aliases.pop(key, None)
                cfg["vm_aliases"] = aliases
                sriovlib.save_config(cfg_path, cfg)
                self.server.log("vm alias name=%s alias=%s" % (domain, alias or "-"))
                self._json({
                    "ok": True,
                    "message": ("已命名为 %s" % alias) if alias and alias != domain else "已恢复虚拟机域名",
                    "status": self._status_payload(),
                })
                return

            if path == "/api/inject":
                status = self._status_payload()
                used = status["vfs_in_use"]
                if used:
                    message = self._in_use_message(used)
                    self.server.log("inject deferred: %s" % message)
                    self._json({
                        "ok": False,
                        "kind": "in_use",
                        "severity": "info",
                        "message": message,
                        "log": [message],
                        "ready": status["sriov_ready"],
                        "status": status,
                    })
                    return
                rebuild = body.get("rebuild_initramfs") is True
                result = self.server.worker.request(
                    "inject",
                    {"rebuild_initramfs": rebuild},
                    timeout=620,
                )
                ok = bool(result.get("ok"))
                log_lines = result.get("log") or []
                status = self._status_payload()
                message = log_lines[-1] if log_lines else ("注入完成" if ok else "注入失败")
                self.server.log("inject ok=%s: %s" % (ok, message))
                self._json({
                    "ok": ok,
                    "message": message,
                    "log": log_lines,
                    "ready": status["sriov_ready"],
                    "status": status,
                })
                return

            self._error("未知接口", 404)
        except WorkerError as exc:
            self.server.log("worker error: %s" % exc)
            self._error("硬件操作失败：%s" % exc, 503)
        finally:
            self.server.operation_lock.release()

    def _serve_index(self):
        index = os.path.join(self.server.ui_dir, "index.html")
        try:
            with open(index, "rb") as handle:
                html = handle.read()
        except OSError:
            self._error("UI 文件缺失", 500)
            return
        html = html.replace(b"__BASE_PATH__", self.server.base_path.encode("utf-8"))
        self._send(200, html, "text/html; charset=utf-8")

    def _serve_static(self, path):
        rel = path.lstrip("/")
        target = resolve_static_file(self.server.ui_dir, rel)
        if target is None:
            self._error("not found", 404)
            return
        ctype = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
            ".ico": "image/x-icon",
            ".txt": "text/plain; charset=utf-8",
        }.get(os.path.splitext(target)[1].lower(), "application/octet-stream")
        try:
            with open(target, "rb") as handle:
                data = handle.read()
        except OSError:
            self._error("not found", 404)
            return
        self._send(200, data, ctype)


def resolve_static_file(ui_dir, rel):
    """Resolve a static request and reject NUL, traversal, and external symlinks."""
    if not rel or "\x00" in rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts:
        return None
    base = os.path.realpath(ui_dir)
    target = os.path.realpath(os.path.join(base, rel))
    try:
        if os.path.commonpath((base, target)) != base:
            return None
    except ValueError:
        return None
    if not os.path.isfile(target):
        return None
    return target


def _identity():
    if os.geteuid() != 0:
        pw = pwd.getpwuid(os.geteuid())
        group = grp.getgrgid(pw.pw_gid)
        return pw, group, False
    username = os.environ.get("TRIM_USERNAME") or APP_NAME
    if username == "root":
        raise RuntimeError("应用监听进程不得使用 root")
    pw = pwd.getpwnam(username)
    if pw.pw_uid == 0:
        raise RuntimeError("应用监听进程不得使用 root")
    groupname = os.environ.get("TRIM_GROUPNAME") or grp.getgrgid(pw.pw_gid).gr_name
    group = grp.getgrnam(groupname)
    return pw, group, True


def _prepare_private_tree(path, uid, gid, as_root, mode=0o770):
    os.makedirs(path, exist_ok=True)
    if as_root:
        os.chown(path, uid, gid)
    os.chmod(path, mode)
    for root, dirs, files in os.walk(path):
        for name in dirs:
            item = os.path.join(root, name)
            if as_root:
                os.chown(item, uid, gid)
            os.chmod(item, mode)
        for name in files:
            item = os.path.join(root, name)
            if as_root:
                os.chown(item, uid, gid)


def _bind_socket(path, uid, gid, as_root):
    if os.path.lexists(path):
        try:
            if not stat.S_ISSOCK(os.lstat(path).st_mode):
                raise RuntimeError("socket 路径已被非 socket 文件占用")
            os.unlink(path)
        except OSError as exc:
            raise RuntimeError("无法清理旧 socket：%s" % exc) from exc
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(64)
    os.chmod(path, 0o600)
    if as_root:
        os.chown(path, uid, gid)
    return sock


def _drop_privileges(pw, group):
    os.setgroups(os.getgrouplist(pw.pw_name, group.gr_gid))
    os.setgid(group.gr_gid)
    os.setuid(pw.pw_uid)
    os.umask(0o077)


def _verify_listener_identity(socket_path, uid):
    if os.geteuid() != uid:
        raise RuntimeError("监听进程未降权到目标应用用户")
    info = os.stat(socket_path)
    if info.st_uid != uid:
        raise RuntimeError("app.sock 属主与监听用户不一致")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("app.sock 权限必须为 0600")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--appdest", required=True)
    parser.add_argument("--etc", required=True)
    parser.add_argument("--var", required=True)
    parser.add_argument("--ui", default="")
    parser.add_argument("--socket", default="")
    parser.add_argument("--gateway-prefix", default="")
    args = parser.parse_args()

    ui_dir = args.ui or os.path.join(args.appdest, "ui")
    socket_path = args.socket or os.path.join(args.appdest, "app.sock")
    config_path = os.path.join(args.etc, "config.json")
    log_path = os.path.join(args.var, "info.log")
    log = Log(log_path)
    python = sys.executable or "/usr/bin/python3"
    pw, group, as_root = _identity()

    _prepare_private_tree(args.etc, pw.pw_uid, group.gr_gid, as_root)
    _prepare_private_tree(args.var, pw.pw_uid, group.gr_gid, as_root)
    os.makedirs(args.var, exist_ok=True)
    if as_root and os.path.exists(log_path):
        os.chown(log_path, pw.pw_uid, group.gr_gid)

    log("starting %s %s (kernel %s)" % (APP_NAME, sriovlib.VERSION, sriovlib.kernel_version()))
    log("listener identity target=%s worker identity=%s" % (
        pw.pw_name,
        "root" if as_root else pwd.getpwuid(os.geteuid()).pw_name,
    ))

    parent_worker, child_worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    log_handle = open(log_path, "ab", buffering=0)
    if as_root:
        os.fchown(log_handle.fileno(), pw.pw_uid, group.gr_gid)
        os.fchmod(log_handle.fileno(), 0o644)
    worker_path = os.path.join(HERE, "hw_worker.py")
    worker_env = dict(os.environ)
    worker_env["PYTHONDONTWRITEBYTECODE"] = "1"
    worker = subprocess.Popen(
        [
            python,
            worker_path,
            "--fd",
            str(child_worker.fileno()),
            "--etc",
            args.etc,
            "--var",
            args.var,
        ],
        pass_fds=(child_worker.fileno(),),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        close_fds=True,
        env=worker_env,
    )
    child_worker.close()
    log_handle.close()
    worker_uid = os.stat("/proc/%d" % worker.pid).st_uid
    if as_root and worker_uid != 0:
        raise RuntimeError("硬件 worker 未以 root 启动")

    listen_socket = _bind_socket(socket_path, pw.pw_uid, group.gr_gid, as_root)
    if as_root:
        _drop_privileges(pw, group)
    else:
        os.umask(0o077)
    _verify_listener_identity(socket_path, pw.pw_uid)

    worker_client = WorkerClient(parent_worker, log)
    server = InheritedUnixHTTPServer(listen_socket, Handler)
    server.log = log
    server.ui_dir = ui_dir
    server.config_path = config_path
    server.gateway_prefix = args.gateway_prefix
    server.base_path = args.gateway_prefix.rstrip("/") + "/" if args.gateway_prefix else "/"
    server.timeout = 1
    server.operation_lock = threading.Lock()
    server.worker = worker_client

    stopping = threading.Event()

    def stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("listening on %s as %s; worker pid=%s user=%s" % (
        socket_path,
        pwd.getpwuid(os.geteuid()).pw_name,
        worker_client.worker_pid,
        worker_client.worker_user,
    ))
    try:
        while not stopping.is_set():
            if worker.poll() is not None:
                log("worker exited unexpectedly with code %s" % worker.returncode)
                break
            server.handle_request()
    finally:
        worker_client.close()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log("worker did not exit after IPC close")
        try:
            server.server_close()
        except OSError:
            pass
        try:
            if os.path.lexists(socket_path) and stat.S_ISSOCK(os.lstat(socket_path).st_mode):
                os.unlink(socket_path)
        except OSError:
            pass
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
