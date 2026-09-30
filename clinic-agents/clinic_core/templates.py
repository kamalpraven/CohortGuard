"""Request validation and computation shared by both clinic agents.

Both clinics answer only fixed templates. The model may pick a template and its
parameters; this module validates them and does all computation.
"""

from __future__ import annotations

from shared.allowlist import DIAGNOSIS_CODES, MEDICATION_CLASSES, SEX_VALUES

from .criteria import validate_criterion
from .store import load_cached_criteria

MAX_CHECKABLE_CRITERIA = 8
COHORT_FILTERS = {"age_band", "diagnosis", "sex"}
OUTCOMES = {"readmit_30d": "readmitted_30d"}


class BadRequest(ValueError):
    """Request rejected before touching any data."""


def validate_criteria(criteria: list[dict]) -> None:
    """Fail closed on malformed, oversized or off-allowlist criteria (mutates source_text default)."""
    if not isinstance(criteria, list) or not all(isinstance(c, dict) for c in criteria):
        raise BadRequest("`criteria` must be a list of criterion objects")
    if sum(bool(c.get("checkable")) for c in criteria) > MAX_CHECKABLE_CRITERIA:
        raise BadRequest("too many stacked criteria")
    try:
        for c in criteria:
            c.setdefault("source_text", c.get("field", ""))
            validate_criterion(c)
    except (ValueError, KeyError, TypeError) as e:
        raise BadRequest(f"criterion outside allowlist: {e}") from e


def criteria_from(req: dict) -> list[dict]:
    criteria = req.get("criteria")
    if criteria is None:
        if not req.get("nct_id"):
            raise BadRequest("need `criteria` or a cached `nct_id`")
        try:
            criteria = load_cached_criteria(req["nct_id"])
        except (ValueError, FileNotFoundError) as e:
            raise BadRequest(str(e)) from e
    validate_criteria(criteria)
    return criteria


def _parse_band(band: str) -> tuple[int, int]:
    try:
        lo, hi = (int(x) for x in band.split("-"))
    except ValueError as e:
        raise BadRequest(f"bad age_band {band!r}") from e
    return lo, hi


def cohort_filters(req: dict) -> tuple[dict, dict | None]:
    """Validate filters. Returns (filters, counter_offer or None)."""
    filters = req.get("filters") or {}
    unknown = set(filters) - COHORT_FILTERS
    if unknown:
        raise BadRequest(f"filters not allowed: {sorted(unknown)}")
    if "diagnosis" in filters and filters["diagnosis"] not in DIAGNOSIS_CODES:
        raise BadRequest("unknown diagnosis code")
    if "sex" in filters and filters["sex"] not in SEX_VALUES:
        raise BadRequest("unknown sex value")
    counter = None
    if "age_band" in filters:
        lo, hi = _parse_band(filters["age_band"])
        if lo % 10 != 0 or hi != lo + 9:
            counter = {"age_band": f"{lo // 10 * 10}-{lo // 10 * 10 + 9}"}
    return filters, counter


def _in_filters(p: dict, filters: dict) -> bool:
    if "age_band" in filters:
        lo, hi = _parse_band(filters["age_band"])
        if not lo <= p["age"] <= hi:
            return False
    if "diagnosis" in filters and filters["diagnosis"] not in p["diagnoses"]:
        return False
    return not ("sex" in filters and p["sex"] != filters["sex"])


def rate_pct(events: int, n: int) -> float | None:
    """Event rate in percent (1 dp). Computed from already-released counts."""
    return round(100 * events / n, 1) if n else None


def cohort_counts(req: dict, patients: list[dict], filters: dict) -> list[dict]:
    if req.get("cohort_field") != "medication":
        raise BadRequest("cohort_field must be 'medication'")
    outcome = OUTCOMES.get(req.get("outcome"))
    cohorts = req.get("cohorts")
    if outcome is None or not cohorts or not set(cohorts) <= MEDICATION_CLASSES:
        raise BadRequest("bad outcome or cohorts")
    pool = [p for p in patients if _in_filters(p, filters)]
    out = []
    for m in cohorts:
        members = [p for p in pool if m in p["current_medications"]]
        events = sum(p[outcome] for p in members)
        out.append({"cohort": m, "n": len(members), "events": events,
                    "rate_pct": rate_pct(events, len(members))})
    return out
