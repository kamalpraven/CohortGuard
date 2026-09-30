---
tags: [agentapp]
dataset: []
framework: []
---

# CohortGuard

CohortGuard is a Flower AgentApp demo for privacy-preserving, federated clinical-research coordination over synthetic data.

## Phase 2 architecture

- **Doctor Agent:** local desktop app on Clinic A's side. Raw doctor text stays local. The Doctor Agent scrubs Clinic A names, MRNs, DOBs, planted canaries, date/ID patterns, and person-name patterns before submitting a structured de-identified request.
- **Coordinator:** one AgentApp running at the SuperLink. It validates de-identification, discovers and pins SuperNode roles, routes workflows in code, pools aggregate results, and optionally asks a model to write only the final summary.
- **SuperNodes:** three role-configured nodes selected through `node_config` and discovered with `identify_role`:
  - `clinic_a`
  - `clinic_b`
  - `research`

Clinic nodes release only privacy-gated aggregates. Patient-level results are local-only in the Clinic A Doctor Agent.

## Phase 2 SuperGrid result

Final SuperGrid demo used model:

```text
flwrlabs/endeavor-1.0
```

Run IDs:

| Workflow | SuperGrid run ID |
|---|---:|
| Cohort question | `300229704695635665` |
| Trial pipeline | `9802931396676563660` |
| Site feasibility | `2808782358741742845` |
| Maria scrubbed cohort | `3547292561572019840` |

Canary scan result across streamed SuperGrid events, local logs, and model input/output boundaries:

```text
canary hits: 0
```

The SGLT2 inhibitor vs sulfonylurea cohort workflow recovered the planted synthetic effect direction: the sulfonylurea cohort had a higher noised 30-day readmission rate than the SGLT2 inhibitor cohort. This is an observational aggregate comparison over synthetic data, not a causal estimate.

## Local/SuperGrid helper scripts

Useful scripts:

```shell
scripts/start_local_grid.sh --session demo-001
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_local_demo.py
scripts/stop_local_grid.sh
```

For SuperGrid:

```shell
scripts/start_supergrid_nodes.sh --session supergrid-demo-001
PYTHONUTF8=1 coordinator/.venv/Scripts/python.exe scripts/run_supergrid_demo.py \
  --federation @praven1/cohortguard-phase2 \
  --expected-role-node-ids '{"clinic_a":"...","clinic_b":"...","research":"..."}'
scripts/stop_local_grid.sh
```

On Windows, use Git Bash for the start scripts. PowerShell can split Flower's quoted `--node-config` argument incorrectly. Use `PYTHONUTF8=1` for Flower commands to avoid Windows console encoding issues.

## Known limitations

- **Synthetic data only:** all clinical records are synthetic and intended for demo/testing.
- **No regulatory claim:** this is not a HIPAA/compliance product.
- **Salted canary hashes:** the coordinator ships salted SHA-256 hashes of planted canary values instead of plaintext canaries. Because the salt ships with the hashes, low-entropy values such as DOBs and MRNs could be brute-forced; this is bundle hygiene and regression protection, not a cryptographic privacy guarantee.
- **Budget per session:** privacy ledgers persist by session path. A full four-workflow demo spends 4.5 of 5.0 budget per clinic. Scripts create new ledger filenames with `--session`; they never auto-delete or reset ledgers.
- **Noisy aggregates:** clinic counts are privacy-gated/noised and must not be interpreted as exact patient counts.

## Project areas

- [coordinator/](coordinator/README.md): Phase 2 coordinator AgentApp and SuperNode role handlers.
- [doctor-agent/](doctor-agent/README.md): Clinic A-local Doctor Agent.
- [clinic-agents/](clinic-agents/README.md): synthetic clinic data, privacy gate, original clinic AgentApps.
- [research-agent/](research-agent/README.md): public evidence/trial pipeline and replay cache.
