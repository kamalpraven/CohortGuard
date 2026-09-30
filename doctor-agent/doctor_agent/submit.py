"""Submit de-identified Doctor Agent requests through Flower's Control API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flwr.cli.chat.chat_app import parse_task_event, start_chat_run
from flwr.cli.chat.chat_local_agent import build_local_agent
from flwr.cli.constant import CHAT_FAILURE_EVENTS, CHAT_TERMINAL_EVENTS, CHAT_TEXT_DELTA_EVENT
from flwr.cli.flower_config import read_superlink_connection
from flwr.cli.utils import init_http_client_from_connection
from flwr.proto.control_pb2 import StreamRunEventsRequest

from cohortguard_coordinator.identifiers import assert_deidentified


class FlowerCoordinatorSubmitter:
    """Build the local coordinator FAB, submit one prompt, and collect its reply."""

    def __init__(
        self,
        coordinator_path: Path,
        superlink: str = "local-agent",
        federation: str | None = None,
    ) -> None:
        self.coordinator_path = coordinator_path
        self.superlink = superlink
        self.federation = federation

    def submit(self, request: dict[str, Any]) -> str:
        assert_deidentified(request)
        prompt = json.dumps(request, separators=(",", ":"), ensure_ascii=False)
        local_agent = build_local_agent(self.coordinator_path)
        connection = read_superlink_connection(self.superlink)
        client = init_http_client_from_connection(connection)
        try:
            run_id, _series_id = start_chat_run(
                client,
                prompt,
                self.federation,
                None,
                local_agent.app_spec,
                local_agent.fab_hash,
                local_agent.fab_content,
            )
            deltas: list[str] = []
            for response in client.StreamRunEvents(StreamRunEventsRequest(run_id=run_id)):
                event_type, payload = parse_task_event(response.task_event)
                if event_type == CHAT_TEXT_DELTA_EVENT and isinstance(payload.get("delta"), str):
                    deltas.append(payload["delta"])
                if event_type in CHAT_FAILURE_EVENTS:
                    raise RuntimeError("Coordinator run failed.")
                if event_type in CHAT_TERMINAL_EVENTS:
                    break
            return "".join(deltas) or "Coordinator returned no summary."
        finally:
            client.close()
