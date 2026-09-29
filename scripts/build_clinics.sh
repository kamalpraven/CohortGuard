#!/usr/bin/env bash
# Build both clinic FABs into dist/. `flwr build` rejects underscores in the app
# directory name, so each app is staged under a hyphenated temp name first.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/sync_clinics.sh
mkdir -p dist
for c in a b; do
  stage="$(mktemp -d)/clinic-$c"
  cp -R "clinic_${c}_agent" "$stage"
  (cd "$stage" && flwr build && mv ./*.fab "$OLDPWD/dist/")
done
ls dist
