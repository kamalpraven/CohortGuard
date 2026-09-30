# CohortGuard — Data & Privacy (M2): clinic agents

Two separate Flower AgentApps, each with its own synthetic patient JSON:

- **`clinic-a-agent/`** — in-house clinic. Exact, local answers for its own doctor (single-patient checklist, local feasibility). Nothing is aggregated or sent out.
- **`clinic-b-agent/`** — outside clinic. Reads its raw patient JSON, aggregates, applies the privacy gate (min cell 10, Laplace noise, persistent budget ledger, age-band counter-offers) and returns only the fixed response schema for the coordinator.

## Layout

```
clinic-agents/
  clinic-a-agent/   clinic_a/{agent_app,clinic}.py  data/clinic_a_patients.json  pyproject.toml
  clinic-b-agent/   clinic_b/{agent_app,clinic,privacy}.py  data/clinic_b_patients.json  pyproject.toml
  clinic_core/      shared logic: criteria checker, templates, store, agent glue, data generator
  shared/           generated from the top-level shared/ contract
  cache/            generated copies of canonical structured trial criteria
  data/canaries.json    planted canaries for M4
  docs/             RUN_LOCAL_AND_MODELS.md
  scripts/  tests/  HANDOFF_M2_to_M1.md
```

## Commands (run inside `clinic-agents/`)

```shell
python ../scripts/sync_apps.py        # cross-platform app vendoring
python ../scripts/sync_apps.py --check # fail if generated copies drift
python -m clinic_core.generate_data   # regenerate synthetic JSON + data/canaries.json
pytest tests                          # clinic tests
python scripts/run_feasibility.py     # site feasibility for both clinics, locally
./scripts/build_clinics.sh            # sync shared code, build both FABs into dist/
```

- Request/response contract for the coordinator: [HANDOFF_M2_to_M1.md](HANDOFF_M2_to_M1.md)
- Local SuperLink run and model choice (default `MiniMaxAI/MiniMax-M3`): [docs/RUN_LOCAL_AND_MODELS.md](docs/RUN_LOCAL_AND_MODELS.md)
