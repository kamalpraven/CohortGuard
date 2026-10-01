"""Offline comparison: federated vs clinic-only vs pooled, SGLT2 coefficient, and plots.

This module reads BOTH clinics' records. That is possible only because the data is
synthetic; it is excluded from the FAB and never runs on a clinic node. The pooled
model is an upper bound that a real deployment could not train.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .task import (
    CLINIC_ROLES,
    FEATURE_NAMES,
    ClinicData,
    auc,
    initial_weights,
    load_clinic,
    log_loss,
    predict_proba,
    train,
)

SGLT2 = "current_sglt2_inhibitor"
MODEL_LABELS = {
    "federated": "Federated (FedAvg)",
    "clinic_a_only": "Clinic A only",
    "clinic_b_only": "Clinic B only",
    "pooled": "Pooled (upper bound)",
}
# Validated categorical slots 1-4 (dataviz reference palette, light mode); fixed per model.
MODEL_COLORS = {"federated": "#2a78d6", "clinic_a_only": "#eb6834", "clinic_b_only": "#1baf7a", "pooled": "#eda100"}
TEST_SETS = {"clinic_a": "Clinic A test", "clinic_b": "Clinic B test", "combined": "Combined test"}
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260930


def auc_ci95(y: np.ndarray, p: np.ndarray) -> list[float]:
    """Seeded stratified bootstrap 95% interval for AUC (resamples positives and negatives)."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    stats = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        stats.append(auc(y[idx], p[idx]))
    return [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))]


def patient_file(data_dir: Path, role: str) -> Path:
    letter = role[-1]
    return Path(data_dir) / f"clinic-{letter}-agent" / f"clinic_{letter}" / "data" / f"{role}_patients.json"


def load_all(data_dir: Path, test_fraction: float, split_seed: int) -> dict[str, ClinicData]:
    return {role: load_clinic(str(patient_file(data_dir, role)), role, test_fraction, split_seed) for role in CLINIC_ROLES}


def score(weights: list[np.ndarray], data: dict[str, ClinicData]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role, clinic in data.items():
        p = predict_proba(weights, clinic.X_test)
        out[role] = {"auc": auc(clinic.y_test, p), "auc_ci95": auc_ci95(clinic.y_test, p),
                     "log_loss": log_loss(clinic.y_test, p), "n": len(clinic.y_test), "events": int(clinic.y_test.sum())}
    X = np.vstack([c.X_test for c in data.values()])
    y = np.concatenate([c.y_test for c in data.values()])
    p = predict_proba(weights, X)
    out["combined"] = {"auc": auc(y, p), "auc_ci95": auc_ci95(y, p), "log_loss": log_loss(y, p), "n": len(y), "events": int(y.sum())}
    n = sum(out[role]["n"] for role in data)
    # Same statistic the federated run logs per round: per-clinic metrics weighted by test size.
    out["weighted_clinic_auc"] = sum(out[r]["auc"] * out[r]["n"] for r in data) / n
    out["weighted_clinic_log_loss"] = sum(out[r]["log_loss"] * out[r]["n"] for r in data) / n
    return out


def wald_ci(weights: list[np.ndarray], X: np.ndarray, y: np.ndarray, l2: float, feature: str) -> dict[str, float]:
    """95% Wald interval from the penalised log-likelihood Hessian (reference only)."""
    Xa = np.hstack([X, np.ones((len(y), 1))])
    p = predict_proba(weights, X)
    penalty = np.diag([l2 * len(y)] * X.shape[1] + [0.0])
    covariance = np.linalg.inv(Xa.T @ (Xa * (p * (1 - p))[:, None]) + penalty)
    index = FEATURE_NAMES.index(feature)
    beta, se = float(weights[0][index]), float(math.sqrt(covariance[index, index]))
    return {"se": se, "ci95_coef": [beta - 1.96 * se, beta + 1.96 * se],
            "ci95_odds_ratio": [math.exp(beta - 1.96 * se), math.exp(beta + 1.96 * se)]}


def compare(fl: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    config = fl["config"]
    rounds, epochs = int(config["num-server-rounds"]), int(config["local-epochs"])
    lr, l2 = float(config["learning-rate"]), float(config["l2"])
    data = load_all(data_dir, float(config["test-fraction"]), int(config["split-seed"]))
    if list(fl["feature_names"]) != list(FEATURE_NAMES):
        raise ValueError("federated run used a different feature order")

    # Same trainer and the same total number of epochs as the federated run.
    def fit(X: np.ndarray, y: np.ndarray) -> list[np.ndarray]:
        return train(initial_weights(), X, y, epochs=rounds * epochs, learning_rate=lr, l2=l2)

    X_pool = np.vstack([c.X_train for c in data.values()])
    y_pool = np.concatenate([c.y_train for c in data.values()])
    models = {
        "federated": [np.array(fl["coefficients"]), np.array([fl["intercept"]])],
        "clinic_a_only": fit(data["clinic_a"].X_train, data["clinic_a"].y_train),
        "clinic_b_only": fit(data["clinic_b"].X_train, data["clinic_b"].y_train),
        "pooled": fit(X_pool, y_pool),
    }
    training_sets = {"clinic_a_only": (data["clinic_a"].X_train, data["clinic_a"].y_train),
                     "clinic_b_only": (data["clinic_b"].X_train, data["clinic_b"].y_train),
                     "pooled": (X_pool, y_pool)}
    index = FEATURE_NAMES.index(SGLT2)
    results: dict[str, Any] = {}
    for name, weights in models.items():
        beta = float(weights[0][index])
        sglt2: dict[str, Any] = {"coefficient": beta, "odds_ratio": math.exp(beta), "direction": "negative" if beta < 0 else "non-negative"}
        if name in training_sets:
            sglt2.update(wald_ci(weights, *training_sets[name], l2, SGLT2))
        results[name] = {"label": MODEL_LABELS[name], "scores": score(weights, data), "sglt2": sglt2}

    users = X_pool[:, index] == 1.0
    return {
        "note": ("Offline evaluation with access to both clinics' test sets, possible only because the data is "
                 "synthetic. The pooled model is an upper bound a real deployment could not train."),
        "models": results,
        "unadjusted_train_readmission_rate": {"sglt2_users": float(y_pool[users].mean()), "non_users": float(y_pool[~users].mean()),
                                              "n_users": int(users.sum()), "n_non_users": int((~users).sum())},
        "planted_effect_note": ("generate_data.py lowers readmission probability by 0.07 (absolute) for current "
                                "sglt2_inhibitor users; an adjusted odds ratio is not that quantity."),
    }


# ---------------------------------------------------------------------------
# Plots (matplotlib is a dev-only dependency)

INK, INK_MUTED, GRID, SURFACE = "#1f1f1e", "#6b6a63", "#e6e5e0", "#fcfcfb"


def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, length=0, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_rounds(fl: dict[str, Any], comparison: dict[str, Any], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rounds = [r["round"] for r in fl["rounds"]]
    panels = (("weighted_auc", "weighted_clinic_auc", "AUC"), ("weighted_test_loss", "weighted_clinic_log_loss", "Log loss"))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)
    for ax, (round_key, ref_key, label) in zip(axes, panels):
        _style(ax)
        values = [r[round_key] for r in fl["rounds"]]
        ax.plot(rounds, values, color=MODEL_COLORS["federated"], linewidth=2, marker="o", markersize=4.5,
                markeredgecolor=SURFACE, markeredgewidth=1.2, label=MODEL_LABELS["federated"], zorder=3)
        for name in ("clinic_a_only", "clinic_b_only", "pooled"):
            ref = comparison["models"][name]["scores"][ref_key]
            ax.axhline(ref, color=MODEL_COLORS[name], linewidth=1.5, linestyle=(0, (4, 3)), label=MODEL_LABELS[name], zorder=2)
            ax.annotate(f"{MODEL_LABELS[name]}  {ref:.3f}", (rounds[-1], ref), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=8, color=INK_MUTED, annotation_clip=False)
        ax.annotate(f"{values[-1]:.3f}", (rounds[-1], values[-1]), xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=8.5, color=INK)
        ax.set_xlabel("FedAvg round", color=INK_MUTED, fontsize=9)
        ax.set_title(f"{label}, weighted mean over clinic test sets", loc="left", fontsize=10.5, color=INK)
        ax.set_xticks(sorted({rounds[0], *range(5, rounds[-1] + 1, 5), rounds[-1]}))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncol=4, frameon=False, fontsize=9, bbox_to_anchor=(0.01, 1.0),
               labelcolor=INK)
    fig.text(0.01, 0.005, "Dashed lines: models trained without federation, scored on the same test sets "
             "(offline, synthetic data only).", fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 0.9, 0.93))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_comparison(comparison: dict[str, Any], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = list(MODEL_LABELS)
    sets = list(TEST_SETS)
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), facecolor=SURFACE)
    for ax, (metric, label) in zip(axes, (("auc", "AUC (higher is better)"), ("log_loss", "Log loss (lower is better)"))):
        _style(ax)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        for row, test_set in enumerate(sets):
            y = len(sets) - 1 - row
            values = [comparison["models"][n]["scores"][test_set][metric] for n in names]
            ax.plot([min(values), max(values)], [y, y], color=GRID, linewidth=2, zorder=1)
            for offset, (name, value) in enumerate(zip(names, values)):
                ax.scatter(value, y - (offset - 1.5) * 0.14, s=58, color=MODEL_COLORS[name], edgecolor=SURFACE,
                           linewidth=1.5, zorder=3, label=MODEL_LABELS[name] if row == 0 else None)
                ax.annotate(f"{value:.3f}", (value, y - (offset - 1.5) * 0.14), xytext=(7, 0), textcoords="offset points",
                            va="center", fontsize=7.5, color=INK_MUTED)
        ax.set_yticks(range(len(sets)))
        ax.set_yticklabels([TEST_SETS[s] for s in reversed(sets)], color=INK, fontsize=9)
        ax.set_ylim(-0.6, len(sets) - 0.4)
        lo, hi = ax.get_xlim()
        ax.set_xlim(lo, hi + (hi - lo) * 0.12)
        ax.set_title(label, loc="left", fontsize=10.5, color=INK)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncol=4, frameon=False, fontsize=9, bbox_to_anchor=(0.01, 1.0),
               labelcolor=INK)
    fig.text(0.01, 0.005, "Offline evaluation on held-out 20% local test sets; possible only because the data is synthetic.",
             fontsize=8, color=INK_MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 0.9))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
