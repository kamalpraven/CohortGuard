# Running the clinic agents locally

Clinic AgentApps accept **only a complete structured JSON object**. Free text,
prefixed JSON, and malformed JSON return a JSON error without calling a model.
This prevents names, MRNs, dates of birth, and other query text from crossing a
model boundary.

## Prepare generated app files

From `clinic-agents/`:

```shell
python ../scripts/sync_apps.py
python ../scripts/sync_apps.py --check
```

The sync command is cross-platform and vendors the canonical shared contract,
criteria, and clinic core into both app build directories.

## Local SuperLink

```shell
# 1. SuperLink (leave running)
cd clinic-b-agent
uv run flower-superlink --insecure

# 2. ~/.flwr/config.toml
# [superlink.local-agent]
# address = "127.0.0.1:8000"
# insecure = true

# 3. In another shell
export FLWR_CHAT_SUPERLINK=local-agent
uv run flwr chat
# /load <path to clinic-b-agent or clinic-a-agent>
# Then send a complete JSON object, for example:
# {"request_id":"q-1","template":"feasibility_count","nct_id":"NCT07060456"}
```

No model provider or model API key is used by a clinic deployment.

## Tests

Run the shared clinic suite in each app environment:

```shell
cd clinic-agents
uv run --project clinic-a-agent pytest -q tests
uv run --project clinic-b-agent pytest -q tests
```
