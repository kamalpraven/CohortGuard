"""ClientApp: each clinic trains and evaluates on its own records; only weights leave."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp

from .selection import ROLE_CONFIG_KEY
from .task import (
    CLINIC_ROLES,
    NUM_FEATURES,
    ClinicData,
    auc,
    load_clinic,
    log_loss,
    predict_proba,
    small_feature_mask,
    train,
)

app = ClientApp()


def resolve_data_source(node_config: dict[str, Any], run_config: dict[str, Any]) -> tuple[str, str]:
    """Return (role, patient file path) for this node, or refuse.

    Deployment: role and data-path come only from this SuperNode's own node_config, so
    the server can never point a clinic at another file. Simulation: partition 0 is
    Clinic A and 1 is Clinic B under run config ``sim-data-dir``; used only on nodes
    that have a partition-id and no role/data-path of their own.
    """
    if "data-path" in node_config or "role" in node_config:
        role = str(node_config.get("role", ""))
        if role not in CLINIC_ROLES or "data-path" not in node_config:
            raise PermissionError("this node is not a clinic node")
        return role, str(node_config["data-path"])
    if "partition-id" in node_config and run_config.get("simulation") is True:
        partition = int(node_config["partition-id"])
        base = str(run_config.get("sim-data-dir", ""))
        if partition not in range(len(CLINIC_ROLES)) or not base:
            raise PermissionError("simulation partition has no clinic data")
        role = CLINIC_ROLES[partition]
        letter = role[-1]
        return role, str(Path(base) / f"clinic-{letter}-agent" / f"clinic_{letter}" / "data" / f"{role}_patients.json")
    raise PermissionError("this node is not a clinic node")


def _clinic_data(context: Context) -> ClinicData:
    role, path = resolve_data_source(dict(context.node_config), dict(context.run_config))
    return load_clinic(path, role, float(context.run_config["test-fraction"]), int(context.run_config["split-seed"]))


def _weights(msg: Message) -> list[np.ndarray]:
    weights = msg.content["arrays"].to_numpy_ndarrays()
    if [list(w.shape) for w in weights] != [[NUM_FEATURES], [1]]:
        raise ValueError("unexpected model shape")
    return [w.astype(np.float64) for w in weights]


def _reply(msg: Message, data: ClinicData, metrics: dict[str, float], arrays: list[np.ndarray] | None = None) -> Message:
    records: dict[str, Any] = {"metrics": MetricRecord(metrics), ROLE_CONFIG_KEY: ConfigRecord({"role": data.role})}
    if arrays is not None:
        records["arrays"] = ArrayRecord(arrays)
    return Message(content=RecordDict(records), reply_to=msg)


@app.train()
def train_handler(msg: Message, context: Context) -> Message:
    data = _clinic_data(context)
    config = msg.content["config"]
    # Local minimum-cell rule: no update for rare binary features at this clinic.
    frozen = small_feature_mask(data.X_train)
    weights = train(
        _weights(msg), data.X_train, data.y_train,
        epochs=int(config["local-epochs"]),
        learning_rate=float(config["learning-rate"]),
        l2=float(config["l2"]),
        frozen=frozen,
    )
    metrics = {"num-examples": len(data.y_train), "train-loss": log_loss(data.y_train, predict_proba(weights, data.X_train))}
    return _reply(msg, data, metrics, weights)


@app.evaluate()
def evaluate_handler(msg: Message, context: Context) -> Message:
    data = _clinic_data(context)
    p = predict_proba(_weights(msg), data.X_test)
    metrics = {"num-examples": len(data.y_test), "test-loss": log_loss(data.y_test, p), "auc": auc(data.y_test, p)}
    return _reply(msg, data, metrics)
