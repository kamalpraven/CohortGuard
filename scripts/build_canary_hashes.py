from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_SALT = "cohortguard-phase-2-canary-hash-v1"
_TOKEN = re.compile(r"[a-z0-9]+")


def normalize_tokens(value: str) -> list[str]:
    return _TOKEN.findall(value.casefold())


def normalized(value: str) -> str:
    return " ".join(normalize_tokens(value))


def digest(salt: str, value: str) -> str:
    return hashlib.sha256(f"{salt}\0{value}".encode("utf-8")).hexdigest()


def canary_values(document: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for records in document.values():
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            for key in ("name", "mrn", "dob"):
                value = str(record.get(key, "")).strip()
                norm = normalized(value)
                if norm:
                    values.add(norm)
    return values


def build(input_path: Path, output_path: Path, salt: str) -> None:
    document = json.loads(input_path.read_text(encoding="utf-8"))
    hashes = sorted(digest(salt, value) for value in canary_values(document))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"version": 1, "salt": salt, "hashes": hashes}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build salted canary hash allow-deny data.")
    parser.add_argument("--input", type=Path, default=root / "clinic-agents/data/canaries.json")
    parser.add_argument("--output", type=Path, default=root / "coordinator/cohortguard_coordinator/canary_hashes.json")
    parser.add_argument("--salt", default=DEFAULT_SALT)
    args = parser.parse_args()
    build(args.input, args.output, args.salt)


if __name__ == "__main__":
    main()
