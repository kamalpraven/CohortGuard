"""Typed wrappers around Flower 1.39 AgentGrid function-call APIs."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from flwr.agentapp import AgentGrid

SUPERLINK_TOOLS = {"get_nodes", "push_messages", "pull_messages"}
SUPERNODE_TOOLS = {"push_reply_message"}
MAX_GRID_TIMEOUT = 60.0


def tool_names(grid: AgentGrid) -> set[str]:
    """Return the runtime-selected Grid tool names."""
    return {str(tool["name"]) for tool in grid.tools()}


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "call_id": f"cg_{uuid.uuid4().hex}",
        "arguments": json.dumps(arguments, separators=(",", ":")),
    }


def _call_output(grid: AgentGrid, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    item = grid.call(_tool_call(name, arguments))
    output = item.get("output")
    if not isinstance(output, str):
        raise RuntimeError("invalid_grid_output")
    parsed = json.loads(output)
    if not isinstance(parsed, dict):
        raise RuntimeError("invalid_grid_output")
    return parsed


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str | None
    location: str | None


class CoordinatorGrid:
    """Code-driven coordinator operations over AgentGrid."""

    def __init__(self, grid: AgentGrid, timeout: float) -> None:
        if not 0 < timeout <= MAX_GRID_TIMEOUT:
            raise ValueError("grid_timeout must be between 0 and 60 seconds")
        self.grid = grid
        self.timeout = timeout

    def get_nodes(self) -> list[Node]:
        output = _call_output(self.grid, "get_nodes", {"sample_size": None})
        nodes = output.get("nodes")
        if not isinstance(nodes, list):
            raise RuntimeError("invalid_node_list")
        return [
            Node(str(node["id"]), node.get("name"), node.get("location"))
            for node in nodes
            if isinstance(node, dict) and "id" in node
        ]

    def request_many(self, requests: dict[str, str]) -> dict[str, dict[str, Any]]:
        """Send one payload per node and return replies keyed by source node ID."""
        if not requests:
            return {}
        messages = [
            {"dst_node_id": node_id, "payload": payload, "reply_to_message_id": None}
            for node_id, payload in requests.items()
        ]
        pushed = _call_output(self.grid, "push_messages", {"messages": messages})
        results = pushed.get("results")
        if not isinstance(results, list) or len(results) != len(messages):
            raise RuntimeError("invalid_push_result")
        message_ids: list[str] = []
        for result in results:
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if not isinstance(message_id, str) or not message_id:
                raise RuntimeError("message_rejected")
            message_ids.append(message_id)

        pulled = _call_output(
            self.grid,
            "pull_messages",
            {"message_ids": message_ids, "timeout": self.timeout},
        )
        if pulled.get("pending_message_ids"):
            raise TimeoutError("node_reply_timeout")
        replies = pulled.get("messages")
        if not isinstance(replies, list) or len(replies) != len(message_ids):
            raise RuntimeError("invalid_reply_count")
        output: dict[str, dict[str, Any]] = {}
        for reply in replies:
            if not isinstance(reply, dict) or reply.get("error") is not None:
                raise RuntimeError("node_reply_error")
            source = reply.get("src_node_id")
            payload = reply.get("payload")
            if not isinstance(source, str) or not isinstance(payload, str):
                raise RuntimeError("invalid_node_reply")
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                raise RuntimeError("invalid_node_reply")
            if source in output:
                raise RuntimeError("duplicate_node_reply")
            output[source] = parsed
        return output


def push_reply(grid: AgentGrid, payload: str) -> None:
    """Send exactly one reply for the current SuperNode instruction."""
    output = _call_output(grid, "push_reply_message", {"payload": payload})
    if not output.get("message_id") or output.get("error") is not None:
        raise RuntimeError("reply_rejected")
