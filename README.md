---
tags: [agentapp]
dataset: []
framework: []
---

# Flower AgentApp

This minimal `AgentApp` uses the OpenAI SDK to send the current prompt and the
conversation's previous user and assistant messages through Flower Runtime. It
republishes every streamed response event to the frontend and prints the final
response text. Use it as a starting point for a custom Flower Agent.

Flower Runtime supplies the SDK base URL and task token, so the AgentApp does
not need provider credentials.

## Build

Install the project and build its Flower App Bundle (FAB):

```shell
uv sync
uv run flwr build
```

## Customize

Edit `agent/agent_app.py` to change the model or add your agent logic. The
current prompt is available as `agent.prompt`, and the run-series history is
available through `agent.events.get_trace()`.

## Learn more

See the [Flower Agent documentation](https://flower.ai/docs/agent/) for more
tutorials and guides.

## Clinic agents (M2)

```
clinic_a_agent/           in-house clinic: exact, local answers, nothing leaves
  clinic_a/  agent_app.py  clinic.py  data/clinic_a_patients.json
  pyproject.toml
clinic_b_agent/           outside clinic: aggregates via privacy gate for the coordinator
  clinic_b/  agent_app.py  clinic.py  privacy.py  data/clinic_b_patients.json
  pyproject.toml
clinic_core/              shared logic (criteria checker, templates, store, synthetic data generator)
shared/allowlist.py       M3's vocabulary (source of truth)
cache/                    M3's structured trial criteria
data/canaries.json        planted canaries for M4
scripts/  tests/
```

`./scripts/build_clinics.sh` builds both FABs; `pytest tests` runs the tests;
`python -m clinic_core.generate_data` regenerates the synthetic JSON.
Local run and model choice: [docs/RUN_LOCAL_AND_MODELS.md](docs/RUN_LOCAL_AND_MODELS.md). See [HANDOFF_M2_to_M1.md](HANDOFF_M2_to_M1.md) for the request/response contract.
