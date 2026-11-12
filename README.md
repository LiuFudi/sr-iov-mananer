# SR-IOV 管理

面向飞牛 fnOS 的 Intel i915 SR-IOV 核显虚拟功能（VF）管理器，提供 Web 管理界面和 CLI。

- 英文名：`SR-IOV Manager`
- 中文名：`SR-IOV 管理`
- 版本：`1.1.7`
- 许可证：GNU GPL-3.0-or-later
- 作者主页：<https://github.com/LiuFudi>

## 项目简介

SR-IOV 管理是一款面向飞牛 fnOS 的 Intel 核显虚拟功能管理工具。它可查看 i915 DKMS 驱动与 SR-IOV 就绪状态，一键调整 VF 数量并持久化开机配置，检查 VF 的驱动绑定、IOMMU 分组和虚拟机占用，并提供诊断修复与命令行工具。适用于在 fnOS NAS 上运行需要 Intel 核显直通虚拟机的场景。

技术栈：Python 标准库 HTTP 守护进程、原生 HTML/CSS/JavaScript、Bash 生命周期脚本与 fnOS FPK 打包。当前正式版为 `1.1.7`。

## 功能

- 查看 i915 DKMS 驱动、SR-IOV 能力、VF 设备与虚拟机占用。
- 为运行中的虚拟机设置本地自定义名称。
- 调整 VF 数量并同步为开机配置。
- VF 与虚拟机列表支持分页，保持主区域高度稳定。
- 在 SR-IOV 未激活时执行诊断和驱动修复。
- 附带 CLI：`app/bin/sriov-vfctl`。

## 前置条件

- fnOS 1.2.0800 或更高版本。
- Intel 核显 `0000:00:02.0`。
- 与当前内核匹配的 `i915-sriov-dkms`。
- 启动入口以 `root` 创建受控 IPC；页面/API 监听进程随后降权到应用用户，读写 `/sys`、卸载/加载 i915 等操作只由独立 root worker 执行。

## 构建

```bash
./build-fpk.sh
```

产物位于 `dist/`。构建脚本会交叉校验版本、整理包内权限、重算 checksum，并生成源码包。

## 安装与使用

1. 安装与当前内核匹配的 `i915-sriov-dkms`。
2. 执行 `./build-fpk.sh` 生成 `sriov-vfctl-1.1.7-x86-fn.fpk`。
3. 通过飞牛应用中心安装 FPK，安装完成后从桌面入口打开管理页面。
4. 需要终端操作时可使用包内 `app/bin/sriov-vfctl`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 安全

漏洞报告方式和权限风险见 [SECURITY.md](SECURITY.md)。

## 打赏支持

应用顶栏有打赏入口，完全自愿，不影响任何功能。如果遇到BUG，欢迎前往 [GitHub](https://github.com/LiuFudi) 反馈或者添加 QQ 群：818299505。
