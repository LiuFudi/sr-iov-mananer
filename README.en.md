# SR-IOV Manager

SR-IOV Manager is an Intel i915 SR-IOV virtual function manager for fnOS. It provides a Web interface and CLI for viewing driver status, adjusting VF counts, inspecting IOMMU groups and VM usage, and running controlled diagnostics and driver repair.

- Version: `1.1.7`
- Chinese name: `SR-IOV 管理`
- License: GNU GPL-3.0-or-later
- Author: <https://github.com/LiuFudi>

## Features

- Inspect i915 DKMS, SR-IOV readiness, VF devices, and VM usage.
- Change VF counts and persist the boot configuration.
- Set local display names for running virtual machines.
- Run controlled diagnostics and driver repair.
- Use the bundled CLI at `app/bin/sriov-vfctl`.

## Requirements

- fnOS 1.2.0800 or later.
- Intel integrated GPU at `0000:00:02.0`.
- A matching `i915-sriov-dkms` build for the running kernel.

## Build

```bash
./build-fpk.sh
```

The FPK and source archives are generated in `dist/`.

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Security

The privileged hardware worker has no network listener and accepts only a fixed operation allowlist. The page/API listener runs as the application user. See [SECURITY.md](SECURITY.md) for details.
