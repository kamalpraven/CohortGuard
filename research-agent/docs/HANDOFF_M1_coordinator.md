# M3 → M1 handoff: Research agent

From: M3 (Research & Evidence) · To: M1 (Platform & Coordinator)

## Files

| File | What it is |
|---|---|
| `research_agent/agent_app.py` | The research AgentApp |
| `pyproject.toml` | App config, dependencies, bundle includes |
| `research/` | API clients, ranking, grounding gate, criteria structuring |
| `shared/allowlist.py` | Shared fields, units and vocabularies (also used by M2) |
| `cache/` | Recorded API responses + hand-checked criteria for demo trials |
| `samples/candidates.json` | Example pipeline output |

## How to call the research agent

Send a JSON string as `agent.input`. Two request types:

**Pipeline** (what's in trials for a condition):
```json
{"type": "pipeline", "condition": "type 2 diabetes", "outcome_keywords": ["readmission"]}
```
Returns ranked, grounding-verified trial candidates plus the grounding log.

**Trial criteria** (structured eligibility for one trial, used for feasibility):
```json
{"type": "trial_criteria", "nct_id": "NCT07060456"}
```
Returns the hand-checked criteria list. Forward it unchanged to the clinic agents for feasibility counts.

**Web page** (literature context from a URL; also the prompt-injection demo):
```json
{"type": "web_page", "url": "https://..."}
```
Returns a summary with the page treated as untrusted data, plus injection flags.

### Response shapes

| Request | Response |
|---|---|
| `pipeline` | `{"type": "pipeline", "candidates": [...], "blocked": [...], "grounding_log": [...]}` |
| `trial_criteria` | `{"type": "trial_criteria", "trial": {...}, "criteria": [...]}` |
| `web_page` | `{"type": "web_page", "url": "...", "status": 200, "summary": "...", "injection_detected": true, "log": [...]}` |
| any failure | `{"type": "error", "error": "bad_request" \| "not_in_cache" \| "unknown_trial", "detail": "..."}` |

Errors come back as JSON; the run doesn't crash. `blocked` lists candidates the grounding gate
rejected (`nct_id`, `intervention`, `reasons`), for the doctor view's strike-through.
The condition is lowercased and whitespace-normalized, so "Type 2 Diabetes" hits the same cache.
In replay mode, only the recorded demo condition ("type 2 diabetes") works; anything else returns `not_in_cache`.

See `samples/candidates.json` for the exact candidate shape. Each candidate has:
`intervention`, `mechanism`, `trial` (`nct_id`, `title`, `phase`, `status`, `primary_endpoint`),
`evidence` (list of `pmid`, `title`, `journal`, `pubdate`), `evidence_level`, `relevance`, `score`, `verified`.

## Run config

| Key | Values | Default |
|---|---|---|
| `http_mode` (or `http.mode`) | `replay` / `record` / `live` | `replay` (served from `cache/`, no network) |
| `use_web_fetch` | `true` / `false` | `false`; `true` routes web access through Flower's web_fetch connector |
| `with_mechanism` | `true` / `false` | `true`; `false` skips the per-candidate model calls (faster, deterministic) |
| `demo_seed_fake` | `true` / `false` | `false`; `true` injects a fake model-suggested trial (`NCT99999999`) that the gate must block |
| `model` | model name | `openai/gpt-5.6-sol`; set to whatever model you assign |

Use `replay` for the demo.

## Model access

Model calls go through the OpenAI SDK using `FLWR_RUNTIME_BASE_URL` and `FLWR_RUNTIME_API_KEY`
from the runtime. The model only writes the one-line mechanism text; every fact
(IDs, phases, statuses, drug names, papers) comes from the registry.

## Status and open items

- Tested on a local SuperLink with a **stand-in model endpoint**. Not yet run on SuperGrid with a real model.
- `pyproject.toml` pins `flwr>=1.39.0`. If SuperGrid runs an older version, tell me and I'll loosen it.
- `publisher = "local"` in `pyproject.toml` must be changed to the Flower account username before publishing to Flower Hub.
- `with_mechanism=true` makes ~8 sequential model calls. For the live demo, consider `false` or check the latency first.
- Security boundary: this agent has no tool or code path that can reach clinic agents or clinical data.

## What I need from you

1. Which model my agent should use.
2. Confirmation the coordinator can call my agent over the Grid tools with the requests above.
3. Which Flower version SuperGrid runs.
