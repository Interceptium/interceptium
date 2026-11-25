#!/bin/sh
# Makes the source archives attached to each release, in dist/release/.
set -e
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -1)
OUT=dist/release
rm -rf "$OUT" && mkdir -p "$OUT"
COMMON="Cargo.toml Cargo.lock build.rs .cargo/config.toml src assets windows/app.manifest LICENSE README.md RELEASE_NOTES.md"
zip -qr -X "$OUT/Interceptium-$VERSION-source-windows.zip" $COMMON scripts/build-windows.bat
zip -qr -X "$OUT/Interceptium-$VERSION-source-linux.zip" $COMMON scripts/build-linux.sh
zip -qr -X "$OUT/Interceptium-$VERSION-source-macos.zip" $COMMON scripts/build-macos.sh
zip -qr -X "$OUT/Interceptium-Setup-$VERSION-source.zip" $COMMON scripts/build-setup.bat
ls -l "$OUT"
