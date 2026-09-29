# CohortGuard — Research & Evidence (M3)

Research agent tooling: ClinicalTrials.gov + PubMed clients, candidate ranking,
grounding gate, eligibility-criteria structuring, and the injection fixture.
Pure Python (standard library only). No dependency on the Flower API, so it can be
tested now and called from the AgentApp once M1's template is running.

## Layout

| File | Purpose |
|---|---|
| `research/http_cache.py` | HTTP with `live` / `record` / `replay` modes. Fetcher is injectable (swap in Flower's `web_fetch` connector if direct HTTP is blocked in the runtime). |
| `research/sources.py` | ClinicalTrials.gov API v2 + PubMed E-utilities. Facts only; no LLM. |
| `research/candidates.py` | Builds candidate records in the Section 8 schema, ranks by phase / status / endpoint fit / papers. |
| `research/grounding.py` | Re-verifies every NCT ID, drug-trial pairing, phase, status and PMID. Logs each check for M4's harness. |
| `research/criteria.py` | Registry age/sex parsed in code; free-text criteria via LLM, then validated against `ALLOWLIST`. Off-allowlist items become `checkable: false`. |
| `research/injection.py` | Flags instruction-like text in fetched pages and wraps it as untrusted data. |
| `fixtures/injection_page.html` | Planted injection page. Host it on GitHub Pages for the attack suite. |
| `record_demo_cache.py` | Records the on-stage cache. |
| `tests/test_research.py` | 14 offline tests with a mocked registry (fake trial, wrong drug, wrong phase, fake PMID, cache replay, injection). |

```bash
python -m pytest -q tests/
```

## Wiring into the AgentApp

```python
from research.http_cache import Http
from research.candidates import build_candidates

http = Http(mode="replay")             # "record" while preparing, "replay" on stage
llm = lambda prompt: ...               # wrap the AgentSession model call; must return text
log = []                               # hand to M4 for the dashboard
cands = build_candidates(http, "type 2 diabetes", ["readmission"], llm=llm, log=log)
```

## Agreements needed with the team

- **M2:** `ALLOWLIST`, `DIAGNOSIS_CODES`, and `MEDICATION_CLASSES` in `shared/allowlist.py` must match the clinic query templates exactly (field names, ops, units: HbA1c in %, eGFR in mL/min/1.73m², BMI in kg/m², age in years).
- **M4:** grounding and injection events are appended to `log` as dicts with an `event` key.
- **M1:** confirm whether the AgentApp runtime allows direct outbound HTTP. If not, pass a fetcher that calls the web_fetch connector.

## Demo prep

1. Run `record_demo_cache.py` with internet access; choose 3–5 trials; hand-check and edit their `cache/criteria_*.json`.
2. Add one seeded fake candidate (e.g. `NCT99999999`) to the demo flow so the gate visibly strikes it.
3. Commit `cache/` so the demo works offline.

## Hardening changes (review pass)

- `trial_criteria` rejects anything but `NCT` + 8 digits (blocks path traversal into other files).
- Agent errors return `{"type": "error", ...}` JSON instead of crashing the run.
- Pipeline condition is lowercased and whitespace-normalized before the cache lookup.
- Grounding gate fails closed when a trial or paper can't be verified (e.g. missing from cache).
- Run config `demo_seed_fake=true` injects `NCT99999999`; the response's `blocked` list reports it.
- Run config `with_mechanism=false` skips per-candidate model calls.
- `record_demo_cache.py` never overwrites existing hand-checked criteria files (use `--force-criteria` to override) and records the seeded fake ID's lookup so replay returns the registry's real "not found".

Handoff notes for teammates are in `docs/`.

## Web page request (injection demo)

```json
{"type": "web_page", "url": "https://<user>.github.io/<repo>/injection_page.html"}
```

Fetches the page, keeps hidden text (where injections hide), logs `injection_detected`, and
summarizes the page wrapped as untrusted data. If the summary repeats injected instructions,
it is withheld and `injection_echoed` is logged. Without a model, flagged page text is never
passed on. Returns `{"type": "web_page", "url", "status", "summary", "injection_detected", "log"}`.

## Responsible use

CohortGuard is research and decision-support tooling, not a prescribing system. The research
agent surfaces public trial and literature evidence and eligibility checklists for discussion
between clinicians, patients and trial teams; clinicians make every decision. It never
recommends starting, stopping or changing a treatment, including to qualify for a trial.

All patient data in this project is synthetic. We make no claim of HIPAA or other regulatory
compliance. Our claims are narrower and tested: the research agent has no path to clinical
data, every trial and paper it reports is verified against ClinicalTrials.gov and PubMed
before release, and unverifiable evidence is dropped rather than shown.
