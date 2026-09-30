# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CohortGuard is a Flower (`flwr`) AgentApp demo for privacy-preserving federated clinical-research coordination over **synthetic** data. A Clinic A-local Doctor Agent scrubs identifiers, a coordinator AgentApp routes deterministic workflows across three SuperNodes (`clinic_a`, `clinic_b`, `research`), and clinics release only noised, privacy-gated aggregates.

## Hard rules

- **Never regenerate or edit** synthetic data (clinic patient JSON), `clinic-agents/data/canaries.json` (the source of the canary hashes), any `cache/` files, the content of `criteria_*.json`, or the Maria fixture (`shared/test_fixtures/maria_profile.json`). Published results depend on them.
- **Never change privacy gate parameters** (min cell size, noise scale, budget, counter-offers) or existing publishers without explicit approval.
- **Ask before any SuperGrid run that makes model calls**, because it spends credits. Use a fresh `--session` for every demo run.
- **Never push, publish to Flower Hub, or force-push** without explicit approval. Commit in small steps, and merge to `main` locally with `--no-ff`.
- **Never print, commit or copy** API keys or SuperNode private keys.

## Commands

The dev environment is `coordinator/.venv`. Run `cd coordinator && uv sync` first if it doesn't exist. On Windows, set `PYTHONUTF8=1` for Flower commands and use **Git Bash** for the `scripts/*.sh` helpers. PowerShell mis-splits the quoted `--node-config` argument.

```shell
python scripts/sync_apps.py            # copy canonical shared code into app bundles (run after editing shared code)
python scripts/sync_apps.py --check    # fail if generated copies drifted

# Tests (all run with the coordinator venv)
PYTHON=coordinator/.venv/Scripts/python.exe
$PYTHON -m pytest -q coordinator/tests doctor-agent/tests
(cd clinic-agents && ../$PYTHON -m pytest -q tests)
(cd research-agent && ../$PYTHON -m pytest -q tests)
$PYTHON -m pytest -q coordinator/tests/test_coordinator.py -k <name>   # single test

cd coordinator && uv run flwr build    # build the coordinator FAB

python scripts/build_canary_hashes.py  # regenerate coordinator/cohortguard_coordinator/canary_hashes.json
# DO NOT RUN: changes all published results
# python -m clinic_core.generate_data  # (inside clinic-agents/) regenerates synthetic patients + canaries
```

End-to-end local demo (Git Bash):

```shell
scripts/start_local_grid.sh --session demo-main-<new>   # SuperLink + 3 SuperNodes; logs in runtime-logs/
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_local_demo.py --demo main   # must print "canary hits: 0"
scripts/stop_local_grid.sh
# then a second fresh session for: run_local_demo.py --demo sglt2_suppression   (sets defined in scripts/demo_plan.py)
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_clinic_attack_suite.py   # offline, seeded; also run by coordinator/tests/test_attack_suite.py
uv run --project coordinator python doctor-agent/app.py   # Tk Doctor Agent UI
```

The SuperGrid variant uses `scripts/start_supergrid_nodes.sh` and `scripts/run_supergrid_demo.py --expected-role-node-ids '{...}'`. See `coordinator/README.md`. SuperGrid CLI options depend on the CLI version, so check `--help` before changing commands.

## Architecture

### Canonical sources vs generated copies

Flower FABs must be self-contained, so shared code is **copied** into each app by `scripts/sync_apps.py`. Edit only the canonical sources:

| Canonical | Copied into |
|---|---|
| `shared/` (allowlist, `criteria/criteria_*.json`) | `coordinator/shared`, `clinic-agents/shared`, `research-agent/shared`, each clinic app, plus `cache/` / `trial_cache/` dirs |
| `clinic-agents/clinic_core/*.py` (except `generate_data.py`) | `coordinator/clinic_core`, `clinic-agents/clinic-{a,b}-agent/clinic_core` |
| `research-agent/research`, `research-agent/research_agent`, `research-agent/cache/*.json` | `coordinator/research`, `coordinator/research_agent`, `coordinator/cache` |

The copies are gitignored. After editing a canonical file, run `sync_apps.py` before testing or building. Otherwise the coordinator runs stale code.

### One coordinator AgentApp, two runtime modes

`coordinator/cohortguard_coordinator/agent_app.py` dispatches on the Grid tools Flower exposes:
- **SuperLink mode** (`run_coordinator`) parses the prompt as JSON and validates it (`workflows.validate_coordinator_request`: requires `"sanitized": true` and passes `identifiers.assert_deidentified`, which includes the salted canary-hash n-gram check). It then runs `discovery.discover_roles` (optionally pinned by `expected_role_node_ids`), `workflows.run_workflow` (`cohort_question` | `trial_pipeline` | `site_feasibility`), and finally `summary.write_final_summary`. Routing and pooling are plain code. The model, if `model` is set in run config, writes only the final summary. It never chooses Grid calls. `model = ""` returns the code-computed JSON.
- **SuperNode mode** (`nodes.run_supernode`) takes one role from `context.node_config` (`role`, `data-path`, `ledger-path`) and sends exactly one reply per instruction. Node handlers must not print or emit events.

Errors surface as `{"type":"error","error":"request_rejected"|"workflow_failed"|...}` rather than exceptions.

### Privacy gate (`clinic_core/privacy.py`)

Every count that leaves a clinic goes through `PrivacyGate.release`. It applies min cell size 10 (checked before and after noise), Laplace noise at ε=0.5 per count, and a persisted per-clinic `BudgetLedger` (total ε=5.0). Suppressions after noise still charge the budget. Ledgers are selected by `--session` and are **never** deleted or reset by scripts. A full four-workflow demo uses 4.5 of the 5.0 budget. Cohort pooling and CIs (`workflows._pool_cohorts`) must account for noise variance.

### Doctor Agent (`doctor-agent/`)

It runs locally at Clinic A. `doctor_agent/core.py` scrubs Clinic A names, MRNs, DOBs, canaries and generic date/ID/name patterns into `[REDACTED_IDENTIFIER]`, then builds a fixed coordinator request. `submit.py` submits it through the Flower Control API (`local-agent` connection in `~/.flwr/config.toml`). Exact single-patient answers are computed locally and never sent through Flower. Raw identifiers must never go into `flwr chat`, because Flower builds the AgentApp prompt before coordinator code runs.

### Research agent (`research-agent/research/`)

This is stdlib-only ClinicalTrials.gov and PubMed tooling. `http_cache.Http` supports `live`, `record` and `replay` modes (the coordinator default `http_mode = "replay"` uses the committed `cache/`). `grounding.py` re-verifies every NCT ID and PMID and fails closed. `injection.py` wraps fetched page text as untrusted. `with_mechanism=false` (the default) skips per-candidate model calls, and the research node strips web-page `log` fields before replying.

### Other directories

- `agent/` plus the root `pyproject.toml` is the original minimal `flwr new` template AgentApp. It is not part of the CohortGuard pipeline.
- `clinic-agents/clinic-{a,b}-agent/` are the earlier standalone clinic AgentApps. Their patient JSON is the node-local `data-path` for the SuperNodes.
- The `HANDOFF_*.md` files hold team contracts, e.g. the clinic request/response schema in `clinic-agents/HANDOFF_M2_to_M1.md`.

## Invariants to preserve

- Anything in a Grid payload or reply appears in SuperLink traces and logs. Treat it as public, and never put patient-level data, identifiers or keys there.
- The coordinator FAB must not include clinic patient JSON, `canaries.json`, or the Maria fixture. It ships only `canary_hashes.json` (see `fab-include` in `coordinator/pyproject.toml`).
- No model name is hard-coded in coordinator Python. Models come from run config, and provider keys come from `FLWR_MODEL_API_KEY` / `FLWR_MODEL_API_ENDPOINT` in the SuperLink environment. Never put keys in `node_config`, run config, or the FAB.
- `shared/allowlist.py` (`ALLOWLIST`, `DIAGNOSIS_CODES`, `MEDICATION_CLASSES`, `SEX_VALUES`) is the contract between research criteria structuring, clinic query templates and coordinator validation. Keep field names and units aligned across all three.
- The Grid pull timeout is capped at 60s (`grid.MAX_GRID_TIMEOUT`).
