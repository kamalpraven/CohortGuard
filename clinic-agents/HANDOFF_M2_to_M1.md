# M2 → M1 handoff: Clinic agents

From: M2 (Data & Privacy) · To: M1 (Platform & Coordinator)

Two separate Flower AgentApps. Clinic B is the one the coordinator calls. Clinic A is in-house and returns exact patient-level answers to its own doctor only.

## Build

```shell
cd clinic-agents
./scripts/build_clinics.sh                # syncs shared code, builds both FABs into dist/
pytest tests                              # 19 tests
```

Apps live in `clinic-a-agent/` and `clinic-b-agent/` (hyphenated, because `flwr build` rejects underscores in the app folder name). Build them in place with `./scripts/sync_clinics.sh` then `flwr build` inside each folder.

Clinic A's patient JSON ships only in the clinic-a FAB. Clinic B's ships only in the clinic-b FAB.

## How to call

Put one JSON object in the AgentApp prompt (surrounding text is fine). The reply is a single JSON object, printed as the run's final text and also emitted as `response.output_text.delta` then `response.completed` events. If the prompt has no JSON, the agent asks the runtime model to build the request. Code validates it either way.

If no JSON request can be found, the reply is `{"clinic":"B","status":"rejected","reason":"no_valid_request"}`.

## Site feasibility: the function to call

`clinic_core/feasibility.py`:

```python
from clinic_core.feasibility import feasibility_count
feasibility_count(clinic, nct_id, criteria, patients, gate)   # clinic: "A" or "B"
```

`criteria` is the `criteria` list from M3's `research-agent/cache/criteria_<NCT>.json`; `patients` is the clinic's own patient list; `gate` is a `PrivacyGate` with that clinic's `BudgetLedger` (`clinic_core/privacy.py`). It returns:

```json
{"nct_id":"NCT07060456","clinic":"A","eligible_n":77,"unchecked_criteria":["..."],"status":"ok","noise_scale":2.0,"budget_remaining":0.9}
```

Both clinics answer the template `{"template":"feasibility_count","nct_id":"NCT07060456"}` through their AgentApp, so the coordinator can call either. Clinic A's count is gated exactly like B's (its own budget ledger, `~/.cohortguard/clinic_a_budget.json`, override `CLINIC_A_STATE`). A's exact in-house `feasibility_local` is unchanged and never leaves the clinic.

Local run, no Grid: `python scripts/run_feasibility.py` (uses throwaway ledgers).

## Clinic B templates

### `feasibility_count` (same template on Clinic A)

Request:

```json
{
  "request_id": "q-1",
  "template": "feasibility_count",
  "nct_id": "NCT07060456"
}
```

`criteria` (M3's structured list) is optional. If omitted, B loads M3's cached criteria for that `nct_id` (NCT07060456 and NCT07112339 are bundled).

Response (real output, seeded run):

```json
{
  "request_id": "q-1",
  "clinic": "B",
  "nct_id": "NCT07060456",
  "unchecked_criteria": [
    "Diagnosed with type 2 diabetes ≥ 90 days；",
    "... 7 more ..."
  ],
  "eligible_n": 83,
  "status": "ok",
  "noise_scale": 2.0,
  "budget_remaining": 0.9
}
```

`eligible_n` is noised. When suppressed it is `null` and `reason` is set. `unchecked_criteria` lists all 8 criteria the clinic cannot check for NCT07060456.

### `outcome_rate_by_cohort`

Request:

```json
{
  "request_id": "q-2",
  "template": "outcome_rate_by_cohort",
  "cohort_field": "medication",
  "cohorts": ["sglt2_inhibitor", "sulfonylurea"],
  "outcome": "readmit_30d",
  "filters": { "diagnosis": "T2D", "age_band": "50-59" }
}
```

- `cohorts` come from the medication classes in `shared/allowlist.py`.
- Allowed filters are `age_band` (10-year bands only), `diagnosis` (allowlist codes) and `sex` (`FEMALE` or `MALE`).
- A cohort is the patients currently on that medication class. Cohorts overlap, so a patient on both appears in both.

Response:

```json
{
  "request_id": "q-2",
  "clinic": "B",
  "status": "ok",
  "counter_offer": null,
  "reason": null,
  "results": [
    { "cohort": "sglt2_inhibitor", "n": 32, "events": 2, "rate_pct": 6.3 },
    { "cohort": "sulfonylurea", "n": 26, "events": 5, "rate_pct": 19.2 }
  ],
  "noise_scale": 2.0,
  "budget_remaining": 0.5,
  "definitions": { "readmit_30d": "unplanned return <= 30 days" }
}
```

`n` and `events` are noised, and `events` is capped at `n`. `rate_pct` is `events / n` in percent (1 decimal), computed from the noised counts, so it costs no extra budget. Pool with `n` and `events`, not by averaging `rate_pct`.

## Status values M1 must handle

| `status`        | When                                                                                                                             | Fields                                                               | What the coordinator should do                        |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------- |
| `ok`            | Released                                                                                                                         | Noised counts, `noise_scale`, `budget_remaining`                     | Pool it.                                              |
| `counter_offer` | `age_band` is not a 10-year band (e.g. `53-57`)                                                                                  | `counter_offer: {"age_band":"50-59"}`, `reason: "age_band_too_fine"` | Resend with the offered band.                         |
| `suppressed`    | Smallest cohort has fewer than 10 patients (`below_disclosure_threshold`), or the budget is used up (`privacy_budget_exhausted`) | `reason`, empty `results` or `eligible_n: null`                      | Treat as no data from B. Do not retry the same query. |
| `rejected`      | Unknown template, field or op outside the allowlist, bad filter, or more than 8 checkable criteria                               | `reason`                                                             | Fix the request. Don't loop on it.                    |

Example `counter_offer`, `suppressed` and `rejected` replies, from real runs:

```json
{"request_id":"q-3","clinic":"B","status":"counter_offer","results":[],"counter_offer":{"age_band":"50-59"},"reason":"age_band_too_fine"}
{"request_id":"q-4","clinic":"B","status":"suppressed","results":[],"reason":"below_disclosure_threshold"}
{"request_id":"q-5","clinic":"B","status":"rejected","reason":"unknown template 'select_all'"}
```

## Privacy settings

Values are in `clinic_core/privacy.py`.

| Setting           | Value                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------- |
| Minimum cell size | 10 (raw count below this is suppressed)                                                 |
| Noise             | Laplace, scale 2.0 per released count                                                   |
| Budget            | 5.0 total; 0.5 per count                                                                |
| Cost              | A feasibility query releases 1 count (0.5); a cohort query releases 2 counts per cohort |
| Ledger file       | `~/.cohortguard/clinic_b_budget.json`                                                   |

The ledger persists across runs. To reset it for a demo, delete that file or set `CLINIC_B_STATE` to a fresh path. The cohort example above spent 0.5 on `q-1` and 2.0 on `q-2`, which is why `budget_remaining` fell from 0.9 to 0.5.

## Clinic A (in-house)

Clinic A is for the Doctor agent inside Clinic A, not for the coordinator. Its replies carry `"scope":"in_house"` and are exact, with no noise.

- `patient_checklist` takes `mrn` or `name` plus `nct_id` (or `criteria`) and returns a met / not_met / unknown checklist. For Maria against NCT07060456 the summary is 5 met, 1 not met, 8 unknown.
- `feasibility_local` returns the exact eligible count and MRNs.
- `outcome_rate_by_cohort` returns exact `n`, `events` and `rate_pct`.

Decision for the team: the demo script says "Clinic A ≈ 30" from the coordinator. If A's count should reach the coordinator, A needs the same gate. Right now it does not send anything out.

## Synthetic data

- Clinic A has 524 patients and Clinic B has 644, using M3's vocabulary.
- Potentially eligible for NCT07060456: 77 in A, 84 in B.
- Clinic B skews older than A.
- The planted effect is that SGLT2 users have lower 30-day readmission than sulfonylurea users.
- Canaries and rare cases are in `data/canaries.json` (also needed by M4).
- Regenerate with `python -m clinic_core.generate_data`.

## Not yet tested

- Running on a real SuperLink or SuperGrid. Everything so far is local, with a fake AgentSession.
- The exact way the coordinator retrieves each agent's reply. The event shape is inferred from the template.
- The natural-language path through the runtime model.
