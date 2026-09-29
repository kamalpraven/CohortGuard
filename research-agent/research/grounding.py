"""Grounding gate: nothing reaches a doctor unless the source APIs confirm it.

Checks for every candidate, regardless of who produced it (code or LLM):
  1. the NCT ID is well formed and the trial exists
  2. the named intervention actually appears in that trial
  3. the claimed phase and status match the registry
  4. every cited PMID exists
Failures are dropped and logged for the evaluation harness (M4).
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .http_cache import CacheMiss, Http
from .sources import NCT_RE, get_trial, summarize_pmids


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def intervention_matches(name: str, trial: dict) -> bool:
    n = _norm(name)
    if not n:
        return False
    for i in trial["interventions"]:
        t = _norm(i["name"])
        if t and (n in t or t in n):
            return True
    return False


def verify_candidate(http: Http, cand: dict) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    claimed = cand.get("trial", {}) or {}
    nct = claimed.get("nct_id")
    try:
        trial = get_trial(http, nct)
    except CacheMiss:
        return False, [f"trial {nct!r} could not be verified (not in cache); failing closed"]
    if trial is None:
        if not isinstance(nct, str) or not NCT_RE.match(nct):
            return False, [f"{nct!r} is not a valid NCT ID"]
        return False, [f"{nct} not found in the ClinicalTrials.gov registry"]
    if not intervention_matches(cand.get("intervention", ""), trial):
        reasons.append(f"intervention {cand.get('intervention')!r} not found in {nct}")
    if claimed.get("status") and claimed["status"] != trial["status"]:
        reasons.append(f"status mismatch: claimed {claimed['status']}, registry {trial['status']}")
    if claimed.get("phase"):
        registry_phases = {p.upper() for p in trial["phases"]}
        claimed_phase = str(claimed["phase"]).upper().replace(" ", "")
        if claimed_phase.isdigit():  # "3" -> "PHASE3"; leave EARLY_PHASE1, NA, PHASE2 as-is
            claimed_phase = "PHASE" + claimed_phase
        if claimed_phase not in registry_phases:
            reasons.append(f"phase mismatch: claimed {claimed['phase']}, registry {trial['phases']}")
    pmids = [e.get("pmid") for e in cand.get("evidence", []) if e.get("pmid")]
    if pmids:
        try:
            found = summarize_pmids(http, [str(p) for p in pmids])
        except CacheMiss:
            found = {}
        missing = [p for p in pmids if str(p) not in found]
        if missing:
            reasons.append(f"PMIDs not found: {missing}")
    return (not reasons), reasons


def grounding_gate(http: Http, candidates: List[dict], log: list | None = None):
    kept, dropped = [], []
    for c in candidates:
        ok, reasons = verify_candidate(http, c)
        c = dict(c, verified=ok)
        (kept if ok else dropped).append(c)
        if log is not None:
            log.append({"event": "grounding_check",
                        "nct_id": c.get("trial", {}).get("nct_id"),
                        "intervention": c.get("intervention"),
                        "passed": ok, "reasons": reasons})
    return kept, dropped
