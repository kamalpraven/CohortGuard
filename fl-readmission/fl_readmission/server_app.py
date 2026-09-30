"""ServerApp: FedAvg over the two clinic nodes only, with fail-closed reply checks."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from flwr.app import ArrayRecord, ConfigRecord, Context, Message, MetricRecord
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

from .selection import ReplyValidator, parse_clinic_node_ids, select_clinic_nodes
from .task import FEATURE_NAMES, MIN_FEATURE_PATIENTS, NUMERIC_SCALING, initial_weights

RESULT_PREFIX = "FL_RESULT "

app = ServerApp()


class ClinicFedAvg(FedAvg):
    """FedAvg that aggregates only after every expected clinic replied validly."""

    def __init__(self, validator: ReplyValidator, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validator = validator

    def aggregate_train(self, server_round: int, replies: Iterable[Message]):
        return super().aggregate_train(server_round, self.validator.validate(server_round, "train", replies))

    def aggregate_evaluate(self, server_round: int, replies: Iterable[Message]) -> MetricRecord | None:
        return super().aggregate_evaluate(server_round, self.validator.validate(server_round, "evaluate", replies))


def _round_metrics(result: Any) -> list[dict[str, Any]]:
    rounds = []
    for server_round in sorted(result.evaluate_metrics_clientapp):
        evaluate = dict(result.evaluate_metrics_clientapp[server_round])
        train = dict(result.train_metrics_clientapp.get(server_round, {}))
        rounds.append({
            "round": server_round,
            "weighted_auc": evaluate["auc"],
            "weighted_test_loss": evaluate["test-loss"],
            "weighted_train_loss": train.get("train-loss"),
        })
    return rounds


@app.main()
def main(grid: Grid, context: Context) -> None:
    config = context.run_config
    pinned = parse_clinic_node_ids(config.get("clinic-node-ids", "{}"))
    clinic_grid = select_clinic_nodes(grid, pinned, simulation=config.get("simulation") is True)
    validator = ReplyValidator(pinned, clinic_grid.allowed)
    strategy = ClinicFedAvg(
        validator,
        fraction_train=1.0,
        fraction_evaluate=1.0,
        min_train_nodes=len(clinic_grid.allowed),
        min_evaluate_nodes=len(clinic_grid.allowed),
        min_available_nodes=len(clinic_grid.allowed),
    )
    train_config = ConfigRecord({
        "local-epochs": int(config["local-epochs"]),
        "learning-rate": float(config["learning-rate"]),
        "l2": float(config["l2"]),
    })
    result = strategy.start(
        grid=clinic_grid,
        initial_arrays=ArrayRecord(initial_weights()),
        num_rounds=int(config["num-server-rounds"]),
        train_config=train_config,
    )
    coef, intercept = result.arrays.to_numpy_ndarrays()
    payload = {
        "mode": "simulation" if not pinned else "deployment",
        "config": {key: config[key] for key in ("num-server-rounds", "local-epochs", "learning-rate", "l2", "split-seed", "test-fraction")},
        "scaling": {name: {"centre": centre, "scale": scale} for name, centre, scale in NUMERIC_SCALING},
        "min_feature_patients": MIN_FEATURE_PATIENTS,
        "feature_names": list(FEATURE_NAMES),
        "coefficients": [float(v) for v in coef],
        "intercept": float(intercept[0]),
        "rounds": _round_metrics(result),
        "clinic_node_roles": {str(node): role for node, role in sorted(validator.roles.items())},
        "addressed_node_ids": sorted({str(n) for n in clinic_grid.addressed}),
        "message_log": validator.log,
    }
    # One line, streamed back to the operator (also works when the ServerApp runs remotely).
    print(RESULT_PREFIX + json.dumps(payload, separators=(",", ":")), flush=True)
