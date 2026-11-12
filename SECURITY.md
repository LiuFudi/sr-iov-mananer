# 安全说明

## 权限模型

安装入口以 `root` 启动并创建受控 IPC，随后页面/API 进程降权到应用专用用户。硬件操作由独立 root worker 执行，原因是：

- 读取 PCI 设备、IOMMU 与 SR-IOV 的 sysfs 状态。
- 写入 `sriov_numvfs` 并调整 PF/VF 驱动绑定。
- 卸载、加载 `i915` 模块。
- 安装和升级时维护 `/etc/modprobe.d/sriov-vfctl.conf`。

Web 服务仅通过 fnOS 统一网关使用的 Unix socket 暴露，socket 权限为 `0600` 且属主为页面/API 运行用户。所有 `/api/*` 请求均校验 `X-Trim-Isadmin: true`，缺失或为假时返回 403。

root worker 不监听 TCP 或文件系统 socket，只通过继承式 socketpair 接受固定白名单操作。静态文件路径经过 NUL、realpath 和 commonpath 校验。

## 风险

- 驱动注入会短暂中断核显输出。
- 调整 VF 数量会重建 VF，可能使已直通的虚拟机设备失效。
- 不应当把 PF `0000:00:02.0` 直通给虚拟机。

## 报告问题

请通过 <https://github.com/LiuFudi> 联系作者，并提供版本、内核、驱动版本和 `var/info.log`。不要在公开问题中附带令牌、密码或私人路径。
