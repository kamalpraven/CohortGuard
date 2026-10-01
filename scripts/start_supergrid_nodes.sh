#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/runtime-logs"
SESSION="supergrid-demo"
KEY_DIR="$HOME/.cohortguard/keys"
SUPERLINK="fleet-supergrid.flower.ai:443"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION="${2:?--session requires a value}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/start_supergrid_nodes.sh [--session NAME]

Starts the three CohortGuard SuperNodes against Flower SuperGrid using ECDSA
private keys stored outside the repo in ~/.cohortguard/keys. The session name is
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

for role in clinic_a clinic_b research; do
  if [[ ! -f "$KEY_DIR/supergrid_${role}_ecdsa" ]]; then
    echo "Missing private key for $role: $KEY_DIR/supergrid_${role}_ecdsa" >&2
    echo "Generate/register keys first; do not store keys in the repo." >&2
    exit 2
  fi
done

mkdir -p "$LOG_DIR"
: > "$LOG_DIR/supergrid-pids.txt"
export PYTHONUTF8=1
export UV_LINK_MODE=copy

LEDGER_ROOT="C:/Users/kamal/.cohortguard"
CLINIC_A_LEDGER="$LEDGER_ROOT/clinic_a_budget_${SESSION}.json"
CLINIC_B_LEDGER="$LEDGER_ROOT/clinic_b_budget_${SESSION}.json"

echo "Starting SuperGrid CohortGuard nodes (session: $SESSION)"
echo "SuperLink Fleet API: $SUPERLINK"
echo "Clinic A ledger: $CLINIC_A_LEDGER"
echo "Clinic B ledger: $CLINIC_B_LEDGER"
# Fail closed: only this explicit step starts a session at zero spent. A readable
# ledger is continued; a missing or corrupted ledger of an existing session aborts.
"$ROOT/coordinator/.venv/Scripts/python.exe" "$ROOT/scripts/init_ledgers.py" "$CLINIC_A_LEDGER" "$CLINIC_B_LEDGER"
# Each SuperNode gets its own Flower home: Flower names a run's runtime environment by run ID
# and deletes it when a ClientApp exits, so nodes sharing one home break each other's runs.
mkdir -p "$LEDGER_ROOT/flwr-home/clinic_a" "$LEDGER_ROOT/flwr-home/clinic_b" "$LEDGER_ROOT/flwr-home/research"
echo "Logs: $LOG_DIR"

(
  cd "$ROOT"
  FLWR_HOME="$LEDGER_ROOT/flwr-home/clinic_a" coordinator/.venv/Scripts/flower-supernode.exe \
    --grpc-rere --superlink "$SUPERLINK" \
    --host 127.0.0.1 --port 9094 \
    --auth-supernode-private-key "$KEY_DIR/supergrid_clinic_a_ecdsa" \
    --allow-runtime-dependency-installation \
    --node-config 'role="clinic_a" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json" ledger-path="'"$CLINIC_A_LEDGER"'"' \
    > "$LOG_DIR/supergrid_clinic_a.log" 2> "$LOG_DIR/supergrid_clinic_a.err.log"
) & echo $! >> "$LOG_DIR/supergrid-pids.txt"

(
  cd "$ROOT"
  FLWR_HOME="$LEDGER_ROOT/flwr-home/clinic_b" coordinator/.venv/Scripts/flower-supernode.exe \
    --grpc-rere --superlink "$SUPERLINK" \
    --host 127.0.0.1 --port 9095 \
    --auth-supernode-private-key "$KEY_DIR/supergrid_clinic_b_ecdsa" \
    --allow-runtime-dependency-installation \
    --node-config 'role="clinic_b" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-b-agent/clinic_b/data/clinic_b_patients.json" ledger-path="'"$CLINIC_B_LEDGER"'"' \
    > "$LOG_DIR/supergrid_clinic_b.log" 2> "$LOG_DIR/supergrid_clinic_b.err.log"
) & echo $! >> "$LOG_DIR/supergrid-pids.txt"

(
  cd "$ROOT"
  FLWR_HOME="$LEDGER_ROOT/flwr-home/research" coordinator/.venv/Scripts/flower-supernode.exe \
    --grpc-rere --superlink "$SUPERLINK" \
    --host 127.0.0.1 --port 9096 \
    --auth-supernode-private-key "$KEY_DIR/supergrid_research_ecdsa" \
    --allow-runtime-dependency-installation \
    --node-config 'role="research"' \
    > "$LOG_DIR/supergrid_research.log" 2> "$LOG_DIR/supergrid_research.err.log"
) & echo $! >> "$LOG_DIR/supergrid-pids.txt"

echo "Started process group PIDs:"
cat "$LOG_DIR/supergrid-pids.txt"
echo "After startup, verify with: coordinator/.venv/Scripts/flwr.exe supernode list supergrid --verbose --format json"
