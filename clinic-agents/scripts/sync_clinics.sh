#!/usr/bin/env bash
# Vendor the shared code into each clinic app so `flwr build` can package it.
# Copies are gitignored; re-run after editing clinic_core/, shared/ or cache/.
set -euo pipefail
cd "$(dirname "$0")/.."
for c in clinic-a-agent clinic-b-agent; do
  dest="$c"
  rm -rf "$dest/clinic_core" "$dest/shared"
  mkdir -p "$dest/clinic_core/trial_cache" "$dest/shared"
  cp clinic_core/*.py "$dest/clinic_core/"
  cp cache/criteria_*.json "$dest/clinic_core/trial_cache/"
  cp shared/*.py "$dest/shared/"
done
echo "synced clinic_core + shared + trial cache into clinic-{a,b}-agent"
