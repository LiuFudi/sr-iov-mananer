# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 LiuFudi
# 作者主页: https://github.com/LiuFudi
#
# sriovlib.py —— Intel i915 SR-IOV 核显 VF 管理的核心逻辑。
# 供 sriovd.py（Web 守护进程）与 bin/sriov-vfctl（命令行）共用。
# 需要 root 权限（读写 /sys、virsh、rmmod）。

import glob
import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

VERSION = "1.1.7"
APP_NAME = "sriov-vfctl"

PF_BDF = "0000:00:02.0"
PF_DIR = "/sys/bus/pci/devices/" + PF_BDF
VF_GLOB = "/sys/bus/pci/devices/0000:00:02.*"
MODULES_ROOT = "/lib/modules"
DEFAULT_MAX_VFS = 7


# ---------------------------------------------------------------- 基础工具

def read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def sys_write(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value) + "\n")


def run(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "命令不存在: " + cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "命令超时: " + " ".join(cmd)


def kernel_version():
    try:
        with open("/proc/sys/kernel/osrelease", "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- 硬件状态

def pf_present():
    return os.path.isdir(PF_DIR)


def totalvfs():
    v = read(PF_DIR + "/sriov_totalvfs")
    return int(v) if v and v.isdigit() else 0


def numvfs():
    v = read(PF_DIR + "/sriov_numvfs")
    return int(v) if v and v.isdigit() else 0


def vf_devices():
    out = []
    for p in sorted(glob.glob(VF_GLOB)):
        bdf = os.path.basename(p)
        if bdf == PF_BDF:
            continue
        # Only PCI virtual functions expose a physfn link back to their PF.
        if not os.path.exists(p + "/physfn"):
            continue
        drv = ""
        if os.path.exists(p + "/driver"):
            drv = os.path.basename(os.readlink(p + "/driver"))
        grp = ""
        if os.path.exists(p + "/iommu_group"):
            grp = os.path.basename(os.readlink(p + "/iommu_group"))
        out.append({
            "bdf": bdf,
            "short": bdf.replace("0000:", ""),
            "driver": drv,
            "iommu_group": grp,
        })
    return out


def dkms_module():
    """当前内核对应的 DKMS 版 i915.ko；没有则返回 None。"""
    root = os.path.join(MODULES_ROOT, kernel_version(), "updates", "dkms")
    patterns = (
        "i915.ko",
        "i915.ko.*",
        "*/i915.ko",
        "*/i915.ko.*",
    )
    for pattern in patterns:
        for p in sorted(glob.glob(os.path.join(root, pattern))):
            if os.path.isfile(p):
                return p
    return None


def loaded_is_dkms():
    return os.path.exists("/sys/module/i915/parameters/max_vfs")


def module_max_vfs():
    v = read("/sys/module/i915/parameters/max_vfs", "")
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def sriov_ready():
    """DKMS 驱动已加载，且 PF 已实际暴露 SR-IOV 能力。"""
    return pf_present() and loaded_is_dkms() and totalvfs() > 0


def configurable_max_vfs():
    """驱动未就绪时仍允许预先保存的开机 VF 数量上限。"""
    return totalvfs() or DEFAULT_MAX_VFS


def driver_info():
    if loaded_is_dkms():
        return {
            "kind": "dkms",
            "label": "DKMS 版（支持 SR-IOV）",
            "version": read("/sys/module/i915/version", ""),
            "max_vfs": str(module_max_vfs()),
            "sriov_ready": totalvfs() > 0,
        }
    if os.path.isdir("/sys/module/i915"):
        return {
            "kind": "stock",
            "label": "内核自带版（无 SR-IOV）",
            "version": "",
            "max_vfs": "-",
            "sriov_ready": False,
        }
    return {
        "kind": "none",
        "label": "未加载",
        "version": "",
        "max_vfs": "-",
        "sriov_ready": False,
    }


def gpu_name():
    return read(PF_DIR + "/device", "?")


# ---------------------------------------------------------------- 虚拟机

def running_vms():
    if not shutil.which("virsh"):
        return []
    rc, out = run(["virsh", "list", "--name"])
    if rc != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def vm_hostdevs():
    """{vm: [bdf...]}，仅运行中的虚拟机。"""
    return {name: item["bdfs"] for name, item in vm_inventory().items()}


def vm_domain_uuid(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    return (root.findtext("./uuid") or "").strip()


def vm_inventory():
    """运行中虚拟机详情：域名、稳定 UUID 与直通 BDF。"""
    result = {}
    for vm in running_vms():
        rc, out = run(["virsh", "dumpxml", vm])
        if rc != 0:
            continue
        result[vm] = {
            "name": vm,
            "uuid": vm_domain_uuid(out),
            "bdfs": hostdev_bdfs(out),
        }
    return result


def hostdev_bdfs(xml_text):
    """从 libvirt XML 提取 PCI hostdev BDF，兼容属性顺序与引号变化。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    bdfs = []
    for hostdev in root.findall(".//hostdev"):
        address = hostdev.find("./source/address")
        if address is None:
            continue
        try:
            domain = int(address.get("domain", "0"), 0)
            bus = int(address.get("bus", "0"), 0)
            slot = int(address.get("slot", "0"), 0)
            function = int(address.get("function", "0"), 0)
        except (TypeError, ValueError):
            continue
        bdfs.append("%04x:%02x:%02x.%x" % (domain, bus, slot, function))
    return bdfs


def vfs_in_use(vf_bdfs=None, vms=None):
    """被运行中虚拟机占用的 VF。"""
    used = []
    if vf_bdfs is None:
        vf_bdfs = {v["bdf"] for v in vf_devices()}
    if vms is None:
        vms = vm_hostdevs()
    for vm, bdfs in vms.items():
        for b in bdfs:
            if b in vf_bdfs:
                used.append({"bdf": b, "short": b.replace("0000:", ""), "vm": vm})
    return used


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = {"count": 1, "vm_aliases": {}}


def load_config(path):
    fallback = {"count": 1, "vm_aliases": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return fallback
    cfg = {"count": 1, "vm_aliases": {}}
    cfg.update(data if isinstance(data, dict) else {})
    try:
        cfg["count"] = int(cfg.get("count", 1))
    except (TypeError, ValueError):
        cfg["count"] = 1
    aliases = cfg.get("vm_aliases")
    cleaned = {}
    if isinstance(aliases, dict):
        for key, value in aliases.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            key = key.strip()
            value = value.strip()
            if key and value and len(value) <= 40:
                cleaned[key] = value
    cfg["vm_aliases"] = cleaned
    return cfg


def save_config(path, cfg):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def decorate_vm_labels(data, cfg):
    """为运行中虚拟机添加自定义名称，并同步 VF 占用信息。"""
    aliases = (cfg or {}).get("vm_aliases", {})
    labels = {}
    details = data.get("vm_details", {})
    for name, info in details.items():
        uuid = info.get("uuid", "")
        label = aliases.get(uuid) or aliases.get("name:" + name) or name
        info["domain"] = name
        info["label"] = label
        labels[name] = label
    data["vm_labels"] = labels
    for item in data.get("vfs_in_use", []):
        item["vm_label"] = labels.get(item.get("vm"), item.get("vm", ""))
    return data


# ---------------------------------------------------------------- 核心操作

def status():
    vfs = vf_devices()
    details = vm_inventory()
    vms = {name: item["bdfs"] for name, item in details.items()}
    tv = totalvfs()
    drv = driver_info()
    used = vfs_in_use({v["bdf"] for v in vfs}, vms)
    for item in used:
        item["vm_uuid"] = details.get(item["vm"], {}).get("uuid", "")
    return {
        "version": VERSION,
        "kernel": kernel_version(),
        "gpu": gpu_name(),
        "driver": drv,
        "dkms_module": dkms_module(),
        "pf_present": pf_present(),
        "sriov_ready": drv["kind"] == "dkms" and tv > 0,
        "totalvfs": tv,
        "numvfs": numvfs(),
        "vfs": vfs,
        "vfs_in_use": used,
        "vms": vms,
        "vm_details": details,
        "running_vms": list(vms),
    }


def set_count(n, allow_in_use=False):
    """设置 VF 数量。返回 (ok, message)。"""
    if not pf_present():
        return False, "找不到核显 %s" % PF_BDF
    if not loaded_is_dkms():
        return False, "当前加载的不是 DKMS 版 i915（不支持 SR-IOV），请先执行注入修补"
    tv = totalvfs()
    if tv <= 0:
        return False, "硬件未暴露 SR-IOV 能力（sriov_totalvfs 缺失）"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False, "数量必须是整数"
    if n < 0 or n > tv:
        return False, "数量超出范围：0..%d" % tv
    cur = numvfs()
    if n == cur:
        return True, "当前已经是 %d 个 VF" % n
    used = vfs_in_use()
    if used and not allow_in_use:
        names = ", ".join("%s(%s)" % (u["short"], u["vm"]) for u in used)
        return False, "VF 正在被虚拟机使用（%s），请先关闭对应虚拟机" % names
    try:
        if cur > 0:
            sys_write(PF_DIR + "/sriov_numvfs", 0)
        sys_write(PF_DIR + "/sriov_numvfs", n)
    except OSError as exc:
        return False, "写入 sriov_numvfs 失败：%s" % exc
    time.sleep(0.3)
    if numvfs() != n:
        return False, "写入后状态异常（当前 %d）" % numvfs()
    return True, "已设置为 %d 个 VF" % n


def _bind_pf():
    if os.path.exists(PF_DIR + "/driver"):
        return
    try:
        sys_write("/sys/bus/pci/drivers/i915/bind", PF_BDF)
    except OSError:
        pass


def _recover_i915(log):
    """尽量把 i915 重新加载并绑定到 PF，保证失败后仍能正常显示。"""
    rc, out = run(["modprobe", "i915"])
    if rc != 0:
        log.append("恢复 i915 失败：%s" % out.strip())
        return False
    _bind_pf()
    return True


def inject(config_path=None, rebuild_initramfs=False):
    """热切换 DKMS 版 i915、激活 SR-IOV 并重建 VF；返回 (ok, 日志行列表)。"""
    log = []

    def say(msg):
        log.append(msg)

    if not pf_present():
        return False, ["找不到核显 %s" % PF_BDF]
    mod = dkms_module()
    configured = None
    if config_path:
        configured = load_config(config_path).get("count", 1)
    say("目标内核：%s" % kernel_version())
    say("DKMS 模块：%s" % (mod or "缺失"))
    say("当前驱动：%s" % driver_info()["label"])
    if configured is not None:
        say("开机 VF 配置：%d" % configured)
    if not mod:
        return False, log + ["当前内核没有 DKMS 版 i915，请先安装匹配内核的 i915-sriov-dkms"]

    current_total = totalvfs()
    current_max = module_max_vfs()
    configured_fits = configured is None or configured <= current_total
    if loaded_is_dkms() and current_total > 0 and configured_fits:
        say("SR-IOV 已就绪：max_vfs=%d，totalvfs=%d" % (current_max, current_total))
        if config_path:
            ok, message = apply_config(config_path)
            say(message)
            return ok, log
        return True, log
    if loaded_is_dkms():
        if current_total <= 0:
            say("DKMS 已加载但 max_vfs=%d，SR-IOV 尚未激活，需要重载驱动" % current_max)
        else:
            say("当前 totalvfs=%d，不足以承载开机配置 %d，需要重载驱动"
                % (current_total, configured))

    used = vfs_in_use()
    if used:
        names = ", ".join("%s(%s)" % (u["short"], u["vm"]) for u in used)
        return False, log + ["VF 正被虚拟机使用（%s），请先关闭对应虚拟机" % names]

    # 1) 解绑 PF 与仍绑在 i915 上的 VF
    bound_vfs = [v["bdf"] for v in vf_devices() if v["driver"] == "i915"]
    try:
        sys_write("/sys/bus/pci/drivers/i915/unbind", PF_BDF)
    except OSError as exc:
        say("解绑 PF 失败（可能本来就未绑定）：%s" % exc)
    for v in vf_devices():
        if v["driver"] == "i915":
            try:
                sys_write("/sys/bus/pci/drivers/i915/unbind", v["bdf"])
            except OSError:
                pass

    # 2) 卸载旧模块
    rc, out = run(["rmmod", "xe"])
    if rc != 0:
        say("rmmod xe：%s" % out.strip())
    rc, out = run(["rmmod", "i915"])
    if rc != 0:
        say("卸载 i915 失败：%s" % out.strip())
        for bdf in [PF_BDF] + bound_vfs:
            try:
                sys_write("/sys/bus/pci/drivers/i915/bind", bdf)
            except OSError:
                pass
        return False, log + ["核显正被控制台/媒体服务占用，无法热切换；请重启宿主（重启后会自动加载 DKMS 版）"]

    # 3) 显式传入 max_vfs 加载 DKMS 版，否则驱动的默认值可能是 0。
    rc, out = run(["modprobe", "i915", "max_vfs=%d" % DEFAULT_MAX_VFS])
    if rc != 0:
        say("modprobe i915 max_vfs=%d 失败：%s" % (DEFAULT_MAX_VFS, out.strip()))
        if _recover_i915(log):
            say("已恢复 i915，但 SR-IOV 未激活")
        return False, log + ["无法加载支持 SR-IOV 的 i915，请重启宿主后重试"]

    # 4) 重新绑定并重建 VF
    _bind_pf()
    for _ in range(25):
        if loaded_is_dkms() and totalvfs() > 0:
            break
        time.sleep(0.2)
    if not loaded_is_dkms() or totalvfs() <= 0:
        say("驱动加载后仍未暴露 SR-IOV：max_vfs=%s，totalvfs=%d"
            % (read("/sys/module/i915/parameters/max_vfs", "缺失"), totalvfs()))
        run(["rmmod", "i915"])
        if _recover_i915(log):
            say("已恢复 i915，但 SR-IOV 未激活")
        return False, log + ["DKMS 驱动未激活 SR-IOV，请重启宿主后重试"]

    say("已加载 DKMS 版 i915：%s" % read("/sys/module/i915/version", ""))
    say("SR-IOV 已激活：max_vfs=%d，totalvfs=%d"
        % (module_max_vfs(), totalvfs()))
    if config_path:
        ok, message = apply_config(config_path)
        say(message)
        if not ok:
            return False, log
    if rebuild_initramfs:
        rc, out = run(["update-initramfs", "-u", "-k", "all"], timeout=600)
        say("update-initramfs rc=%d" % rc)
    return True, log


def apply_config(config_path):
    """开机/启动时按配置创建 VF。返回 (ok, message)。"""
    cfg = load_config(config_path)
    n = cfg.get("count", 1)
    if not pf_present():
        return False, "找不到核显"
    if not sriov_ready():
        return False, "DKMS 版 i915 已加载，但 SR-IOV 未激活（max_vfs 可能为 0）"
    if numvfs() == n:
        return True, "已是目标数量 %d" % n
    return set_count(n)
