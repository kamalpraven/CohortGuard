"""Clinic B request handler: every released aggregate uses the privacy gate."""

from __future__ import annotations

from typing import Any

from clinic_core.aggregate import handle_gated_aggregate
from clinic_core.privacy import PrivacyGate


def handle_clinic_b(
    req: dict[str, Any], patients: list[dict[str, Any]], gate: PrivacyGate
) -> dict[str, Any]:
    """Handle a Clinic B request using the shared gated aggregate path."""
    return handle_gated_aggregate(req, patients, gate, clinic="B")
