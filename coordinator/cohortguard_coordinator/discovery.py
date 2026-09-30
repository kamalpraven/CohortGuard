"""SuperNode role discovery and expected-mapping validation."""

from __future__ import annotations

import json
from typing import Any

from .grid import CoordinatorGrid
from .identifiers import safe_json

REQUIRED_ROLES = {"clinic_a", "clinic_b", "research"}


def parse_expected_mapping(value: Any) -> dict[str, str]:
    """Parse a role-to-node-ID mapping stored as a scalar run-config value."""
    if value in (None, "", "{}"):
        return {}
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("invalid_expected_role_mapping")
    mapping = {str(role): str(node_id) for role, node_id in parsed.items()}
    if set(mapping) != REQUIRED_ROLES or any(not node_id.isdigit() for node_id in mapping.values()):
        raise ValueError("invalid_expected_role_mapping")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("invalid_expected_role_mapping")
    return mapping


def discover_roles(grid: CoordinatorGrid, expected: dict[str, str]) -> dict[str, str]:
    """Ask every node for its local role and validate one node per required role."""
    nodes = grid.get_nodes()
    if len(nodes) < len(REQUIRED_ROLES):
        raise RuntimeError("insufficient_nodes")
    identify = safe_json({"type": "identify_role", "request_id": "role-discovery"})
    replies = grid.request_many({node.node_id: identify for node in nodes})
    discovered: dict[str, str] = {}
    for node_id, reply in replies.items():
        role = reply.get("role")
        if role not in REQUIRED_ROLES or reply.get("type") != "role_identity":
            raise RuntimeError("invalid_role_claim")
        if role in discovered:
            raise RuntimeError("duplicate_role")
        if expected and expected.get(role) != node_id:
            raise RuntimeError("role_node_mismatch")
        discovered[str(role)] = node_id
    if set(discovered) != REQUIRED_ROLES:
        raise RuntimeError("missing_role")
    if expected and discovered != expected:
        raise RuntimeError("role_node_mismatch")
    return discovered
