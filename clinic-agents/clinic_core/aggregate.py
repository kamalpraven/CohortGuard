"""Privacy-gated aggregate request handling shared by clinic nodes."""

from __future__ import annotations

from typing import Any

from .feasibility import feasibility_count
from .privacy import PrivacyGate
from .templates import (
    BadRequest,
    cohort_counts,
    cohort_filters,
    criteria_from,
    rate_pct,
    request_id_from,
)


def _released_row(cohort: str, counts: dict[str, int]) -> dict[str, Any]:
    """Build one row using only already-noised counts."""
    n = counts[f"{cohort}.n"]
    events = min(counts[f"{cohort}.events"], n)
    return {"cohort": cohort, "n": n, "events": events, "rate_pct": rate_pct(events, n)}


def handle_gated_aggregate(
    req: dict[str, Any],
    patients: list[dict[str, Any]],
    gate: PrivacyGate,
    clinic: str,
) -> dict[str, Any]:
    """Handle the fixed aggregate templates through ``PrivacyGate``."""
    base = {"request_id": None, "clinic": clinic}
    try:
        base["request_id"] = request_id_from(req)
        template = req.get("template")
        if template == "feasibility_count":
            return {
                **base,
                **feasibility_count(
                    clinic, req.get("nct_id"), criteria_from(req), patients, gate
                ),
            }
        if template == "outcome_rate_by_cohort":
            filters, counter = cohort_filters(req)
            if counter:
                return {
                    **base,
                    "status": "counter_offer",
                    "results": [],
                    "counter_offer": counter,
                    "reason": "age_band_too_fine",
                }
            rows = cohort_counts(req, patients, filters)
            flat = {f"{row['cohort']}.{key}": row[key] for row in rows for key in ("n", "events")}
            release = gate.release(flat, gate_on=min(row["n"] for row in rows))
            if release["suppressed"]:
                return {
                    **base,
                    "status": "suppressed",
                    "results": [],
                    "reason": release["reason"],
                }
            counts = release["counts"]
            return {
                **base,
                "status": "ok",
                "counter_offer": None,
                "reason": None,
                "results": [_released_row(row["cohort"], counts) for row in rows],
                "noise_scale": release["noise_scale"],
                "budget_remaining": release["budget_remaining"],
                "definitions": {"readmit_30d": "unplanned return <= 30 days"},
            }
        raise BadRequest("unknown template")
    except (BadRequest, KeyError, TypeError, ValueError, IndexError):
        return {**base, "status": "rejected", "reason": "invalid_request"}
    except Exception:
        return {**base, "status": "error", "reason": "internal_error"}
