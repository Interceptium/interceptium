#!/bin/sh
# Builds Interceptium for macOS. Needs Rust (https://rustup.rs) and the Xcode command line tools.
# Output: dist/Interceptium
set -e
cd "$(dirname "$0")/.."
cargo build --release --locked
mkdir -p dist
cp target/release/interceptium dist/Interceptium
echo "Built dist/Interceptium"
