"""Prepare the on-stage cache. Run this on a machine with internet access.

  python record_demo_cache.py "type 2 diabetes" readmission hospitalization

Then pick 3-5 trials from the printed list, check their criteria by hand,
and set DEMO_TRIALS below so their records and papers are cached too.
On stage, construct Http(mode="replay") and everything is served from ./cache.
"""
import json
import os
import sys
from pathlib import Path

from research.candidates import build_candidates
from research.criteria import structure_criteria
from research.http_cache import Http
from research.sources import get_trial, papers_for_trial, summarize_pmids

DEMO_TRIALS: list[str] = ["NCT07112339", "NCT07060456"]
FIXTURE_URLS: list[str] = ["https://kamalpraven.github.io/cohortguard-fixtures/injection_page.html"]  # add the hosted injection page URL, e.g. https://<user>.github.io/<repo>/injection_page.html
SEEDED_FAKE_IDS = ["NCT99999999"]  # recorded so replay mode returns the registry's real 404
ROOT = Path(__file__).resolve().parent
FORCE = "--force-criteria" in sys.argv  # overwrite hand-checked criteria files (normally never)

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force-criteria"]
    condition = " ".join((args[0] if args else "type 2 diabetes").lower().split())
    keywords = args[1:] or ["readmission"]
    http = Http(mode="record")
    log: list = []
    cands = build_candidates(http, condition, keywords, log=log)
    for c in cands:
        t = c["trial"]
        print(f"{t['nct_id']}  {t['phase']:<8} {t['status']:<12} score={c['score']:<4} "
              f"{c['intervention'][:30]:<30} papers={len(c['evidence'])}")
    dropped = [e for e in log if not e["passed"]]
    print(f"\n{len(cands)} verified, {len(dropped)} dropped by grounding gate")
    for e in dropped:
        print(f"  DROPPED {e['nct_id']} ({e['intervention']}): {'; '.join(e['reasons'])}")

    for url in FIXTURE_URLS:
        status, _ = http.get(url)
        print(f"{url}: HTTP {status} {'(recorded for replay)' if status == 200 else '(CHECK THE URL)'}")

    for fake in SEEDED_FAKE_IDS:
        print(f"{fake}: {'EXISTS (pick another fake ID!)' if get_trial(http, fake) else 'not found (recorded for replay)'}")

    os.makedirs(ROOT / "samples", exist_ok=True)
    with open(ROOT / "samples" / "candidates.json", "w", encoding="utf-8") as f:
        json.dump(cands, f, indent=2)
    with open(ROOT / "samples" / "grounding_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

    for nct in DEMO_TRIALS:
        t = get_trial(http, nct)
        if not t:
            print(f"{nct}: NOT FOUND"); continue
        summarize_pmids(http, papers_for_trial(http, t))
        path = ROOT / "cache" / f"criteria_{nct}.json"
        if path.exists() and not FORCE:
            print(f"{nct}: records cached; kept existing hand-checked {path.name}")
            continue
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"trial": t, "criteria": structure_criteria(t)}, f, indent=2)
        print(f"{nct}: cached; edit {path.name} by hand to finalize criteria")
