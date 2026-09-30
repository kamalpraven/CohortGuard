"""Local Doctor Agent logic. Raw doctor text never leaves this process."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cohortguard_coordinator.identifiers import assert_deidentified, scrub_text

_NCT = re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)
_AGE_BAND = re.compile(r"\b(\d0)-(\d9)\b")


@dataclass(frozen=True)
class LocalContext:
    patients: list[dict[str, Any]]
    canaries: dict[str, list[dict[str, Any]]]

    @classmethod
    def load(cls, patient_path: Path, canary_path: Path) -> "LocalContext":
        patient_doc = json.loads(patient_path.read_text(encoding="utf-8"))
        canaries = json.loads(canary_path.read_text(encoding="utf-8"))
        return cls(patient_doc["patients"], canaries)

    def known_identifiers(self) -> set[str]:
        values: set[str] = set()
        def add_record(record: dict[str, Any]) -> None:
            values.update(str(record.get(key, "")) for key in ("name", "mrn", "dob"))
            name = str(record.get("name", "")).strip()
            parts = name.split()
            if len(parts) >= 2:
                first = parts[0]
                last = parts[-1]
                values.update({first, f"{last}, {first}", f"{last} {first}"})

        for patient in self.patients:
            add_record(patient)
        for records in self.canaries.values():
            for record in records:
                add_record(record)
        values.discard("")
        return values


def scrub_doctor_question(question: str, local: LocalContext) -> str:
    """Scrub clinic records, all canaries, dates, IDs, and person-name patterns."""
    return scrub_text(question, local.known_identifiers())


def _cohorts(question: str) -> list[str]:
    lowered = question.lower()
    aliases = {
        "sglt2": "sglt2_inhibitor",
        "sglt2_inhibitor": "sglt2_inhibitor",
        "sulfonylurea": "sulfonylurea",
        "metformin": "metformin",
        "basal insulin": "basal_insulin",
        "glp-1": "glp1_ra",
        "dpp-4": "dpp4_inhibitor",
    }
    found: list[str] = []
    for phrase, code in aliases.items():
        if phrase in lowered and code not in found:
            found.append(code)
    return found or ["sglt2_inhibitor", "sulfonylurea"]


def structure_coordinator_request(scrubbed_question: str) -> dict[str, Any]:
    """Convert scrubbed text into one fixed coordinator workflow request."""
    assert_deidentified(scrubbed_question)
    lowered = scrubbed_question.lower()
    request_id = f"doctor-{uuid.uuid4().hex[:12]}"
    base: dict[str, Any] = {
        "sanitized": True,
        "request_id": request_id,
        "question": scrubbed_question,
    }
    nct_match = _NCT.search(scrubbed_question)
    if "feasib" in lowered or (nct_match and "eligible" in lowered):
        if not nct_match:
            raise ValueError("A trial NCT ID is required for feasibility.")
        return {**base, "workflow": "site_feasibility", "nct_id": nct_match.group(0).upper()}
    if "trial" in lowered or "trials" in lowered:
        condition = "type 2 diabetes" if ("type 2" in lowered or "t2d" in lowered) else lowered[:120]
        return {
            **base,
            "workflow": "trial_pipeline",
            "condition": condition,
            "outcome_keywords": ["readmission", "hospitalization"],
        }
    if "cohort" in lowered or "readmission" in lowered or "readmit" in lowered:
        filters: dict[str, str] = {}
        if "type 2" in lowered or "t2d" in lowered:
            filters["diagnosis"] = "T2D"
        age = _AGE_BAND.search(scrubbed_question)
        if age:
            filters["age_band"] = age.group(0)
        if " female" in f" {lowered}":
            filters["sex"] = "FEMALE"
        elif " male" in f" {lowered}":
            filters["sex"] = "MALE"
        return {
            **base,
            "workflow": "cohort_question",
            "cohort_field": "medication",
            "cohorts": _cohorts(scrubbed_question),
            "outcome": "readmit_30d",
            "filters": filters,
        }
    condition = "type 2 diabetes" if ("type 2" in lowered or "t2d" in lowered) else lowered[:120]
    return {
        **base,
        "workflow": "trial_pipeline",
        "condition": condition,
        "outcome_keywords": ["readmission", "hospitalization"],
    }


def prepare_coordinator_request(question: str, local: LocalContext) -> dict[str, Any]:
    """Scrub first, then structure and independently verify the outbound value."""
    scrubbed = scrub_doctor_question(question, local)
    request = structure_coordinator_request(scrubbed)
    assert_deidentified(request)
    return request


def find_local_patient(question: str, local: LocalContext) -> dict[str, Any] | None:
    """Find one locally named patient without returning identifiers externally."""
    lowered = question.lower()
    matches = [
        patient
        for patient in local.patients
        if any(str(patient.get(key, "")).lower() in lowered for key in ("name", "mrn", "dob"))
    ]
    unique = {patient["mrn"]: patient for patient in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def local_patient_answer(question: str, local: LocalContext) -> dict[str, Any] | None:
    """Return an exact checklist locally; this function performs no I/O."""
    patient = find_local_patient(question, local)
    nct = _NCT.search(question)
    if patient is None or nct is None or not any(word in question.lower() for word in ("eligible", "eligibility", "checklist")):
        return None
    from clinic_a.clinic import handle_clinic_a

    return handle_clinic_a(
        {"template": "patient_checklist", "mrn": patient["mrn"], "nct_id": nct.group(0).upper()},
        local.patients,
    )
