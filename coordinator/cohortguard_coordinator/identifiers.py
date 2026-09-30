"""Deterministic identifier detection for coordinator boundary checks."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# Deliberately broad: false positives are safer than sending an identifier to Grid/model.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mrn", re.compile(r"(?i)\b[A-Z]{1,8}-MRN-[A-Z0-9-]*\d[A-Z0-9-]*\b")),
    ("mrn", re.compile(r"(?i)\bMRN\s*[:#-]?\s*[A-Z0-9-]{4,}\b")),
    ("identifier", re.compile(r"(?i)\b(?:patient[-_ ]?id|subject[-_ ]?id)\s*[:#-]?\s*[A-Z0-9-]{4,}\b")),
    ("date", re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")),
    ("date", re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)?\d{2}\b")),
    ("date", re.compile(r"(?i)\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s*(?:19|20)\d{2}\b")),
    ("canary", re.compile(r"(?i)\b(?:canary|cohortguard-canary)[-_:#A-Z0-9]*\b")),
    ("name", re.compile(r"\b[A-Z][a-z]{2,}(?:[-'][A-Z]?[a-z]+)?\s+[A-Z][a-z]{2,}(?:[-'][A-Z]?[a-z]+)?\b")),
)

REDACTION = "[REDACTED_IDENTIFIER]"


def identifier_kinds(text: str, *, include_names: bool = True) -> set[str]:
    """Return identifier categories found in text without returning matched values."""
    if not isinstance(text, str):
        return {"invalid_text"}
    return {
        kind
        for kind, pattern in _PATTERNS
        if (include_names or kind != "name") and pattern.search(text)
    }


def contains_identifier(value: Any, *, include_names: bool = True) -> bool:
    """Scan all strings in a JSON-like value for identifier patterns."""
    if isinstance(value, str):
        return bool(identifier_kinds(value, include_names=include_names))
    if isinstance(value, dict):
        return any(
            contains_identifier(key, include_names=include_names)
            or contains_identifier(item, include_names=include_names)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(contains_identifier(item, include_names=include_names) for item in value)
    return False


def assert_deidentified(value: Any, *, include_names: bool = True) -> None:
    """Reject a boundary value containing an identifier without echoing it."""
    if contains_identifier(value, include_names=include_names):
        raise ValueError("identifier_detected")


def scrub_text(text: str, known_values: Iterable[str] = ()) -> str:
    """Replace known identifiers and generic identifier patterns deterministically."""
    scrubbed = text
    values = sorted({value for value in known_values if value}, key=len, reverse=True)
    for value in values:
        scrubbed = re.sub(re.escape(value), REDACTION, scrubbed, flags=re.IGNORECASE)
    for _kind, pattern in _PATTERNS:
        scrubbed = pattern.sub(REDACTION, scrubbed)
    return re.sub(rf"(?:{re.escape(REDACTION)}\s*)+", REDACTION + " ", scrubbed).strip()


def safe_json(value: Any, *, include_names: bool = True) -> str:
    """Serialize only after the value passes the identifier boundary check."""
    assert_deidentified(value, include_names=include_names)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
