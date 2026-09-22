#!/usr/bin/env bash
# 把自编的 fork 二进制叠成 hca-daemon:<tag>-fork。
#
#     bash deploy/eval/i2-fork-image.sh /path/to/atomcode
#
# 两段：
#   1. 先确保基础镜像 hca-daemon:<tag> 是最新的（官方二进制 + 两道 sha256）
#   2. 再用 deploy/Dockerfile.daemon-fork 叠一层，把二进制换掉 ——
#      这一层**自己带一道 sha256**，值由本脚本按传进来的文件现算并写进 pins.lock
#      的 [atomcode.fork] 段。校验一样是硬的，只是校验对象换成我们自己的产物。
set -euo pipefail
BIN="${1:?用法：i2-fork-image.sh <fork 二进制路径>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${HCA_IMAGE_TAG:-i2}"
[ -f "$BIN" ] || { echo "✗ 二进制不在：$BIN" >&2; exit 2; }

SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
SIZE="$(stat -c%s "$BIN")"
echo "[fork-image] 二进制 $BIN"
echo "[fork-image]   sha256=$SHA size=$SIZE"

# 构建上下文故意是一个只放二进制的小目录（见 Dockerfile.daemon-fork 的注释）
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
install -m 0755 "$BIN" "$CTX/atomcode"

cd "$REPO"
echo "[fork-image] 叠层 → hca-daemon:${TAG}-fork"
docker build -f deploy/Dockerfile.daemon-fork \
  --build-arg "BASE_IMAGE=hca-daemon:${TAG}" \
  --build-arg "FORK_BIN_SHA256=${SHA}" \
  -t "hca-daemon:${TAG}-fork" "$CTX"

echo "[fork-image] 版本回显：$(docker run --rm --entrypoint atomcode "hca-daemon:${TAG}-fork" --version)"
echo
echo "[fork-image] pins.lock 请记这一段："
cat <<PINS
[atomcode.fork]
base_version = "5.1.0"
base_tag     = "v5.1.0"
patch        = "docs/fork-patch/apply.py"
sha256       = "${SHA}"
size         = ${SIZE}
PINS
