"""ClinicalTrials.gov (API v2) and PubMed (E-utilities) clients.

All functions return plain dicts with facts taken from the source APIs.
The LLM never fills these fields; it only adds narrative (mechanism, relevance).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Dict, List, Optional

from .http_cache import Http

CTGOV = "https://clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_TOOL = "cohortguard_hackathon"
NCBI_EMAIL = None  # set to a team email; NCBI asks for one

NCT_RE = re.compile(r"^NCT\d{8}$")


# ---------------------------------------------------------------- ClinicalTrials.gov

def normalize_study(study: dict) -> dict:
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    arms = ps.get("armsInterventionsModule", {})
    outcomes = ps.get("outcomesModule", {})
    elig = ps.get("eligibilityModule", {})
    conds = ps.get("conditionsModule", {})
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "status": status.get("overallStatus"),
        "phases": design.get("phases", []) or [],
        "interventions": [{"name": i.get("name", ""), "type": i.get("type", "")}
                          for i in arms.get("interventions", []) or []],
        "conditions": conds.get("conditions", []) or [],
        "primary_outcomes": [o.get("measure", "") for o in outcomes.get("primaryOutcomes", []) or []],
        "eligibility_text": elig.get("eligibilityCriteria", "") or "",
        "min_age": elig.get("minimumAge"),
        "max_age": elig.get("maximumAge"),
        "sex": elig.get("sex"),
    }


def search_trials(http: Http, condition: str, intervention: Optional[str] = None,
                  statuses: Sequence[str] = ("RECRUITING",), page_size: int = 20) -> List[dict]:
    params = {
        "query.cond": condition,
        "query.intr": intervention,
        "filter.overallStatus": ",".join(statuses) if statuses else None,
        "pageSize": page_size,
        "format": "json",
    }
    code, data = http.get_json(CTGOV, params)
    if code != 200 or not data:
        return []
    return [normalize_study(s) for s in data.get("studies", [])]


def get_trial(http: Http, nct_id: str) -> Optional[dict]:
    """Return the normalized trial, or None if the ID is malformed or does not exist."""
    if not isinstance(nct_id, str) or not NCT_RE.match(nct_id):
        return None
    code, data = http.get_json(f"{CTGOV}/{nct_id}", {"format": "json"})
    if code != 200 or not data:
        return None
    t = normalize_study(data)
    return t if t["nct_id"] == nct_id else None


# ---------------------------------------------------------------- PubMed

def _ncbi(params: dict) -> dict:
    p = dict(params, tool=NCBI_TOOL, retmode="json")
    if NCBI_EMAIL:
        p["email"] = NCBI_EMAIL
    return p


def search_pubmed(http: Http, term: str, retmax: int = 5) -> List[str]:
    code, data = http.get_json(f"{EUTILS}/esearch.fcgi", _ncbi({"db": "pubmed", "term": term, "retmax": retmax}))
    if code != 200 or not data:
        return []
    return data.get("esearchresult", {}).get("idlist", []) or []


def summarize_pmids(http: Http, pmids: List[str]) -> Dict[str, dict]:
    """pmid -> {title, journal, pubdate}. PMIDs that do not exist are omitted."""
    pmids = [p for p in pmids if str(p).isdigit()]
    if not pmids:
        return {}
    code, data = http.get_json(f"{EUTILS}/esummary.fcgi", _ncbi({"db": "pubmed", "id": ",".join(pmids)}))
    if code != 200 or not data:
        return {}
    res = data.get("result", {})
    out = {}
    for uid in res.get("uids", []):
        rec = res.get(uid, {})
        if "error" in rec or not rec.get("title"):
            continue
        out[uid] = {"title": rec.get("title"), "journal": rec.get("source"), "pubdate": rec.get("pubdate")}
    return out


def papers_for_trial_linked(
    http: Http, trial: dict, retmax: int = 3
) -> tuple[List[str], str | None]:
    """(pmids, link). link says how the papers relate to the trial:
      "cites_trial"             - the paper cites this trial's NCT ID
      "same_drug_and_condition" - fallback: same drug and condition, NOT about this trial
    """
    pmids = search_pubmed(http, f"{trial['nct_id']}[si]", retmax)
    if pmids:
        return pmids, "cites_trial"
    drug = next((i["name"] for i in trial["interventions"] if i["type"] == "DRUG"), None)
    cond = trial["conditions"][0] if trial["conditions"] else None
    if drug and cond:
        return search_pubmed(http, f'"{drug}"[tiab] AND "{cond}"[tiab]', retmax), "same_drug_and_condition"
    return [], None


def papers_for_trial(http: Http, trial: dict, retmax: int = 3) -> List[str]:
    return papers_for_trial_linked(http, trial, retmax)[0]
