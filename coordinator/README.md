# CohortGuard Coordinator AgentApp

One Flower AgentApp runs in two modes selected from `agent.grid.tools()`:

- **SuperLink:** deterministic role discovery, workflow routing and pooling; an optional configured model writes only the final summary.
- **SuperNode:** one deterministic role from `context.node_config` (`clinic_a`, `clinic_b`, or `research`) and exactly one `push_reply_message` per instruction.

The app began from `flwr new @flwrlabs/collaborative-agent`. Unlike the template, the model never chooses or relays Grid calls.

## Privacy boundaries

- The coordinator accepts only a JSON object with `"sanitized": true`.
- It scans the entire request for MRNs, dates, patient/subject IDs, canary markers and person-name patterns before any model or Grid call.
- Clinic nodes read only their node-local `data-path` and return privacy-gated aggregates.
- Node handlers do not print or emit custom events.
- The research node defaults to `with_mechanism=false` and strips every web-page `log` field before replying.
- Grid calls and replies are visible in SuperLink traces and logs; payloads are therefore treated as public boundary values.
- Grid pull timeout is capped at 60 seconds (default 45), well below Flower's five-minute task limit.

Exact Clinic A patient answers are available only through the local [Doctor Agent](../doctor-agent/README.md).

## Prepare and test

From the repository root:

```shell
python scripts/sync_apps.py
python scripts/sync_apps.py --check
cd coordinator
uv sync
uv run pytest -q
uv run flwr build
```

The coordinator FAB contains shared clinic/research code and public replay cache files, but no clinic patient JSON, canaries, or Maria fixture.

## Local SuperLink plus three SuperNodes

On Windows, use **Git Bash** for the helper scripts and set `PYTHONUTF8=1` for Flower commands. PowerShell can split the quoted `--node-config` string incorrectly, causing SuperNode startup failures. Replace paths if the clone is elsewhere.

Recommended fresh demo session flow from the repository root:

```shell
# Session selects new ledger filenames only. Existing ledgers are never deleted or reset.
scripts/start_local_grid.sh --session demo-main-001
sleep 15
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_local_demo.py --demo main
scripts/stop_local_grid.sh

# Separate fresh session for the SGLT2 suppression example.
scripts/start_local_grid.sh --session demo-sglt2-001
sleep 15
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_local_demo.py --demo sglt2_suppression
scripts/stop_local_grid.sh
```

`--demo main` runs, in order, the primary cohort question (metformin vs basal insulin), the Maria identifier case (same comparison, so it shows redaction and a released result), the trial pipeline and site feasibility: 2.0 + 2.0 + 0.5 = 4.5 of 5.0 epsilon per clinic. `--demo sglt2_suppression` runs the SGLT2 vs sulfonylurea 50-59 comparison on a fresh budget, where it is suppressed because its event counts are too small. Both sets live in `scripts/demo_plan.py`, which checks each expected outcome. The script scans the streamed Flower run events plus `runtime-logs/*.log` for Maria's identifiers, prints `canary hits: 0` on success and exits nonzero on any hit or unexpected outcome.

The manual commands below were verified with Flower 1.39 on Windows using separate Git Bash terminals.

Add the Control API connection to `%USERPROFILE%\.flwr\config.toml`:

```toml
[superlink.local-agent]
address = "127.0.0.1:8000"
insecure = true
```

Before starting the clinic SuperNodes by hand, create (or continue) their session ledgers. Ledgers fail closed: a clinic node refuses every release if its ledger is missing, corrupted or unreadable, and a deleted session ledger is never recreated.

```shell
python scripts/init_ledgers.py C:/Users/kamal/.cohortguard/clinic_a_budget.json C:/Users/kamal/.cohortguard/clinic_b_budget.json
```

### Terminal 1 — SuperLink

```shell
cd /c/Users/kamal/Documents/cohortguard/coordinator
PYTHONUTF8=1 flower-superlink --insecure \
  --fleet-api-type grpc-rere \
  --fleet-api-address 127.0.0.1:9092 \
  --host 127.0.0.1 --port 8000
```

### Terminal 2 — Clinic A

```shell
flower-supernode --insecure --grpc-rere --superlink 127.0.0.1:9092 \
  --host 127.0.0.1 --port 9094 --allow-runtime-dependency-installation \
  --node-config 'role="clinic_a" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-a-agent/clinic_a/data/clinic_a_patients.json" ledger-path="C:/Users/kamal/.cohortguard/clinic_a_budget.json"'
```

### Terminal 3 — Clinic B

```shell
flower-supernode --insecure --grpc-rere --superlink 127.0.0.1:9092 \
  --host 127.0.0.1 --port 9095 --allow-runtime-dependency-installation \
  --node-config 'role="clinic_b" data-path="C:/Users/kamal/Documents/cohortguard/clinic-agents/clinic-b-agent/clinic_b/data/clinic_b_patients.json" ledger-path="C:/Users/kamal/.cohortguard/clinic_b_budget.json"'
```

### Terminal 4 — Research

```shell
flower-supernode --insecure --grpc-rere --superlink 127.0.0.1:9092 \
  --host 127.0.0.1 --port 9096 --allow-runtime-dependency-installation \
  --node-config 'role="research"'
```

### Terminal 5 — Doctor Agent

```shell
cd /c/Users/kamal/Documents/cohortguard
uv run --project coordinator python doctor-agent/app.py
```

The Doctor Agent builds/submits the local coordinator FAB using Flower's Control API. Raw doctor text remains in the Clinic A desktop process; only its scrubbed structured request becomes the AgentApp prompt.

## SuperGrid dry run

Use Flower 1.39 CLI. Check `--help` before changing commands because SuperGrid options are CLI-version sensitive.

Generate one ECDSA key pair per SuperNode role outside the repository:

```shell
mkdir -p ~/.cohortguard/keys
ssh-keygen -t ecdsa -b 384 -N "" -f ~/.cohortguard/keys/supergrid_clinic_a_ecdsa -C "cohortguard-clinic_a-supergrid"
ssh-keygen -t ecdsa -b 384 -N "" -f ~/.cohortguard/keys/supergrid_clinic_b_ecdsa -C "cohortguard-clinic_b-supergrid"
ssh-keygen -t ecdsa -b 384 -N "" -f ~/.cohortguard/keys/supergrid_research_ecdsa -C "cohortguard-research-supergrid"
```

Never commit or copy private keys into the repo. Register only public keys:

```shell
flwr supernode register ~/.cohortguard/keys/supergrid_clinic_a_ecdsa.pub supergrid --name cohortguard-clinic_a
flwr supernode register ~/.cohortguard/keys/supergrid_clinic_b_ecdsa.pub supergrid --name cohortguard-clinic_b
flwr supernode register ~/.cohortguard/keys/supergrid_research_ecdsa.pub supergrid --name cohortguard-research
flwr supernode list supergrid --verbose --format json
```

If the CLI asks for authentication, run `flwr login supergrid` yourself in the browser.

Start the three SuperGrid-connected nodes from Git Bash:

```shell
scripts/start_supergrid_nodes.sh --session supergrid-demo-001
sleep 30
flwr supernode list supergrid --verbose --format json
```

Run the no-model SuperGrid demo with role discovery pinned to the registered node IDs:

```shell
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_supergrid_demo.py \
  --expected-role-node-ids '{"clinic_a":"<clinic-a-node-id>","clinic_b":"<clinic-b-node-id>","research":"<research-node-id>"}'
```

To test the final-summary model path, pass `--model <model-id> --allow-model-calls`. This makes one model call per submitted workflow, so use sparingly. `with_mechanism` remains false.

Stop local SuperNode processes:

```shell
scripts/stop_local_grid.sh
```

## Canary hash boundary

The coordinator bundle includes salted SHA-256 hashes of normalized planted canary values, generated locally from `clinic-agents/data/canaries.json` by:

```shell
python scripts/build_canary_hashes.py
```

The coordinator does not ship `canaries.json` or plaintext canary identifiers. It hashes normalized 1-3 word n-grams from incoming requests and rejects matches before any model or Grid call. Known limitation: because the salt ships with the hash file, low-entropy values such as DOBs and MRNs could be brute-forced. This is a bundle hygiene and regression-test control, not a cryptographic privacy guarantee.

## Role-to-node-ID pinning

`expected_role_node_ids` is a JSON string in run config:

```toml
expected_role_node_ids = '{"clinic_a":"123","clinic_b":"456","research":"789"}'
```

Role discovery still runs. The coordinator rejects unknown/duplicate roles and any claim that does not match the expected node ID. Leave it as `"{}"` only when node IDs are intentionally dynamic during local development.

## Model configuration

No model name is hard-coded in Python. Set `model` in run config. An empty value returns the code-computed JSON result and is useful for offline local testing:

```toml
[tool.flwr.app.config]
model = ""
```

For a model-written final summary, override it with an accessible model ID and configure the model provider in the **SuperLink** environment before startup:

```shell
export FLWR_MODEL_API_KEY="<provider-key>"
# Optional Open Responses-compatible endpoint, including /v1/responses:
export FLWR_MODEL_API_ENDPOINT="http://127.0.0.1:8080/v1/responses"
```

### Self-hosted research SuperNode model key

The research role does not use a model by default (`with_mechanism=false`), so it needs no model key. If mechanism generation is explicitly enabled, set the provider variables in the shell/service environment that starts that self-hosted SuperNode so its runtime/model subprocess inherits them:

```shell
export FLWR_MODEL_API_KEY="<provider-key>"
export FLWR_MODEL_API_ENDPOINT="https://provider.example/v1/responses"  # optional
flower-supernode ... --node-config 'role="research"'
```

Never put provider keys in `node_config`, run config, the FAB, or Grid payloads.

## Workflow request examples

All examples must already be de-identified.

```json
{"sanitized":true,"request_id":"q-1","question":"Compare readmission cohorts.","workflow":"cohort_question","cohort_field":"medication","cohorts":["sglt2_inhibitor","sulfonylurea"],"outcome":"readmit_30d","filters":{"diagnosis":"T2D","age_band":"50-59"}}
```

```json
{"sanitized":true,"request_id":"q-2","question":"Find recruiting trials.","workflow":"trial_pipeline","condition":"type 2 diabetes","outcome_keywords":["readmission","hospitalization"]}
```

```json
{"sanitized":true,"request_id":"q-3","question":"Estimate site feasibility.","workflow":"site_feasibility","nct_id":"NCT07060456"}
```
