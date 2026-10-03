#!/bin/bash
# Build the prebuilt QEMU that "ppcosx setup" downloads on Linux, as a
# tarball: tools/build-linux-qemu.sh OUT.tar.xz
#
# Needs the build dependencies (see APT_DEPS in ppcosx) and the qemu/
# submodule checked out.  The GitHub workflow .github/workflows/
# qemu-linux.yml runs this on Ubuntu 24.04 (x86_64 and arm64) and
# attaches the result to the release "qemu-<first 12 of the qemu commit>".
#
# Layout: ppcosx-qemu/{bin/qemu-system-ppc, bin/qemu-img, share/pc-bios/,
# VERSION (the qemu commit)}.
set -euo pipefail

out=$(realpath -m "${1:?usage: $0 OUT.tar.xz}")
repo=$(cd "$(dirname "$0")/.." && pwd)
qemu="$repo/qemu"
build="$qemu/build-dist"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

[ -f "$qemu/configure" ] || { echo "qemu/ is not checked out" >&2; exit 1; }

# QEMU's configure wants a Python with distlib.
venv="$build/pyvenv"
rm -rf "$build"
mkdir -p "$build"
python3 -m venv "$venv"
"$venv/bin/python3" -m pip install -q distlib

# dtc:werror: see setup_build in ppcosx (the bundled dtc's own -Werror).
(cd "$build" && ../configure --python="$venv/bin/python3" \
    --target-list=ppc-softmmu --disable-docs --disable-sdl \
    --enable-gtk --enable-pa --enable-slirp --disable-werror -Doptimization=2 \
    -Ddtc:werror=false)
ninja -C "$build" -j "$(nproc)" qemu-system-ppc qemu-img

d="$stage/ppcosx-qemu"
mkdir -p "$d/bin" "$d/share/pc-bios"
cp "$build/qemu-system-ppc" "$build/qemu-img" "$d/bin/"
strip "$d/bin/"*
# ppcosx passes its own firmware first (-L firmware/...); these cover
# what QEMU itself looks up.
cp -r "$qemu/pc-bios/keymaps" "$d/share/pc-bios/"
for f in openbios-ppc qemu_vga.ndrv; do
    [ -f "$qemu/pc-bios/$f" ] && cp "$qemu/pc-bios/$f" "$d/share/pc-bios/"
done
git -C "$repo" rev-parse HEAD:qemu > "$d/VERSION" 2>/dev/null \
    || git -C "$qemu" rev-parse HEAD > "$d/VERSION"

tar -C "$stage" -cJf "$out" ppcosx-qemu
echo "wrote $out ($(du -h "$out" | cut -f1), qemu $(cat "$d/VERSION"))"
