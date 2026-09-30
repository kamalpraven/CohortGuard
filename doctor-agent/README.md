# CohortGuard Doctor Agent

A Clinic A-local desktop UI for doctor questions.

1. Loads Clinic A patient names, MRNs and DOBs locally.
2. Loads every planted canary locally.
3. Deterministically removes those values plus generic date, MRN, patient-ID and person-name patterns.
4. Converts only the scrubbed text into a fixed coordinator request.
5. Submits only that JSON request to the coordinator AgentApp.

If the question identifies one Clinic A patient and asks for an eligibility checklist, the Doctor Agent calls the exact local Clinic A handler and displays the result in its Tk window. It does not submit, print, log, or emit that result through Flower.

## Run

Start the local SuperLink and three SuperNodes using [the coordinator commands](../coordinator/README.md), then:

```shell
cd /c/Users/kamal/Documents/cohortguard
uv run --project coordinator python doctor-agent/app.py
```

The default Flower connection is `local-agent` from `%USERPROFILE%\.flwr\config.toml`.

## Important boundary

Do not paste raw identifiers into `flwr chat`. Flower creates the SuperLink AgentApp prompt before coordinator code runs. Raw doctor questions belong only in this local Doctor Agent UI.
