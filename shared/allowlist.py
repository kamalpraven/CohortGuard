"""Shared eligibility allowlist for CohortGuard clinic/research agents.

Contract with M2 clinic agents:
- age values are in years.
- hba1c values are in percent (%).
- egfr values are in mL/min/1.73m².
- bmi values are in kg/m².
- sex values use ClinicalTrials.gov-style uppercase enums in SEX_VALUES.
- diagnosis values use DIAGNOSIS_CODES.
- medication values use MEDICATION_CLASSES.

Any field, operator, value-type, enum, or unit change requires agreement with M2
because clinic query templates must match this file exactly.
"""

ALLOWLIST = {
    # field:              (value type, allowed ops)
    "age":                ("number", {"between", "gte", "lte"}),
    "sex":                ("enum",   {"eq"}),
    "diagnosis":          ("code",   {"eq", "in"}),
    "hba1c":              ("number", {"between", "gte", "lte"}),
    "egfr":               ("number", {"between", "gte", "lte"}),
    "bmi":                ("number", {"between", "gte", "lte"}),
    "current_medication": ("code",   {"has", "not_has"}),
    "prior_medication":   ("code",   {"has", "not_has"}),
}

SEX_VALUES = {"FEMALE", "MALE"}

DIAGNOSIS_CODES = {"T2D", "T1D", "secondary_diabetes", "other_specific_diabetes"}
MEDICATION_CLASSES = {"metformin", "sglt2_inhibitor", "basal_insulin", "glp1_ra", "dpp4_inhibitor", "sulfonylurea"}
