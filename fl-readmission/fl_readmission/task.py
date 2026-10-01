"""Features, local data split and NumPy logistic regression for the readmission model.

Everything here runs inside a clinic. Only the model weights and a few aggregate
metrics built from these functions ever leave it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from shared.allowlist import DIAGNOSIS_CODES, MEDICATION_CLASSES

# Fixed clinical reference constants (centre, scale). They are chosen in advance and
# never derived from any dataset, so scaling shares no statistics between clinics.
NUMERIC_SCALING: tuple[tuple[str, float, float], ...] = (
    ("age", 60.0, 20.0),     # years
    ("hba1c", 8.0, 2.0),     # %
    ("egfr", 80.0, 25.0),    # mL/min/1.73m²
    ("bmi", 30.0, 6.0),      # kg/m²
)
DIAGNOSES = tuple(sorted(DIAGNOSIS_CODES))
MEDICATIONS = tuple(sorted(MEDICATION_CLASSES))
FEATURE_NAMES: tuple[str, ...] = (
    *(name for name, _, _ in NUMERIC_SCALING),
    "sex_female",
    *(f"dx_{code}" for code in DIAGNOSES),
    *(f"current_{med}" for med in MEDICATIONS),
    *(f"prior_{med}" for med in MEDICATIONS),
)
NUM_FEATURES = len(FEATURE_NAMES)
LABEL_FIELD = "readmitted_30d"
# The only record fields the loader reads. Identifiers (mrn, name, dob) are never touched.
RECORD_FIELDS = (
    "age", "sex", "hba1c", "egfr", "bmi",
    "diagnoses", "current_medications", "prior_medications", LABEL_FIELD,
)
# FL analogue of the privacy gate's minimum cell size: a clinic sends no update for a
# binary feature held by fewer than this many of its training patients. Fixed in code
# so the server cannot lower it through run config.
MIN_FEATURE_PATIENTS = 10
CLINIC_ROLES = ("clinic_a", "clinic_b")


def encode_record(record: dict[str, Any]) -> list[float]:
    """Encode one record from the fixed field allowlist into the feature vector."""
    values = {field: record[field] for field in RECORD_FIELDS}
    row = [(float(values[name]) - centre) / scale for name, centre, scale in NUMERIC_SCALING]
    row.append(1.0 if values["sex"] == "FEMALE" else 0.0)
    row += [1.0 if code in values["diagnoses"] else 0.0 for code in DIAGNOSES]
    row += [1.0 if med in values["current_medications"] else 0.0 for med in MEDICATIONS]
    row += [1.0 if med in values["prior_medications"] else 0.0 for med in MEDICATIONS]
    return row


def encode(records: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    X = np.array([encode_record(r) for r in records], dtype=np.float64).reshape(-1, NUM_FEATURES)
    y = np.array([bool(r[LABEL_FIELD]) for r in records], dtype=np.float64)
    return X, y


def stratified_split(y: np.ndarray, test_fraction: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (train_idx, test_idx): a seeded shuffle within each class, by row index."""
    rng = np.random.default_rng(seed)
    train, test = [], []
    for label in (0.0, 1.0):
        idx = rng.permutation(np.flatnonzero(y == label))
        n_test = int(round(test_fraction * len(idx)))
        test.append(idx[:n_test])
        train.append(idx[n_test:])
    return np.sort(np.concatenate(train)), np.sort(np.concatenate(test))


@dataclass(frozen=True)
class ClinicData:
    role: str
    X_train: np.ndarray
    y_train: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray


def expected_filename(role: str) -> str:
    return f"{role}_patients.json"


@lru_cache(maxsize=4)
def load_clinic(path: str, role: str, test_fraction: float, split_seed: int) -> ClinicData:
    """Load one clinic's own patient file and split it locally."""
    if role not in CLINIC_ROLES or Path(path).name != expected_filename(role):
        raise PermissionError("not a clinic data path for this role")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("clinic") != role[-1].upper():
        raise PermissionError("patient file belongs to another clinic")
    X, y = encode(doc["patients"])
    train_idx, test_idx = stratified_split(y, test_fraction, split_seed)
    return ClinicData(role, X[train_idx], y[train_idx], X[test_idx], y[test_idx])


# ---------------------------------------------------------------------------
# Model: weights are [coef (NUM_FEATURES,), intercept (1,)].


def initial_weights() -> list[np.ndarray]:
    return [np.zeros(NUM_FEATURES), np.zeros(1)]


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def predict_proba(weights: list[np.ndarray], X: np.ndarray) -> np.ndarray:
    coef, intercept = weights
    return sigmoid(X @ coef + intercept[0])


def small_feature_mask(X: np.ndarray) -> np.ndarray:
    """True for binary features held by fewer than MIN_FEATURE_PATIENTS patients."""
    binary = np.all((X == 0.0) | (X == 1.0), axis=0)
    return binary & ((X != 0.0).sum(axis=0) < MIN_FEATURE_PATIENTS)


def train(
    weights: list[np.ndarray],
    X: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int,
    learning_rate: float,
    l2: float,
    frozen: np.ndarray | None = None,
) -> list[np.ndarray]:
    """Full-batch gradient descent on L2-regularised log loss.

    Coefficients marked ``frozen`` are never updated, so their update is exactly zero.
    """
    coef, intercept = weights[0].astype(np.float64).copy(), weights[1].astype(np.float64).copy()
    n = len(y)
    for _ in range(epochs):
        residual = sigmoid(X @ coef + intercept[0]) - y
        grad = X.T @ residual / n + l2 * coef
        if frozen is not None:
            grad[frozen] = 0.0
        coef -= learning_rate * grad
        intercept -= learning_rate * residual.mean()
    return [coef, intercept]


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(y: np.ndarray, scores: np.ndarray) -> float:
    """ROC AUC via the rank-sum (Mann-Whitney U) statistic, averaging tied ranks."""
    y = np.asarray(y).astype(bool)
    positives, negatives = int(y.sum()), int((~y).sum())
    if positives == 0 or negatives == 0:
        raise ValueError("AUC needs both classes")
    _, inverse, counts = np.unique(np.asarray(scores), return_inverse=True, return_counts=True)
    upper = np.cumsum(counts)
    ranks = (upper - (counts - 1) / 2.0)[inverse]
    return float((ranks[y].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))
