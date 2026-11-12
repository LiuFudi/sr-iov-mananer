# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app", "server"))
import sriovlib  # noqa: E402


class SriovLibTest(unittest.TestCase):
    def test_sriov_ready_requires_capacity_not_just_parameter(self):
        with mock.patch.object(sriovlib, "pf_present", return_value=True), \
                mock.patch.object(sriovlib, "loaded_is_dkms", return_value=True), \
                mock.patch.object(sriovlib, "totalvfs", return_value=0):
            self.assertFalse(sriovlib.sriov_ready())
        with mock.patch.object(sriovlib, "pf_present", return_value=True), \
                mock.patch.object(sriovlib, "loaded_is_dkms", return_value=True), \
                mock.patch.object(sriovlib, "totalvfs", return_value=7):
            self.assertTrue(sriovlib.sriov_ready())

    def test_dkms_module_finds_compressed_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            kernel = "6.18-test"
            module_dir = os.path.join(tmp, kernel, "updates", "dkms", "i915-sriov")
            os.makedirs(module_dir)
            module = os.path.join(module_dir, "i915.ko.zst")
            with open(module, "wb") as f:
                f.write(b"test")
            with mock.patch.object(sriovlib, "MODULES_ROOT", tmp), \
                    mock.patch.object(sriovlib, "kernel_version", return_value=kernel):
                self.assertEqual(sriovlib.dkms_module(), module)

    def test_vf_devices_excludes_pf_without_physfn(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = os.path.join(tmp, sriovlib.PF_BDF)
            vf = os.path.join(tmp, "0000:00:02.1")
            os.makedirs(pf)
            os.makedirs(vf)
            os.symlink(pf, os.path.join(vf, "physfn"))
            with mock.patch.object(sriovlib, "VF_GLOB", os.path.join(tmp, "*")):
                devices = sriovlib.vf_devices()
            self.assertEqual([item["bdf"] for item in devices], ["0000:00:02.1"])

    def test_hostdev_bdfs_accepts_double_quotes_and_attribute_reordering(self):
        xml = """<domain><devices>
          <hostdev mode="subsystem" type="pci">
            <source><address function="0x1" slot="0x02" bus="0x00" domain="0x0000"/></source>
          </hostdev>
          <hostdev mode="subsystem" type="pci">
            <source><address domain="0x0000" bus="0x00" slot="0x02" function="0x3"/></source>
          </hostdev>
        </devices></domain>"""
        self.assertEqual(
            sriovlib.hostdev_bdfs(xml),
            ["0000:00:02.1", "0000:00:02.3"],
        )

    def test_vm_uuid_and_custom_label(self):
        xml = """<domain><name>xamv3zra</name><uuid>1234-test</uuid>
          <devices><hostdev><source><address domain="0x0000" bus="0x00"
          slot="0x02" function="0x1"/></source></hostdev></devices></domain>"""
        self.assertEqual(sriovlib.vm_domain_uuid(xml), "1234-test")
        status = {
            "vm_details": {
                "xamv3zra": {"name": "xamv3zra", "uuid": "1234-test", "bdfs": ["0000:00:02.1"]},
            },
            "vfs_in_use": [{"bdf": "0000:00:02.1", "vm": "xamv3zra"}],
        }
        sriovlib.decorate_vm_labels(status, {"vm_aliases": {"1234-test": "开发测试机"}})
        self.assertEqual(status["vm_labels"]["xamv3zra"], "开发测试机")
        self.assertEqual(status["vfs_in_use"][0]["vm_label"], "开发测试机")

    def _inject_with_state(self, initial_total, config_count, fail_rmmod=False):
        state = {"loaded": True, "total": initial_total, "max": 0}
        calls = []
        writes = []
        applied = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config_path = os.path.join(tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({"count": config_count}, f)

        def fake_run(cmd, timeout=180):
            calls.append((cmd, timeout))
            if cmd == ["rmmod", "xe"]:
                return 1, "not loaded"
            if cmd == ["rmmod", "i915"]:
                if fail_rmmod:
                    return 1, "Module i915 is in use"
                return 0, ""
            if cmd and cmd[:2] == ["modprobe", "i915"] and len(cmd) == 3:
                self.assertEqual(cmd[2], "max_vfs=7")
                state["total"] = 7
                state["max"] = 7
                return 0, ""
            if cmd == ["modprobe", "i915"]:
                return 0, ""
            self.fail("unexpected command: %r" % (cmd,))

        def fake_apply(_path):
            applied.append(_path)
            return True, "已设置为 %d 个 VF" % config_count

        patches = [
            mock.patch.object(sriovlib, "pf_present", return_value=True),
            mock.patch.object(sriovlib, "dkms_module", return_value="/tmp/i915.ko"),
            mock.patch.object(sriovlib, "kernel_version", return_value="6.18-test"),
            mock.patch.object(sriovlib, "driver_info", return_value={
                "kind": "dkms",
                "label": "DKMS 版（支持 SR-IOV）",
                "version": "test",
                "max_vfs": "0",
                "sriov_ready": False,
            }),
            mock.patch.object(sriovlib, "loaded_is_dkms", side_effect=lambda: state["loaded"]),
            mock.patch.object(sriovlib, "totalvfs", side_effect=lambda: state["total"]),
            mock.patch.object(sriovlib, "module_max_vfs", side_effect=lambda: state["max"]),
            mock.patch.object(sriovlib, "vf_devices", return_value=[]),
            mock.patch.object(sriovlib, "vfs_in_use", return_value=[]),
            mock.patch.object(sriovlib, "sys_write", side_effect=lambda path, value: writes.append((path, value))),
            mock.patch.object(sriovlib, "read", return_value="test"),
            mock.patch.object(sriovlib, "run", side_effect=fake_run),
            mock.patch.object(sriovlib, "apply_config", side_effect=fake_apply),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return sriovlib.inject(config_path=config_path), calls, writes, applied

    def test_inject_reloads_dkms_with_max_vfs_and_applies_config(self):
        (ok, lines), calls, writes, applied = self._inject_with_state(
            initial_total=0,
            config_count=2,
        )
        self.assertTrue(ok, lines)
        self.assertIn((["modprobe", "i915", "max_vfs=7"], 180), calls)
        self.assertTrue(any(path.endswith("/i915/unbind") for path, _ in writes))
        self.assertEqual(len(applied), 1)
        self.assertIn("SR-IOV 已激活：max_vfs=7，totalvfs=7", lines)

    def test_inject_skips_reload_when_ready_and_config_fits(self):
        (ok, lines), calls, _writes, applied = self._inject_with_state(
            initial_total=7,
            config_count=1,
        )
        self.assertTrue(ok, lines)
        self.assertFalse(any(cmd == ["rmmod", "i915"] for cmd, _ in calls))
        self.assertEqual(len(applied), 1)

    def test_inject_restores_binding_when_module_cannot_unload(self):
        (ok, lines), _calls, writes, applied = self._inject_with_state(
            initial_total=0,
            config_count=1,
            fail_rmmod=True,
        )
        self.assertFalse(ok)
        self.assertFalse(applied)
        self.assertTrue(any(path.endswith("/i915/bind") for path, _ in writes))
        self.assertIn("核显正被控制台/媒体服务占用", lines[-1])


if __name__ == "__main__":
    unittest.main()
