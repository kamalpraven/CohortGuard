"""CohortGuard collaborative AgentApp for SuperLink and role-configured SuperNodes."""

from __future__ import annotations

import json
from typing import Any

from flwr.agentapp import AgentApp, AgentSession
from flwr.app import Context

from .discovery import discover_roles, parse_expected_mapping
from .grid import CoordinatorGrid, SUPERLINK_TOOLS, SUPERNODE_TOOLS, tool_names
from .identifiers import assert_deidentified
from .nodes import run_supernode
from .summary import write_final_summary
from .workflows import run_workflow, validate_coordinator_request

app = AgentApp()


def _emit_text(agent: AgentSession, text: str) -> None:
    agent.events.emit({"type": "response.output_text.delta", "delta": text})
    agent.events.emit({"type": "response.completed"})


def _error_text(code: str) -> str:
    return json.dumps({"type": "error", "error": code}, separators=(",", ":"))


def run_coordinator(agent: AgentSession, context: Context) -> None:
    """Validate input, execute a code-driven workflow, then summarize it."""
    try:
        raw_request: Any = json.loads(agent.prompt)
        request = validate_coordinator_request(raw_request)
    except (ValueError, TypeError, json.JSONDecodeError):
        _emit_text(agent, _error_text("request_rejected"))
        return

    try:
        timeout = float(context.run_config.get("grid_timeout", 45.0))
        grid = CoordinatorGrid(agent.grid, timeout)
        expected = parse_expected_mapping(context.run_config.get("expected_role_node_ids", "{}"))
        roles = discover_roles(grid, expected)
        result = run_workflow(grid, roles, request)
        # Node replies may contain public research names, but never strong identifiers.
        assert_deidentified(result, include_names=False)
        summary = write_final_summary(
            request,
            result,
            str(context.run_config.get("model", "")),
            str(context.run_config.get("reasoning_effort", "low")),
        )
        assert_deidentified(summary, include_names=False)
    except (ValueError, TypeError, KeyError, RuntimeError, TimeoutError, json.JSONDecodeError):
        _emit_text(agent, _error_text("workflow_failed"))
        return
    _emit_text(agent, summary)


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    """Dispatch by the Grid capabilities Flower exposes at this runtime role."""
    names = tool_names(agent.grid)
    if SUPERLINK_TOOLS <= names:
        run_coordinator(agent, context)
        return
    if names == SUPERNODE_TOOLS:
        run_supernode(agent, context)
        return
    _emit_text(agent, _error_text("unsupported_runtime"))
