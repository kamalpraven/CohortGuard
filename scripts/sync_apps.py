"""Synchronize canonical shared assets into Flower app build directories.

Run ``python scripts/sync_apps.py`` to update generated copies, or add
``--check`` to fail without writing when a copy is missing or differs.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "shared"
CLINICS = ROOT / "clinic-agents"
CLINIC_CORE = CLINICS / "clinic_core"
CLINIC_APPS = (CLINICS / "clinic-a-agent", CLINICS / "clinic-b-agent")
COORDINATOR = ROOT / "coordinator"
RESEARCH_APP = ROOT / "research-agent"
FL_APP = ROOT / "fl-readmission"


def _asset_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    allowlist_sources = (
        RESEARCH_APP / "shared",
        CLINICS / "shared",
        COORDINATOR / "shared",
        FL_APP / "shared",
        *(app / "shared" for app in CLINIC_APPS),
    )
    for destination in allowlist_sources:
        pairs.extend(
            [
                (CANONICAL / "__init__.py", destination / "__init__.py"),
                (CANONICAL / "allowlist.py", destination / "allowlist.py"),
            ]
        )

    criteria_destinations = (
        RESEARCH_APP / "cache",
        CLINICS / "cache",
        CLINIC_CORE / "trial_cache",
        COORDINATOR / "cache",
        *(app / "clinic_core" / "trial_cache" for app in CLINIC_APPS),
    )
    for source in sorted((CANONICAL / "criteria").glob("criteria_*.json")):
        pairs.extend((source, destination / source.name) for destination in criteria_destinations)

    for app in (*CLINIC_APPS, COORDINATOR):
        for source in sorted(CLINIC_CORE.glob("*.py")):
            if source.name != "generate_data.py":
                pairs.append((source, app / "clinic_core" / source.name))

    for package in ("research", "research_agent"):
        for source in sorted((RESEARCH_APP / package).glob("*.py")):
            pairs.append((source, COORDINATOR / package / source.name))
    for source in sorted((RESEARCH_APP / "cache").glob("*.json")):
        pairs.append((source, COORDINATOR / "cache" / source.name))
    return pairs


def _unexpected_paths() -> list[Path]:
    """Files deliberately excluded from runtime app bundles."""
    return [app / "clinic_core" / "generate_data.py" for app in (*CLINIC_APPS, COORDINATOR)]


def find_drift() -> list[str]:
    """Return descriptions of missing, different, or unexpected generated files."""
    drift: list[str] = []
    for source, destination in _asset_pairs():
        relative = destination.relative_to(ROOT)
        if not destination.is_file():
            drift.append(f"missing: {relative}")
        elif not filecmp.cmp(source, destination, shallow=False):
            drift.append(f"different: {relative}")
    drift.extend(
        f"unexpected: {path.relative_to(ROOT)}" for path in _unexpected_paths() if path.exists()
    )
    return drift


def sync() -> None:
    """Copy canonical assets and clinic core into app-local build paths."""
    for source, destination in _asset_pairs():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or not filecmp.cmp(source, destination, shallow=False):
            shutil.copy2(source, destination)
    for path in _unexpected_paths():
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args()
    if args.check:
        drift = find_drift()
        if drift:
            print("Shared app copies are out of sync:")
            print("\n".join(f"- {item}" for item in drift))
            return 1
        print("Shared app copies are in sync.")
        return 0
    sync()
    print("Synchronized canonical assets into research, clinic, and coordinator apps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
