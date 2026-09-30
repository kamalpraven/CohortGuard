"""Deterministic SuperNode role handlers."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from flwr.agentapp import AgentSession
from flwr.app import Context

from clinic_core.aggregate import handle_gated_aggregate
from clinic_core.privacy import BudgetLedger, PrivacyGate
from clinic_core.store import load_patients
from research_agent.agent_app import safe_handle_request

from .discovery import REQUIRED_ROLES
from .grid import push_reply

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def _error(role: str | None, code: str) -> dict[str, Any]:
    return {"type": "error", "role": role, "error": code}


def _parse_instruction(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = json.loads(prompt)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), str):
        raise ValueError("invalid_instruction")
    payload = json.loads(envelope["payload"])
    if not isinstance(payload, dict):
        raise ValueError("invalid_instruction")
    return envelope, payload


def _config_path(context: Context, key: str) -> Path:
    value = context.node_config.get(key, context.node_config.get(key.replace("-", "_")))
    if not isinstance(value, str) or not value:
        raise ValueError("missing_node_config")
    return Path(value)


def _clinic_response(role: str, request: dict[str, Any], context: Context) -> dict[str, Any]:
    clinic = "A" if role == "clinic_a" else "B"
    data_path = _config_path(context, "data-path")
    expected_name = f"clinic_{clinic.lower()}_patients.json"
    if data_path.name != expected_name:
        raise ValueError("wrong_clinic_data_path")
    patients = load_patients(clinic, data_path.parent)
    ledger_value = context.node_config.get("ledger-path", context.node_config.get("ledger_path"))
    ledger = (
        Path(str(ledger_value))
        if ledger_value
        else Path.home() / ".cohortguard" / f"clinic_{clinic.lower()}_budget.json"
    )
    gate = PrivacyGate(BudgetLedger(ledger), random.SystemRandom())
    return handle_gated_aggregate(request, patients, gate, clinic)


def _research_response(
    request: dict[str, Any], context: Context, agent: AgentSession
) -> dict[str, Any]:
    with_mechanism = bool(context.run_config.get("with_mechanism", False))
    run_config = {
        "http_mode": str(context.run_config.get("http_mode", "replay")),
        "with_mechanism": with_mechanism,
        "cache_dir": str(CACHE_DIR),
        "model": str(context.run_config.get("model", "")),
    }
    result = safe_handle_request(
        request,
        run_config=run_config,
        agent=agent if with_mechanism else None,
    )
    if result.get("type") == "web_page":
        return {
            "type": "web_page",
            "url": result.get("url"),
            "status": result.get("status"),
            "summary": result.get("summary"),
            "injection_detected": bool(result.get("injection_detected")),
        }
    return result


def run_supernode(agent: AgentSession, context: Context) -> None:
    """Handle one instruction and invoke ``push_reply_message`` exactly once."""
    role_value = context.node_config.get("role")
    role = str(role_value) if role_value is not None else None
    try:
        _envelope, request = _parse_instruction(agent.prompt)
        if role not in REQUIRED_ROLES:
            response = _error(role, "invalid_role")
        elif request.get("type") == "identify_role":
            response = {"type": "role_identity", "role": role}
        elif role in {"clinic_a", "clinic_b"}:
            response = _clinic_response(role, request, context)
        else:
            response = _research_response(request, context, agent)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        response = _error(role, "invalid_request")
    except Exception:
        response = _error(role, "internal_error")
    payload = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
    push_reply(agent.grid, payload)
