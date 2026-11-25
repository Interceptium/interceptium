#!/bin/sh
# Builds Interceptium for Linux. Needs Rust (https://rustup.rs).
# Output: dist/interceptium
set -e
cd "$(dirname "$0")/.."
cargo build --release --locked
mkdir -p dist
cp target/release/interceptium dist/interceptium
echo "Built dist/interceptium"
