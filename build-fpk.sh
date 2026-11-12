#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 LiuFudi
#
# Build the fnOS package and source archives for sriov-vfctl.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-$HERE}"
PROJECT="$(cd "$PROJECT" && pwd)"
DIST="$HERE/dist"
ARCH="x86"
SYS="fn"

APPNAME="sriov-vfctl"
MANIFEST="$PROJECT/manifest"
SOURCE_ITEMS=(
    app
    cmd
    config
    tests
    tools
    manifest
    ICON.PNG
    ICON_256.PNG
    LICENSE
    README.md
    README.en.md
    CHANGELOG.md
    SECURITY.md
    NOTICE
    build-fpk.sh
)

fail() {
    echo "build-fpk: $*" >&2
    exit 1
}

manifest_value() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$MANIFEST" \
        | head -n1 | tr -d '\r' | sed 's/[[:space:]]*$//'
}

[ -f "$MANIFEST" ] || fail "缺少 $MANIFEST"
[ "$(manifest_value appname)" = "$APPNAME" ] || fail "manifest.appname 必须为 $APPNAME"
VERSION="$(manifest_value version)"
[ -n "$VERSION" ] || fail "manifest 缺少 version"

# fnpack 1.2.4 only accepts numeric test revisions. The release manifest keeps -rN.
FN_VERSION="$VERSION"
if [[ "$VERSION" =~ ^([0-9]+\.[0-9]+\.[0-9]+)-r([0-9]+)$ ]]; then
    FN_VERSION="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}"
fi

CODE_VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$PROJECT/app/server/sriovlib.py" | head -n1)"
CHANGELOG_VERSION="$(sed -n 's/^## \[\([^]]*\)\].*/\1/p' "$PROJECT/CHANGELOG.md" | head -n1)"
MANIFEST_CHANGELOG="$(manifest_value changelog)"

[ "$CODE_VERSION" = "$VERSION" ] || fail "代码版本=$CODE_VERSION，manifest=$VERSION"
[ "$CHANGELOG_VERSION" = "$VERSION" ] || fail "CHANGELOG 顶层=$CHANGELOG_VERSION，manifest=$VERSION"
case "$MANIFEST_CHANGELOG" in
    "$VERSION"*) ;;
    *) fail "manifest.changelog 未以版本号 $VERSION 开头" ;;
esac

for item in "${SOURCE_ITEMS[@]}"; do
    [ -e "$PROJECT/$item" ] || fail "缺少源码项：$item"
done
[ -s "$PROJECT/app/ui/LICENSE.txt" ] || fail "缺少随包许可证 app/ui/LICENSE.txt"
cmp -s "$PROJECT/LICENSE" "$PROJECT/app/ui/LICENSE.txt" || fail "LICENSE 与 app/ui/LICENSE.txt 不一致"

echo "build-fpk: 构建 $APPNAME $VERSION ($ARCH-$SYS)"

# Clean generated files and reject symlinks before fnpack reads the tree.
find "$PROJECT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PROJECT" -name '*.py[co]' -delete 2>/dev/null || true
SYMLINK="$(find "$PROJECT" -type l -print -quit)"
if [ -n "$SYMLINK" ]; then
    printf '%s\n' "$SYMLINK" >&2
    fail "源码树中存在软链接"
fi

# Refresh the compiled-in local donation QR module when external sources exist.
python3 "$PROJECT/tools/make_donate_qr.py" "$PROJECT" || fail "生成打赏二维码模块失败"
[ -s "$PROJECT/app/ui/donate-qr.js" ] || fail "缺少内嵌打赏二维码模块"
grep -q 'data:image/' "$PROJECT/app/ui/donate-qr.js" || fail "打赏二维码模块没有内嵌图片"
RAW_QR="$(find "$PROJECT/app/ui" -maxdepth 2 -type f \
    \( -iname '*wechat*' -o -iname '*alipay*' -o -name '微信.jpg' -o -name '支付宝.jpg' \) \
    -print -quit)"
if [ -n "$RAW_QR" ]; then
    fail "源码树中不允许出现独立收款码图片"
fi

# Normalize source permissions before packaging.
find "$PROJECT" -type d -exec chmod 755 {} +
find "$PROJECT" -type f -exec chmod 644 {} +
chmod 755 "$PROJECT/build-fpk.sh" "$PROJECT/cmd"/* "$PROJECT/app/bin"/* "$PROJECT/tools"/*.py

mkdir -p "$DIST"
find "$DIST" -maxdepth 1 -type f -name "${APPNAME}-*" -delete
rm -f "$PROJECT/${APPNAME}.fpk"

MANIFEST_BACKUP=""
restore_manifest() {
    if [ -n "$MANIFEST_BACKUP" ] && [ -f "$MANIFEST_BACKUP" ]; then
        cp -f "$MANIFEST_BACKUP" "$MANIFEST"
        rm -f "$MANIFEST_BACKUP"
    fi
}
trap restore_manifest EXIT

if [ "$FN_VERSION" != "$VERSION" ]; then
    MANIFEST_BACKUP="$(mktemp)"
    cp -f "$MANIFEST" "$MANIFEST_BACKUP"
    sed -i "s/^version[[:space:]]*=.*/version               = $FN_VERSION/" "$MANIFEST"
fi
( cd "$PROJECT" && fnpack build --directory . )
restore_manifest
MANIFEST_BACKUP=""

BUILT="$PROJECT/${APPNAME}.fpk"
[ -f "$BUILT" ] || fail "fnpack 没有产出 $BUILT"

# fnpack may rewrite permissions and checksum. Normalize both archive layers.
REPACK_DIR="$(mktemp -d)"
mkdir -p "$REPACK_DIR/outer" "$REPACK_DIR/app"
# fnOS turns a top-level LICENSE into a blocking install agreement. Keep the
# license in the source tree and app.tgz, but omit the outer wrapper copy.
mapfile -t OUTER_ENTRIES < <(tar tzf "$BUILT" | grep -v '^LICENSE$')
tar xzf "$BUILT" -C "$REPACK_DIR/outer"
find "$REPACK_DIR/outer/LICENSE" -maxdepth 0 -type f -delete 2>/dev/null || true

find "$REPACK_DIR/outer" -type d -exec chmod 755 {} +
find "$REPACK_DIR/outer" -type f -exec chmod 644 {} +
chmod 755 "$REPACK_DIR/outer/cmd"/*

mapfile -t APP_ENTRIES < <(tar tzf "$REPACK_DIR/outer/app.tgz")
tar xzf "$REPACK_DIR/outer/app.tgz" -C "$REPACK_DIR/app"
find "$REPACK_DIR/app" -type d -exec chmod 755 {} +
find "$REPACK_DIR/app" -type f -exec chmod 644 {} +
chmod 755 "$REPACK_DIR/app/bin"/*
[ -z "$(find "$REPACK_DIR/app" -type l -print -quit)" ] || fail "app.tgz 内存在软链接"

tar --no-recursion -czf "$REPACK_DIR/app.tgz.new" \
    -C "$REPACK_DIR/app" "${APP_ENTRIES[@]}"
mv -f "$REPACK_DIR/app.tgz.new" "$REPACK_DIR/outer/app.tgz"

APP_MD5="$(md5sum "$REPACK_DIR/outer/app.tgz" | awk '{print $1}')"
sed -i "s/^version[[:space:]]*=.*/version               = $VERSION/" "$REPACK_DIR/outer/manifest"
if grep -q '^checksum[[:space:]]*=' "$REPACK_DIR/outer/manifest"; then
    sed -i "s/^checksum[[:space:]]*=.*/checksum              = $APP_MD5/" "$REPACK_DIR/outer/manifest"
else
    printf 'checksum              = %s\n' "$APP_MD5" >> "$REPACK_DIR/outer/manifest"
fi
chmod 644 "$REPACK_DIR/outer/manifest"

tar --no-recursion -czf "$BUILT.repacked" \
    -C "$REPACK_DIR/outer" "${OUTER_ENTRIES[@]}"
mv -f "$BUILT.repacked" "$BUILT"
rm -rf "$REPACK_DIR"

ARTIFACT="$DIST/${APPNAME}-${VERSION}-${ARCH}-${SYS}.fpk"
mv -f "$BUILT" "$ARTIFACT"

# Reverse checks: version, checksum and duplicate entries.
PKG_VERSION="$(tar xOf "$ARTIFACT" manifest | sed -n 's/^version[[:space:]]*= *//p' | tr -d '\r')"
[ "$PKG_VERSION" = "$VERSION" ] || fail "包内版本=$PKG_VERSION 与 $VERSION 不一致"
PKG_CHECKSUM="$(tar xOf "$ARTIFACT" manifest | sed -n 's/^checksum[[:space:]]*= *//p' | tr -d '\r')"
APP_TGZ_MD5="$(tar xOf "$ARTIFACT" app.tgz | md5sum | awk '{print $1}')"
[ "$PKG_CHECKSUM" = "$APP_TGZ_MD5" ] || fail "manifest checksum 与 app.tgz 不一致"
[ -z "$(tar tzf "$ARTIFACT" | sort | uniq -d)" ] || fail "FPK 中存在重复条目"
if tar tzf "$ARTIFACT" | grep -qx 'LICENSE'; then
    fail "FPK 顶层不得包含 LICENSE，否则飞牛会显示安装协议页"
fi
[ -f "$ARTIFACT" ] || fail "FPK 产物不存在"

# Source archives are generated from a whitelist staged outside the source tree.
SOURCE_STAGE="$(mktemp -d)"
mkdir -p "$SOURCE_STAGE/$APPNAME"
for item in "${SOURCE_ITEMS[@]}"; do
    cp -a "$PROJECT/$item" "$SOURCE_STAGE/$APPNAME/"
done
tar -czf "$DIST/${APPNAME}-${VERSION}-source.tar.gz" \
    -C "$SOURCE_STAGE" "$APPNAME"
( cd "$SOURCE_STAGE" && zip -qr "$DIST/${APPNAME}-${VERSION}-source.zip" "$APPNAME" )
rm -rf "$SOURCE_STAGE"

# Ensure the source package can reproduce the packaged code.
SOURCE_CODE_VERSION="$(tar xOf "$DIST/${APPNAME}-${VERSION}-source.tar.gz" \
    "$APPNAME/app/server/sriovlib.py" \
    | sed -n "s/^VERSION = \"\\(.*\\)\"/\\1/p")"
[ "$SOURCE_CODE_VERSION" = "$VERSION" ] || fail "源码包代码版本=$SOURCE_CODE_VERSION，期望=$VERSION"
tar xOf "$ARTIFACT" app.tgz | tar xOz ui/LICENSE.txt \
    | cmp -s - "$PROJECT/LICENSE" || fail "包内 LICENSE.txt 与源码 LICENSE 不一致"

chmod 644 "$ARTIFACT" \
    "$DIST/${APPNAME}-${VERSION}-source.tar.gz" \
    "$DIST/${APPNAME}-${VERSION}-source.zip"
(
    cd "$DIST"
    sha256sum \
        "${APPNAME}-${VERSION}-${ARCH}-${SYS}.fpk" \
        "${APPNAME}-${VERSION}-source.tar.gz" \
        "${APPNAME}-${VERSION}-source.zip" > SHA256SUMS
)

echo
echo "build-fpk: 产物"
ls -lh "$DIST/${APPNAME}-${VERSION}"*
echo
echo "build-fpk: SHA-256"
cat "$DIST/SHA256SUMS"
echo
echo "build-fpk: checksum"
echo "  app.tgz  $APP_TGZ_MD5"
echo "  manifest $PKG_CHECKSUM"
echo
echo "build-fpk: FPK 权限"
tar -tvf "$ARTIFACT" | sed -n '1,20p'
