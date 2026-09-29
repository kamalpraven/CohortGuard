"""Load a clinic's patient JSON and trial criteria from disk."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_CORE = Path(__file__).parent


def _find(env_var: str, filename: str, dirs: list[Path]) -> Path:
    candidates = [Path(os.environ[env_var])] if os.environ.get(env_var) else []
    candidates += [d / filename for d in dirs]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(f"{filename} not found (set {env_var}); tried {candidates}")


def load_patients(clinic: str, data_dir: Path | None = None) -> list[dict]:
    """Read clinic_<x>_patients.json ({"clinic": "A", "patients": [...]}).

    Each clinic app ships only its own file and passes its own `data_dir`.
    """
    dirs = ([data_dir] if data_dir else []) + [Path.cwd() / "data"]
    path = _find(f"CLINIC_{clinic}_DATA", f"clinic_{clinic.lower()}_patients.json", dirs)
    doc = json.loads(path.read_text())
    if doc.get("clinic") != clinic:
        raise ValueError(f"{path} belongs to clinic {doc.get('clinic')!r}, not {clinic!r}")
    return doc["patients"]


def load_cached_criteria(nct_id: str) -> list[dict]:
    """M3's hand-checked criteria for a demo trial (public trial data)."""
    if not re.fullmatch(r"NCT\d{8}", nct_id):
        raise ValueError(f"bad NCT id: {nct_id!r}")
    path = _find("CRITERIA_CACHE", f"criteria_{nct_id}.json",
                 [_CORE / "trial_cache", _CORE.parent / "cache"])
    return json.loads(path.read_text())["criteria"]
