#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/runtime-logs"
SESSION="default"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION="${2:?--session requires a value}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/start_local_grid.sh [--session NAME]

Starts a local SuperLink and three SuperNodes from Git Bash. The session name is
used only to choose ledger file paths. Existing ledgers are never deleted or reset.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! command -v tasklist >/dev/null 2>&1; then
  echo "This script is intended for Windows Git Bash." >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
: > "$LOG_DIR/pids.txt"
export PYTHONUTF8=1
export UV_LINK_MODE=copy

LEDGER_ROOT="C:/Users/kamal/.cohortguard"
CLINIC_A_LEDGER="$LEDGER_ROOT/clinic_a_budget_${SESSION}.json"
CLINIC_B_LEDGER="$LEDGER_ROOT/clinic_b_budget_${SESSION}.json"

echo "Starting local CohortGuard grid (session: $SESSION)"
echo "Clinic A ledger: $CLINIC_A_LEDGER"
echo "Clinic B ledger: $CLINIC_B_LEDGER"
echo "Logs: $LOG_DIR"

(
  cd "$ROOT/coordinator"
  PYTHONUTF8=1 .venv/Scripts/flower-superlink.exe \
    --insecure \
    --fleet-api-type grpc-rere \
    --fleet-api-address 127.0.0.1:9092 \
    --host 127.0.0.1 --port 8000 \
    > "$LOG_DIR/superlink.log" 2> "$LOG_DIR/superlink.err.log"
) & echo $! >> "$LOG_DIR/pids.txt"

sleep 5

(
  cd "$ROOT"
  coordinator/.venv/Scripts/flower-supernode.exe \
    --insecure --grpc-rere --superlink 127.0.0.1:9092 \
    --host 127.0.0.1 --port 9094 \
    --allow-runtime-dependency-installation \
    --node-config 'role="clinic_a" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json" ledger-path="'"$CLINIC_A_LEDGER"'"' \
    > "$LOG_DIR/clinic_a.log" 2> "$LOG_DIR/clinic_a.err.log"
) & echo $! >> "$LOG_DIR/pids.txt"

(
  cd "$ROOT"
  coordinator/.venv/Scripts/flower-supernode.exe \
    --insecure --grpc-rere --superlink 127.0.0.1:9092 \
    --host 127.0.0.1 --port 9095 \
    --allow-runtime-dependency-installation \
    --node-config 'role="clinic_b" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-b-agent/clinic_b/data/clinic_b_patients.json" ledger-path="'"$CLINIC_B_LEDGER"'"' \
    > "$LOG_DIR/clinic_b.log" 2> "$LOG_DIR/clinic_b.err.log"
) & echo $! >> "$LOG_DIR/pids.txt"

(
  cd "$ROOT"
  coordinator/.venv/Scripts/flower-supernode.exe \
    --insecure --grpc-rere --superlink 127.0.0.1:9092 \
    --host 127.0.0.1 --port 9096 \
    --allow-runtime-dependency-installation \
    --node-config 'role="research"' \
    > "$LOG_DIR/research.log" 2> "$LOG_DIR/research.err.log"
) & echo $! >> "$LOG_DIR/pids.txt"

echo "Started process group PIDs:"
cat "$LOG_DIR/pids.txt"
echo "Wait ~15 seconds, then run: PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_local_demo.py"
