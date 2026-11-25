#!/bin/sh
# Build Interceptium on macOS.
# Requirements: Rust from https://rustup.rs and Apple's command line developer tools.
# The finished program is written to dist/Interceptium.
set -e
cd "$(dirname "$0")/.."
cargo build --release --locked
mkdir -p dist
cp target/release/interceptium dist/Interceptium
echo "Done: dist/Interceptium"
