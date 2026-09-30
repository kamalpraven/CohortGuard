#!/usr/bin/env bash
# Build both clinic FABs into dist/. The app directories are already hyphenated,
# so `flwr build` runs in place. Needs `flwr` on PATH (activate a venv with flwr).
set -euo pipefail
cd "$(dirname "$0")/.."
python ../scripts/sync_apps.py
mkdir -p dist
for c in clinic-a-agent clinic-b-agent; do
  (cd "$c" && flwr build && mv ./*.fab ../dist/)
done
ls dist
