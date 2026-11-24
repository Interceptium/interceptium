#!/usr/bin/env sh
# Builds the Interceptium executable into dist/
set -e
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests
python tools/build.py
echo "Done: see dist/"
