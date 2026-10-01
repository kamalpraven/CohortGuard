"""Tests for the federated readmission app: data, scaling, min-cell rule, selection, results."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from flwr.app import ArrayRecord, ConfigRecord, Message, MetricRecord, RecordDict
from flwr.serverapp import Grid
from flwr.serverapp.strategy import FedAvg
from flwr.supercore.task_identity import TaskIdentity

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP))

from fl_readmission import client_app, evaluate  # noqa: E402
from fl_readmission.selection import (  # noqa: E402
    ClinicOnlyGrid,
    ReplyValidator,
    SelectionError,
    parse_clinic_node_ids,
    select_clinic_nodes,
)
from fl_readmission.task import (  # noqa: E402
    FEATURE_NAMES,
    MIN_FEATURE_PATIENTS,
    NUM_FEATURES,
    RECORD_FIELDS,
    auc,
    encode,
    encode_record,
    initial_weights,
    load_clinic,
    small_feature_mask,
    stratified_split,
    train,
)

DATA_DIR = ROOT / "clinic-agents"
PATHS = {role: evaluate.patient_file(DATA_DIR, role) for role in ("clinic_a", "clinic_b")}
A_ID, B_ID, RESEARCH_ID = 101, 202, 303
PINS = json.dumps({"clinic_a": str(A_ID), "clinic_b": str(B_ID)})
RUN_CONFIG = {"test-fraction": 0.2, "split-seed": 20260930}


def patients(role: str) -> list[dict]:
    return json.loads(PATHS[role].read_text(encoding="utf-8"))["patients"]


@pytest.fixture(autouse=True)
def task_identity():
    TaskIdentity.run_id, TaskIdentity.node_id, TaskIdentity.task_id = 1, 1, 1
    yield
    TaskIdentity.run_id = TaskIdentity.node_id = TaskIdentity.task_id = None


# ---------------------------------------------------------------------------
# Data loading never touches identifiers


class IdentifierTrap(dict):
    """A record that fails loudly if an identifier field is ever read."""

    def __getitem__(self, key):
        if key in {"mrn", "name", "dob"}:
            raise AssertionError(f"identifier field read: {key}")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in {"mrn", "name", "dob"}:
            raise AssertionError(f"identifier field read: {key}")
        return super().get(key, default)


def test_encoder_reads_only_allowlisted_fields():
    assert not {"mrn", "name", "dob"} & set(RECORD_FIELDS)
    maria = next(p for p in patients("clinic_a") if p["name"] == "Maria Delgado")
    trapped = IdentifierTrap(maria)
    assert encode_record(trapped) == encode_record({k: maria[k] for k in RECORD_FIELDS})


def test_identifier_fields_do_not_change_features():
    records = patients("clinic_b")[:50]
    stripped = [{k: r[k] for k in RECORD_FIELDS} for r in records]
    X1, y1 = encode(records)
    X2, y2 = encode(stripped)
    assert np.array_equal(X1, X2) and np.array_equal(y1, y2)
    assert X1.shape == (50, NUM_FEATURES) and len(FEATURE_NAMES) == NUM_FEATURES == 21
    assert not any(token in name for name in FEATURE_NAMES for token in ("mrn", "name", "dob"))


# ---------------------------------------------------------------------------
# Fixed scaling


def test_scaling_uses_fixed_constants():
    record = {"age": 80, "sex": "FEMALE", "hba1c": 10.0, "egfr": 55.0, "bmi": 36.0,
              "diagnoses": ["T2D"], "current_medications": ["sglt2_inhibitor"], "prior_medications": [],
              "readmitted_30d": False}
    row = dict(zip(FEATURE_NAMES, encode_record(record)))
    assert row["age"] == 1.0 and row["hba1c"] == 1.0 and row["egfr"] == -1.0 and row["bmi"] == 1.0
    assert row["sex_female"] == 1.0 and row["dx_T2D"] == 1.0 and row["current_sglt2_inhibitor"] == 1.0
    assert sum(row.values()) == (1.0 + 1.0 - 1.0 + 1.0) + 3.0  # numeric features + three indicators, all else 0


def test_scaling_does_not_depend_on_the_dataset():
    records = patients("clinic_a")
    X_all, _ = encode(records)
    for index in (0, 17, len(records) - 1):
        X_one, _ = encode([records[index]])
        assert np.array_equal(X_one[0], X_all[index])
    X_half, _ = encode(records[::2])
    assert np.array_equal(X_half, X_all[::2])


# ---------------------------------------------------------------------------
# Local split


def test_stratified_split_is_reproducible_and_keeps_class_ratio():
    _, y = encode(patients("clinic_b"))
    train_1, test_1 = stratified_split(y, 0.2, 7)
    train_2, test_2 = stratified_split(y, 0.2, 7)
    assert np.array_equal(train_1, train_2) and np.array_equal(test_1, test_2)
    assert not set(train_1) & set(test_1) and len(train_1) + len(test_1) == len(y)
    assert abs(y[test_1].mean() - y.mean()) < 0.01
    assert abs(len(test_1) / len(y) - 0.2) < 0.01
    _, test_3 = stratified_split(y, 0.2, 8)
    assert not np.array_equal(test_1, test_3)


# ---------------------------------------------------------------------------
# Minimum-cell rule for model updates


def test_small_binary_features_are_frozen_and_get_zero_update():
    rng = np.random.default_rng(0)
    X = np.zeros((200, NUM_FEATURES))
    X[:, 0] = rng.normal(size=200)                       # continuous: never frozen
    X[: MIN_FEATURE_PATIENTS - 1, 5] = 1.0               # 9 patients: frozen
    X[: MIN_FEATURE_PATIENTS, 6] = 1.0                   # 10 patients: trained
    y = (rng.random(200) < 0.3).astype(float)
    y[: MIN_FEATURE_PATIENTS] = 1.0
    mask = small_feature_mask(X)
    assert mask[5] and not mask[6] and not mask[0]
    start = [rng.normal(size=NUM_FEATURES), np.array([0.1])]
    trained = train(start, X, y, epochs=10, learning_rate=0.5, l2=1e-3, frozen=mask)
    assert trained[0][5] == start[0][5]
    assert trained[0][6] != start[0][6] and trained[0][0] != start[0][0]


def test_clinic_train_handler_sends_no_update_for_rare_features():
    data = load_clinic(str(PATHS["clinic_a"]), "clinic_a", 0.2, 20260930)
    frozen = np.flatnonzero(small_feature_mask(data.X_train))
    assert [FEATURE_NAMES[i] for i in frozen] == ["prior_metformin"]
    start = [np.full(NUM_FEATURES, 0.05), np.array([-1.5])]
    msg = Message(RecordDict({"arrays": ArrayRecord(start), "config": ConfigRecord(
        {"local-epochs": 10, "learning-rate": 1.0, "l2": 1e-3})}), dst_node_id=A_ID, message_type="train")
    context = SimpleNamespace(node_config={"role": "clinic_a", "data-path": str(PATHS["clinic_a"])}, run_config=RUN_CONFIG)
    reply = client_app.train_handler(msg, context)
    coef, _ = reply.content["arrays"].to_numpy_ndarrays()
    assert np.array_equal(coef[frozen], start[0][frozen])
    moved = np.setdiff1d(np.arange(NUM_FEATURES), frozen)
    assert np.all(coef[moved] != start[0][moved])
    assert set(reply.content.config_records) == {"meta"} and reply.content["meta"]["role"] == "clinic_a"


# ---------------------------------------------------------------------------
# ClientApp data source: clinics only, path only from node_config in deployment


def test_research_node_is_refused():
    with pytest.raises(PermissionError):
        client_app.resolve_data_source({"role": "research"}, {"simulation": True, "sim-data-dir": str(DATA_DIR)})


def test_deployment_path_comes_only_from_node_config():
    node_config = {"role": "clinic_b", "data-path": str(PATHS["clinic_b"]), "partition-id": 0}
    run_config = {"simulation": True, "sim-data-dir": "C:/elsewhere"}
    assert client_app.resolve_data_source(node_config, run_config) == ("clinic_b", str(PATHS["clinic_b"]))


def test_clinic_cannot_be_pointed_at_another_clinics_file():
    with pytest.raises(PermissionError):
        load_clinic(str(PATHS["clinic_b"]), "clinic_a", 0.2, 1)


def test_simulation_partitions_map_to_clinics_only_in_simulation():
    config = {"simulation": True, "sim-data-dir": str(DATA_DIR)}
    assert client_app.resolve_data_source({"partition-id": 0}, config) == ("clinic_a", str(PATHS["clinic_a"]))
    assert client_app.resolve_data_source({"partition-id": 1}, config)[0] == "clinic_b"
    for node_config, run_config in (({"partition-id": 2}, config), ({"partition-id": 0}, {"sim-data-dir": str(DATA_DIR)}),
                                    ({"partition-id": 0}, {"simulation": True, "sim-data-dir": ""})):
        with pytest.raises(PermissionError):
            client_app.resolve_data_source(node_config, run_config)


# ---------------------------------------------------------------------------
# Node selection: the research node never receives a message


class FakeGrid(Grid):
    def __init__(self, node_ids):
        self.node_ids = list(node_ids)
        self.pushed = []

    def set_run(self, run):
        pass

    @property
    def run(self):
        return None

    def create_message(self, content, message_type, dst_node_id, group_id, ttl=None):
        return Message(content, dst_node_id=dst_node_id, message_type=message_type, group_id=group_id)

    def get_node_ids(self):
        return list(self.node_ids)

    def push_messages(self, messages):
        messages = list(messages)
        self.pushed += messages
        return [str(i) for i, _ in enumerate(messages)]

    def pull_messages(self, message_ids):
        return []

    def send_and_receive(self, messages, *, timeout=None):
        self.push_messages(messages)
        return []


def test_pinned_ids_parse_and_reject_research():
    assert parse_clinic_node_ids(PINS) == {A_ID: "clinic_a", B_ID: "clinic_b"}
    assert parse_clinic_node_ids("{}") == {}
    for bad in ('{"clinic_a":"1","research":"2"}', '{"clinic_a":"1"}', '{"clinic_a":"1","clinic_b":"1"}', "[]"):
        with pytest.raises(ValueError):
            parse_clinic_node_ids(bad)


def test_strategy_sampling_only_sees_pinned_clinics():
    grid = FakeGrid([A_ID, RESEARCH_ID, B_ID])
    clinic_grid = select_clinic_nodes(grid, parse_clinic_node_ids(PINS), simulation=False, connect_timeout=0)
    assert sorted(clinic_grid.get_node_ids()) == [A_ID, B_ID]
    strategy = FedAvg(min_train_nodes=2, min_evaluate_nodes=2, min_available_nodes=2)
    arrays = ArrayRecord(initial_weights())
    for configure in (strategy.configure_train, strategy.configure_evaluate):
        for _ in range(5):
            messages = list(configure(1, arrays, ConfigRecord({}), clinic_grid))
            assert sorted(m.metadata.dst_node_id for m in messages) == [A_ID, B_ID]


def test_grid_refuses_messages_to_the_research_node():
    grid = FakeGrid([A_ID, RESEARCH_ID, B_ID])
    clinic_grid = ClinicOnlyGrid(grid, [A_ID, B_ID], connect_timeout=0)
    rogue = Message(RecordDict({}), dst_node_id=RESEARCH_ID, message_type="train")
    for send in (clinic_grid.push_messages, clinic_grid.send_and_receive):
        with pytest.raises(SelectionError):
            send([rogue])
    with pytest.raises(SelectionError):
        clinic_grid.create_message(RecordDict({}), "train", RESEARCH_ID, "1")
    assert grid.pushed == []


def test_missing_pinned_clinic_fails_instead_of_training_on_fewer():
    clinic_grid = ClinicOnlyGrid(FakeGrid([A_ID, RESEARCH_ID]), [A_ID, B_ID], connect_timeout=0)
    with pytest.raises(SelectionError):
        clinic_grid.get_node_ids()


def test_unpinned_selection_only_in_two_node_simulation():
    with pytest.raises(SelectionError):
        select_clinic_nodes(FakeGrid([A_ID, B_ID]), {}, simulation=False, connect_timeout=0)
    with pytest.raises(SelectionError):
        select_clinic_nodes(FakeGrid([A_ID, B_ID, RESEARCH_ID]), {}, simulation=True, connect_timeout=0)
    assert select_clinic_nodes(FakeGrid([A_ID, B_ID]), {}, simulation=True, connect_timeout=0).allowed == {A_ID, B_ID}


def _reply(src, role="clinic_a", phase="train", metrics=None, arrays=None, extra=None):
    records = {"metrics": MetricRecord(metrics or ({"num-examples": 10, "train-loss": 0.4} if phase == "train"
                                                  else {"num-examples": 10, "test-loss": 0.4, "auc": 0.6})),
               "meta": ConfigRecord({"role": role})}
    if phase == "train":
        records["arrays"] = ArrayRecord(arrays or initial_weights())
    records.update(extra or {})
    msg = Message(RecordDict({}), dst_node_id=src, message_type=phase)
    TaskIdentity.node_id = src
    reply = Message(RecordDict(records), reply_to=msg)
    TaskIdentity.node_id = 1
    return reply


def test_reply_validator_accepts_clinic_replies():
    validator = ReplyValidator({A_ID: "clinic_a", B_ID: "clinic_b"}, [A_ID, B_ID])
    validator.validate(1, "train", [_reply(A_ID), _reply(B_ID, "clinic_b")])
    validator.validate(1, "evaluate", [_reply(A_ID, phase="evaluate"), _reply(B_ID, "clinic_b", phase="evaluate")])
    assert len(validator.log) == 4 and all(entry["role"] in {"clinic_a", "clinic_b"} for entry in validator.log)


@pytest.mark.parametrize("replies", [
    lambda: [_reply(A_ID)],                                                                  # a clinic is missing
    lambda: [_reply(A_ID), _reply(RESEARCH_ID, "clinic_b")],                                 # research node replied
    lambda: [_reply(A_ID), _reply(B_ID, "clinic_a")],                                        # wrong role claim
    lambda: [_reply(A_ID), _reply(B_ID, "research")],                                        # non-clinic role
    lambda: [_reply(A_ID), _reply(B_ID, "clinic_b", metrics={"num-examples": 10, "train-loss": 0.4, "n-readmitted": 3})],
    lambda: [_reply(A_ID), _reply(B_ID, "clinic_b", arrays=[np.zeros(NUM_FEATURES + 1), np.zeros(1)])],
    lambda: [_reply(A_ID), _reply(B_ID, "clinic_b", extra={"records": ConfigRecord({"mrn": "B-100001"})})],
])
def test_reply_validator_fails_closed(replies):
    validator = ReplyValidator({A_ID: "clinic_a", B_ID: "clinic_b"}, [A_ID, B_ID])
    with pytest.raises(SelectionError):
        validator.validate(1, "train", replies())


# ---------------------------------------------------------------------------
# Metrics and results pipeline


def test_auc_matches_scikit_learn():
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(3)
    y = rng.random(300) < 0.2
    scores = np.round(rng.random(300) + y * 0.3, 2)  # rounded to create ties
    assert auc(y, scores) == pytest.approx(roc_auc_score(y, scores), abs=1e-12)


def test_results_pipeline_builds_schema_and_plots(tmp_path):
    data = evaluate.load_all(DATA_DIR, 0.2, 20260930)
    X = np.vstack([c.X_train for c in data.values()])
    y = np.concatenate([c.y_train for c in data.values()])
    weights = train(initial_weights(), X, y, epochs=40, learning_rate=1.0, l2=1e-3)
    fl = {"config": {"num-server-rounds": 2, "local-epochs": 20, "learning-rate": 1.0, "l2": 1e-3,
                     "split-seed": 20260930, "test-fraction": 0.2},
          "feature_names": list(FEATURE_NAMES), "coefficients": weights[0].tolist(), "intercept": float(weights[1][0]),
          "rounds": [{"round": r, "weighted_auc": 0.6, "weighted_test_loss": 0.46, "weighted_train_loss": 0.45} for r in (1, 2)]}
    comparison = evaluate.compare(fl, DATA_DIR)
    assert set(comparison["models"]) == {"federated", "clinic_a_only", "clinic_b_only", "pooled"}
    for model in comparison["models"].values():
        assert set(model["scores"]) >= {"clinic_a", "clinic_b", "combined", "weighted_clinic_auc"}
        assert model["sglt2"]["odds_ratio"] == pytest.approx(np.exp(model["sglt2"]["coefficient"]))
    # Federated weights equal to a pooled fit of the same epochs score identically.
    assert comparison["models"]["federated"]["scores"]["combined"]["auc"] == pytest.approx(
        comparison["models"]["pooled"]["scores"]["combined"]["auc"])
    assert "ci95_odds_ratio" in comparison["models"]["pooled"]["sglt2"]
    evaluate.plot_rounds(fl, comparison, tmp_path / "rounds.png")
    evaluate.plot_comparison(comparison, tmp_path / "comparison.png")
    assert (tmp_path / "rounds.png").stat().st_size > 10_000 and (tmp_path / "comparison.png").stat().st_size > 10_000
    json.dumps(comparison)  # serialisable


def test_fab_contains_code_only(tmp_path):
    config = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["flwr"]["app"]
    assert config["publisher"] == "praven1"
    assert not any(pattern.endswith(".json") or "evaluate" in pattern or "data" in pattern for pattern in config["fab-include"])
    flwr = APP / ".venv" / "Scripts" / "flwr.exe"
    result = subprocess.run([str(flwr), "build", "--app", str(APP)], cwd=tmp_path, capture_output=True, text=True,
                            env={**__import__("os").environ, "PYTHONUTF8": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    fabs = list(tmp_path.glob("*.fab")) or list(APP.glob("*.fab"))
    assert fabs
    names = zipfile.ZipFile(fabs[0]).namelist()
    for fab in fabs:
        if fab.parent == APP:
            fab.unlink()
    assert not any(name.endswith(".json") and "patients" in name for name in names)
    assert not any("evaluate" in name or "canaries" in name or "run_experiment" in name for name in names)
    assert {"fl_readmission/client_app.py", "fl_readmission/server_app.py", "shared/allowlist.py"} <= set(names)


def test_start_scripts_give_each_supernode_its_own_flower_home():
    """Flower names a run's runtime env by run ID and deletes it when a ClientApp exits;
    SuperNodes sharing one Flower home on this machine would break each other's runs."""
    import re
    for script in ("start_local_grid.sh", "start_supergrid_nodes.sh"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        homes = re.findall(r'FLWR_HOME="\$LEDGER_ROOT/flwr-home/(\w+)" coordinator/\.venv/Scripts/flower-supernode\.exe', text)
        roles = re.findall(r"--node-config 'role=\"(\w+)\"", text)
        assert homes == roles == ["clinic_a", "clinic_b", "research"], script
        assert text.count("flower-supernode.exe") == 3


def test_runtime_install_excludes_dev_tools():
    config = tomllib.loads((APP / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["uv"]["default-groups"] == []
    assert sorted(dep.split(">")[0] for dep in config["project"]["dependencies"]) == ["flwr", "numpy"]
