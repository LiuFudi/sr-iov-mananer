#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 LiuFudi
"""生成 sriov-vfctl 的图标（不依赖任何图像库，纯 Python 写 PNG）。

构图：蓝紫渐变的圆角方章 + 白色芯片轮廓 + 芯片内 3 个方块（代表多个 VF）。
"""

import os
import struct
import zlib

TOP = (0x5B, 0x6B, 0xF5)
BOTTOM = (0x1D, 0x3A, 0xB8)
CHIP = (0xFF, 0xFF, 0xFF)
DIE = (0x2A, 0x33, 0xC9)

OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_png(path, size, pixels):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(pixels[y]) for y in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", header))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def rounded(x, y, hw, hh, r):
    """点 (x,y) 落在圆角矩形内则返回 True。"""
    dx = abs(x) - (hw - r)
    dy = abs(y) - (hh - r)
    if dx <= 0 or dy <= 0:
        return abs(x) <= hw and abs(y) <= hh
    return dx * dx + dy * dy <= r * r


def sample(x, y):
    """返回该点的 RGBA（0..255），坐标范围 [-1,1]。"""
    if not rounded(x, y, 0.99, 0.99, 0.20):
        return (0, 0, 0, 0)
    t = (y + 1.0) / 2.0
    bg = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))

    # 芯片外框
    if rounded(x, y, 0.44, 0.44, 0.07) and not rounded(x, y, 0.38, 0.38, 0.06):
        return CHIP + (255,)

    # 芯片内：3 个方块（VF）
    if rounded(x, y, 0.32, 0.32, 0.03):
        for i, cx in enumerate((-0.20, 0.0, 0.20)):
            if rounded(x - cx, y, 0.055, 0.13, 0.02):
                return DIE + (255,)
        # 方块之间留白，其余保持芯片底色
        return (235, 239, 255, 255)

    # 四边引脚
    pin_w, pin_h = 0.028, 0.075
    for k in (-0.24, 0.0, 0.24):
        if rounded(x - k, y + 0.47, pin_w, pin_h, 0.012) or \
                rounded(x - k, y - 0.47, pin_w, pin_h, 0.012) or \
                rounded(x + 0.47, y - k, pin_h, pin_w, 0.012) or \
                rounded(x - 0.47, y - k, pin_h, pin_w, 0.012):
            return CHIP + (255,)

    return bg + (255,)


def render(size):
    ss = 3
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    x = ((px + (sx + 0.5) / ss) / size) * 2.0 - 1.0
                    y = ((py + (sy + 0.5) / ss) / size) * 2.0 - 1.0
                    r, g, b, a = sample(x, y)
                    acc[0] += r
                    acc[1] += g
                    acc[2] += b
                    acc[3] += a
            n = ss * ss
            row.extend([int(acc[0] / n), int(acc[1] / n), int(acc[2] / n), int(acc[3] / n)])
        rows.append(row)
    return rows


def main():
    targets = [
        (os.path.join(OUT, "ICON.PNG"), 64),
        (os.path.join(OUT, "ICON_256.PNG"), 256),
        (os.path.join(OUT, "app", "ui", "images", "icon_64.png"), 64),
        (os.path.join(OUT, "app", "ui", "images", "icon_256.png"), 256),
    ]
    cache = {}
    for path, size in targets:
        if size not in cache:
            cache[size] = render(size)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_png(path, size, cache[size])
        print("wrote %s (%dx%d)" % (path, size, size))


if __name__ == "__main__":
    main()
