# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import socket
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app", "server"))
import sriovd  # noqa: E402


class FakeWorker(object):
    worker_user = "package"
    worker_pid = "123"

    def request(self, operation, params=None, timeout=30):
        if operation == "status":
            return {
                "version": "1.1.7",
                "sriov_ready": True,
                "totalvfs": 7,
                "numvfs": 0,
                "vfs": [],
                "vfs_in_use": [],
                "vm_details": {},
                "vms": {},
            }
        return {}


class ServerSecurityTest(unittest.TestCase):
    def test_listener_and_root_worker_are_separate(self):
        with open(os.path.join(ROOT, "app", "server", "sriovd.py"), encoding="utf-8") as handle:
            server = handle.read()
        with open(os.path.join(ROOT, "app", "server", "hw_worker.py"), encoding="utf-8") as handle:
            worker = handle.read()
        self.assertIn("socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)", server)
        self.assertIn('worker_path = os.path.join(HERE, "hw_worker.py")', server)
        self.assertIn("pass_fds=(child_worker.fileno(),)", server)
        self.assertIn("_drop_privileges(pw, group)", server)
        self.assertIn("path.startswith(\"/api/\") and not self._require_admin()", server)
        self.assertIn("ALLOWED_OPS = {\"status\", \"set_count\", \"vm_inventory\", \"inject\"}", worker)
        self.assertNotIn("sock.bind", worker)
        self.assertNotIn("sock.listen", worker)

    def test_static_path_rejects_nul_and_external_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui = os.path.join(tmp, "ui")
            os.makedirs(ui)
            with open(os.path.join(ui, "app.js"), "w", encoding="utf-8") as handle:
                handle.write("ok")
            outside = os.path.join(tmp, "secret.txt")
            with open(outside, "w", encoding="utf-8") as handle:
                handle.write("secret")
            os.symlink(outside, os.path.join(ui, "leak.txt"))

            self.assertEqual(sriovd.resolve_static_file(ui, "app.js"), os.path.realpath(os.path.join(ui, "app.js")))
            self.assertIsNone(sriovd.resolve_static_file(ui, "leak.txt"))
            self.assertIsNone(sriovd.resolve_static_file(ui, "bad\x00.txt"))
            self.assertIsNone(sriovd.resolve_static_file(ui, "../secret.txt"))

    def test_all_api_get_routes_require_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            ui = os.path.join(tmp, "ui")
            etc = os.path.join(tmp, "etc")
            var = os.path.join(tmp, "var")
            os.makedirs(ui)
            os.makedirs(etc)
            os.makedirs(var)
            with open(os.path.join(ui, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("<html>ok</html>")
            with open(os.path.join(etc, "config.json"), "w", encoding="utf-8") as handle:
                json.dump({"count": 1, "vm_aliases": {"uuid": "private"}}, handle)

            socket_path = os.path.join(tmp, "app.sock")
            uid = os.getuid()
            gid = os.getgid()
            listen_socket = sriovd._bind_socket(socket_path, uid, gid, False)
            server = sriovd.InheritedUnixHTTPServer(listen_socket, sriovd.Handler)
            server.ui_dir = ui
            server.config_path = os.path.join(etc, "config.json")
            server.gateway_prefix = "/app/sriov-vfctl"
            server.base_path = "/app/sriov-vfctl/"
            server.operation_lock = threading.Lock()
            server.worker = FakeWorker()
            server.log = lambda message: None
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.addCleanup(thread.join, 2)

            status, _headers, _body = self._request(socket_path, "GET", "/app/sriov-vfctl/api/config")
            self.assertIn("403", status)
            status, _headers, body = self._request(
                socket_path,
                "GET",
                "/app/sriov-vfctl/api/config",
                ["X-Trim-Isadmin: true"],
            )
            self.assertIn("200", status)
            self.assertTrue(body)
            status, _headers, _body = self._request(socket_path, "GET", "/app/sriov-vfctl/api/status")
            self.assertIn("403", status)
            status, _headers, _body = self._request(
                socket_path,
                "GET",
                "/app/sriov-vfctl/api/status",
                ["X-Trim-Isadmin: true"],
            )
            self.assertIn("200", status)

    @staticmethod
    def _request(socket_path, method, path, extra_headers=None):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(socket_path)
        lines = [
            "%s %s HTTP/1.1" % (method, path),
            "Host: localhost",
            "Content-Length: 0",
            "Connection: close",
        ]
        lines.extend(extra_headers or [])
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        data = b""
        while True:
            part = sock.recv(65536)
            if not part:
                break
            data += part
        sock.close()
        head, _, body = data.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("utf-8")
        return status, head, body


if __name__ == "__main__":
    unittest.main()
