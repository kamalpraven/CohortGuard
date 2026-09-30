"""Salted-hash canary matching without shipping raw canary identifiers."""

from __future__ import annotations

import hashlib
import json
import re
from importlib.resources import files
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")
_DATA = "canary_hashes.json"


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def _hash(salt: str, phrase: str) -> str:
    return hashlib.sha256(f"{salt}\0{phrase}".encode("utf-8")).hexdigest()


def _load_data() -> tuple[str, set[str]]:
    try:
        raw = files(__package__).joinpath(_DATA).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "", set()
    data = json.loads(raw)
    salt = data.get("salt")
    hashes = data.get("hashes")
    if not isinstance(salt, str) or not isinstance(hashes, list):
        raise ValueError("invalid_canary_hash_data")
    return salt, {str(item) for item in hashes}


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(str(key))
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def contains_hashed_canary(value: Any) -> bool:
    """Hash normalized 1-3 word n-grams from ``value`` and compare to shipped hashes."""
    salt, canary_hashes = _load_data()
    if not salt or not canary_hashes:
        return False
    for text in _strings(value):
        tokens = _tokens(text)
        for size in (1, 2, 3):
            for start in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[start : start + size])
                if _hash(salt, phrase) in canary_hashes:
                    return True
    return False
