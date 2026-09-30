#!/usr/bin/env bash
set -euo pipefail

if ! command -v taskkill >/dev/null 2>&1; then
  echo "This script is intended for Windows Git Bash." >&2
  exit 2
fi

taskkill //F //IM flower-superlink.exe //T >/dev/null 2>&1 || true
taskkill //F //IM flower-supernode.exe //T >/dev/null 2>&1 || true
taskkill //F //IM flower-superexec.exe //T >/dev/null 2>&1 || true

echo "Stopped local Flower SuperLink/SuperNode/SuperExec processes."
