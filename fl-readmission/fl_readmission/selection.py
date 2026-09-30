"""Server-side node selection and reply validation: only clinic nodes ever get a task.

Three layers keep the research SuperNode (and any other node) out of training:
``ClinicOnlyGrid`` exposes only the pinned clinic node IDs to the strategy and refuses
any message addressed elsewhere; ``ReplyValidator`` checks every reply's source, role
claim and record structure; and the ClientApp itself refuses on non-clinic nodes.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable
from typing import Any

from flwr.app import Message
from flwr.serverapp import Grid

from .task import CLINIC_ROLES, NUM_FEATURES

ROLE_CONFIG_KEY = "meta"
ALLOWED_TRAIN_METRICS = {"num-examples", "train-loss"}
ALLOWED_EVALUATE_METRICS = {"num-examples", "test-loss", "auc"}
CONNECT_TIMEOUT = 60.0


class SelectionError(RuntimeError):
    """A node outside the clinic set was about to receive, or sent, a message."""


def parse_clinic_node_ids(value: Any) -> dict[int, str]:
    """Parse ``{"clinic_a": "<id>", "clinic_b": "<id>"}`` into ``{node_id: role}``.

    An empty mapping ("{}") returns {}; anything else must pin exactly both clinics.
    """
    mapping = json.loads(value) if isinstance(value, str) else value
    if not isinstance(mapping, dict):
        raise ValueError("clinic-node-ids must be a JSON object")
    if not mapping:
        return {}
    if set(mapping) != set(CLINIC_ROLES):
        raise ValueError("clinic-node-ids must pin exactly clinic_a and clinic_b")
    pinned = {int(str(node_id)): role for role, node_id in mapping.items()}
    if len(pinned) != len(CLINIC_ROLES):
        raise ValueError("clinic roles must be pinned to distinct node IDs")
    return pinned


class ClinicOnlyGrid(Grid):
    """Delegating Grid that only ever reports and addresses the allowed node IDs."""

    def __init__(self, grid: Grid, allowed: Iterable[int], connect_timeout: float = CONNECT_TIMEOUT) -> None:
        self._grid = grid
        self.connect_timeout = connect_timeout
        self.allowed = frozenset(int(node_id) for node_id in allowed)
        if not self.allowed:
            raise SelectionError("no clinic nodes selected")
        self.addressed: list[int] = []

    def set_run(self, run: Any) -> None:
        self._grid.set_run(run)

    @property
    def run(self) -> Any:
        return self._grid.run

    def create_message(self, content, message_type, dst_node_id, group_id, ttl=None):  # noqa: ANN001
        self._check_destination(dst_node_id)
        return self._grid.create_message(content, message_type, dst_node_id, group_id, ttl)

    def get_node_ids(self) -> Iterable[int]:
        deadline = time.monotonic() + self.connect_timeout
        while True:
            missing = self.allowed - {int(node_id) for node_id in self._grid.get_node_ids()}
            if not missing:
                return sorted(self.allowed)
            if time.monotonic() >= deadline:
                raise SelectionError(f"pinned clinic node(s) not connected: {sorted(missing)}")
            time.sleep(1.0)

    def push_messages(self, messages: Iterable[Message]) -> Iterable[str]:
        messages = list(messages)
        for message in messages:
            self._check_destination(message.metadata.dst_node_id)
        self.addressed += [int(m.metadata.dst_node_id) for m in messages]
        return self._grid.push_messages(messages)

    def pull_messages(self, message_ids: Iterable[str]) -> Iterable[Message]:
        return self._grid.pull_messages(message_ids)

    def send_and_receive(self, messages: Iterable[Message], *, timeout: float | None = None) -> Iterable[Message]:
        messages = list(messages)
        for message in messages:
            self._check_destination(message.metadata.dst_node_id)
        self.addressed += [int(m.metadata.dst_node_id) for m in messages]
        return self._grid.send_and_receive(messages, timeout=timeout)

    def _check_destination(self, node_id: int) -> None:
        if int(node_id) not in self.allowed:
            raise SelectionError(f"refusing to address non-clinic node {node_id}")


def select_clinic_nodes(
    grid: Grid, pinned: dict[int, str], *, simulation: bool, connect_timeout: float = CONNECT_TIMEOUT
) -> ClinicOnlyGrid:
    """Pinned IDs in deployment; in simulation (no research node) exactly two nodes."""
    if pinned:
        return ClinicOnlyGrid(grid, pinned, connect_timeout)
    if not simulation:
        raise SelectionError("clinic-node-ids must be pinned outside simulation")
    deadline = time.monotonic() + connect_timeout
    while len(node_ids := [int(node_id) for node_id in grid.get_node_ids()]) < len(CLINIC_ROLES):
        if time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    if len(node_ids) != len(CLINIC_ROLES):
        raise SelectionError(f"simulation expects exactly {len(CLINIC_ROLES)} nodes, found {len(node_ids)}")
    return ClinicOnlyGrid(grid, node_ids, connect_timeout)


class ReplyValidator:
    """Fail closed on any reply that is missing, erroneous, mis-sourced or off-schema.

    Also keeps a structural log of every reply (for the canary scan and the results).
    """

    def __init__(self, pinned: dict[int, str], expected_nodes: Iterable[int]) -> None:
        self.pinned = dict(pinned)
        self.expected_nodes = frozenset(int(n) for n in expected_nodes)
        self.roles: dict[int, str] = dict(pinned)
        self.log: list[dict[str, Any]] = []

    def validate(self, server_round: int, phase: str, replies: Iterable[Message]) -> list[Message]:
        replies = list(replies)
        sources = [int(reply.metadata.src_node_id) for reply in replies]
        if sorted(sources) != sorted(self.expected_nodes):
            raise SelectionError(f"round {server_round} {phase}: replies from {sorted(sources)}, "
                                 f"expected {sorted(self.expected_nodes)}")
        for reply, source in zip(replies, sources):
            if reply.has_error():
                raise SelectionError(f"round {server_round} {phase}: node {source} replied with an error")
            self.log.append(self._check_content(server_round, phase, source, reply))
        if set(self.roles.values()) != set(CLINIC_ROLES) or len(self.roles) != len(CLINIC_ROLES):
            raise SelectionError("replies must come from exactly clinic_a and clinic_b")
        return replies

    def _check_content(self, server_round: int, phase: str, source: int, reply: Message) -> dict[str, Any]:
        content = reply.content
        expected_arrays = {"arrays"} if phase == "train" else set()
        if set(content.array_records) != expected_arrays:
            raise SelectionError(f"unexpected array records {sorted(content.array_records)}")
        if set(content.metric_records) != {"metrics"} or set(content.config_records) != {ROLE_CONFIG_KEY}:
            raise SelectionError("unexpected metric or config records")
        if set(content.config_records[ROLE_CONFIG_KEY]) != {"role"}:
            raise SelectionError("unexpected config keys")
        role = content.config_records[ROLE_CONFIG_KEY]["role"]
        if role not in CLINIC_ROLES or self.roles.setdefault(source, role) != role:
            raise SelectionError(f"node {source} role claim {role!r} does not match its pinned role")
        metrics = dict(content.metric_records["metrics"])
        allowed = ALLOWED_TRAIN_METRICS if phase == "train" else ALLOWED_EVALUATE_METRICS
        if set(metrics) != allowed or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in metrics.values()):
            raise SelectionError(f"unexpected metrics {sorted(metrics)}")
        shapes: list[list[int]] = []
        if phase == "train":
            arrays = content.array_records["arrays"].to_numpy_ndarrays()
            shapes = [list(a.shape) for a in arrays]
            if shapes != [[NUM_FEATURES], [1]] or not all(math.isfinite(float(v)) for a in arrays for v in a.ravel()):
                raise SelectionError(f"unexpected array shapes {shapes}")
        return {"round": server_round, "phase": phase, "src_node_id": source, "role": role,
                "array_shapes": shapes, "metrics": metrics}
