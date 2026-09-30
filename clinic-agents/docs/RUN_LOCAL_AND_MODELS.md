# Running the clinic agents on a local SuperLink, and choosing the model

The clinic agents only call a model when the prompt has no JSON request (natural
language). JSON requests from the coordinator never touch a model.

## Local SuperLink (from Flower's local-SuperLink guide)

```shell
# 1. model provider (read by SuperLink)
export FLWR_MODEL_API_KEY="<key>"
# optional custom Open Responses-compatible endpoint:
# export FLWR_MODEL_API_ENDPOINT="http://127.0.0.1:8080/v1/responses"

# 2. SuperLink (leave running)
cd clinic-b-agent && uv run flower-superlink --insecure

# 3. ~/.flwr/config.toml
# [superlink.local-agent]
# address = "127.0.0.1:8000"
# insecure = true

# 4. talk to the agent (plain `flwr chat` runs Flower's own default agent, not ours)
export FLWR_CHAT_SUPERLINK=local-agent
uv run flwr chat
# inside the chat: /load <path to clinic-b-agent or clinic-a-agent>, then paste:
# {"request_id":"q-1","template":"feasibility_count","nct_id":"NCT07060456"}
```

Run `python ../scripts/sync_apps.py` from `clinic-agents/` first so every app has the canonical shared code and criteria. This command is cross-platform; use `--check` to detect drift without writing.

## Choosing the model (Nebius / MiniMax)

The model ID is a run-config value. The default is `MiniMaxAI/MiniMax-M3` (Nebius Token Factory); the Flower starter used `openai/gpt-5.6-sol`:

```shell
uv run flwr run . local-agent -c model=openai/gpt-5.6-sol   # fall back to the starter's model
```

Nebius Token Factory lists MiniMax-M3 as `MiniMaxAI/MiniMax-M3`. Two things to
confirm with M1 or the Flower mentors, because the docs don't say:

1. The model ID string the Flower runtime accepts for Nebius (the starter uses a
   `provider/model` form).
2. Flower's `FLWR_MODEL_API_ENDPOINT` expects an Open Responses-compatible
   (`/v1/responses`) endpoint. Nebius's OpenAI-compatible API may only offer
   chat completions, in which case a gateway or the Flower-hosted models are needed.

On SuperGrid the provider is configured by the platform, not by these apps.

## What was verified

Tested with `flower-superlink --insecure` on this machine: the SuperLink started,
`/load` accepted clinic-b (`Loaded @flwrlabs/clinic-b`), and the run was created
and started. It then hung in the runtime's `uv sync` dependency install for
several minutes, so an end-to-end reply through the SuperLink was **not**
observed. Clinic logic and `main()` are covered by `pytest tests` with a fake session.
