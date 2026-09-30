"""Load a clinic's patient JSON and trial criteria from disk."""

from __future__ import annotations

import copy
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_CORE = Path(__file__).parent


def _find(env_var: str, filename: str, dirs: list[Path]) -> Path:
    candidates = [Path(os.environ[env_var])] if os.environ.get(env_var) else []
    candidates += [d / filename for d in dirs]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(f"{filename} not found (set {env_var}); tried {candidates}")


@lru_cache(maxsize=None)
def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_patients(clinic: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read and cache one clinic's patient file for the process lifetime.

    Each clinic app ships only its own file and passes its own ``data_dir``.
    Handlers treat the returned records as read-only.
    """
    dirs = ([data_dir] if data_dir else []) + [Path.cwd() / "data"]
    path = _find(f"CLINIC_{clinic}_DATA", f"clinic_{clinic.lower()}_patients.json", dirs)
    doc = _read_json(path)
    if doc.get("clinic") != clinic:
        raise ValueError(f"{path} belongs to clinic {doc.get('clinic')!r}, not {clinic!r}")
    return doc["patients"]


def load_cached_criteria(nct_id: str) -> list[dict[str, Any]]:
    """M3's hand-checked criteria for a demo trial (public trial data)."""
    if not re.fullmatch(r"NCT\d{8}", nct_id):
        raise ValueError(f"bad NCT id: {nct_id!r}")
    path = _find("CRITERIA_CACHE", f"criteria_{nct_id}.json",
                 [_CORE / "trial_cache", _CORE.parent / "cache"])
    # Validation adds defaults, so callers receive a copy of the cached document.
    return copy.deepcopy(_read_json(path)["criteria"])
