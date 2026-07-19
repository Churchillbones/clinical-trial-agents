"""Deterministic safety and evidence harness for the Clinical Trials agent.

This module keeps clinical job boundaries, PHI checks, evidence references, and
mutation authority outside the language-model prompt. The model can reason over
the returned structures, but it cannot silently relax these contracts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

HARNESS_VERSION = "1.0.0"
SEARCH_JOB_ID = "find_recruiting_clinical_trials"

_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "date_of_birth",
        re.compile(
            r"\b(?:dob|date\s+of\s+birth)\s*[:=]?\s*"
            r"(?:\d{1,2}[/-]){2}\d{2,4}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "medical_record_identifier",
        re.compile(
            r"\b(?:mrn|medical\s+record\s+number|patient\s+id)\s*[:#=]?\s*"
            r"[A-Za-z0-9-]{4,}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "email_address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "phone_number",
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    ),
    (
        "labeled_patient_name",
        re.compile(
            r"\b(?:patient|veteran)\s+name\s*[:=]\s*[A-Za-z][A-Za-z' -]{2,}",
            re.IGNORECASE,
        ),
    ),
)


def utc_now_iso() -> str:
    """Return an RFC 3339 timestamp for source-freshness receipts."""
    return datetime.now(timezone.utc).isoformat()


def validate_nct_id(nct_id: Any) -> str:
    """Normalize and validate a ClinicalTrials.gov identifier."""
    normalized = str(nct_id or "").strip().upper()
    if not re.fullmatch(r"NCT\d{8}", normalized):
        raise ValueError("nct_id must match the format NCT########")
    return normalized


def detect_potential_phi(text: str | None) -> list[str]:
    """Return PHI categories detected by conservative deterministic patterns.

    This is a guardrail, not a full de-identification service. It intentionally
    reports categories only and never echoes the suspected identifier.
    """
    if not text:
        return []
    return [name for name, pattern in _PHI_PATTERNS if pattern.search(text)]


def require_deidentified_text(text: str | None, *, field_name: str) -> None:
    """Reject common direct identifiers before model-visible tool output."""
    findings = detect_potential_phi(text)
    if findings:
        categories = ", ".join(sorted(findings))
        raise ValueError(
            f"Potential PHI detected in {field_name}: {categories}. "
            "Remove direct identifiers and retry with de-identified context."
        )


def clinical_trials_anchor(nct_id: str, source_path: str) -> str:
    """Build a stable evidence reference to a ClinicalTrials.gov response field."""
    return f"ctg:{validate_nct_id(nct_id)}:{source_path}"


def build_search_receipt(
    *,
    query: dict[str, Any],
    result: dict[str, Any],
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Describe what the search proved and what remains for clinician review."""
    studies = result.get("studies") or []
    warnings: list[str] = []
    if query.get("age") or query.get("sex"):
        warnings.append(
            "Age and sex were supplied as patient context but were not applied as "
            "server-side ClinicalTrials.gov filters; review each study's criteria."
        )

    recovery_options: list[str] = []
    if not studies:
        if query.get("location"):
            recovery_options.append("Repeat without the location filter or use a broader region.")
        recovery_options.extend(
            [
                "Try a related condition or intervention term.",
                "Include NOT_YET_RECRUITING studies if appropriate.",
            ]
        )

    return {
        "harnessVersion": HARNESS_VERSION,
        "jobId": SEARCH_JOB_ID,
        "jobVersion": "1.0",
        "source": {
            "system": "ClinicalTrials.gov API v2",
            "retrievedAt": retrieved_at or utc_now_iso(),
        },
        "query": {key: value for key, value in query.items() if value not in (None, "")},
        "returnedCount": len(studies),
        "totalCount": result.get("totalCount"),
        "assessmentBoundary": (
            "Search results are candidates for clinician review. They are not an "
            "eligibility determination or trial recommendation."
        ),
        "warnings": warnings,
        "recoveryOptions": recovery_options,
    }


def build_eligibility_receipt(nct_id: str) -> dict[str, Any]:
    """Return evidence paths and allowed eligibility classifications."""
    normalized = validate_nct_id(nct_id)
    return {
        "harnessVersion": HARNESS_VERSION,
        "assessmentStatus": "clinician_review_required",
        "allowedClassifications": [
            "potentially_satisfied",
            "potential_conflict",
            "not_assessable",
            "not_applicable",
        ],
        "prohibitedConclusions": ["eligible", "ineligible", "recommended_trial"],
        "evidenceAnchors": [
            clinical_trials_anchor(
                normalized,
                "protocolSection.eligibilityModule.eligibilityCriteria",
            ),
            clinical_trials_anchor(normalized, "protocolSection.eligibilityModule.sex"),
            clinical_trials_anchor(
                normalized,
                "protocolSection.eligibilityModule.minimumAge",
            ),
            clinical_trials_anchor(
                normalized,
                "protocolSection.eligibilityModule.maximumAge",
            ),
        ],
    }


def prioritize_va_locations(
    locations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return a stable copy with VA facilities first, preserving source order."""
    if not locations:
        return []

    def is_va(location: dict[str, Any]) -> bool:
        facility = str(location.get("facility") or "").lower()
        return any(
            marker in facility
            for marker in ("vamc", "veterans affairs", "va medical", " va ")
        ) or facility.startswith("va ")

    valid_locations = [location for location in locations if isinstance(location, dict)]
    return sorted(valid_locations, key=lambda location: 0 if is_va(location) else 1)


def referral_authority_envelope() -> dict[str, Any]:
    """Describe the non-transmitting authority boundary for referral drafts."""
    return {
        "reviewState": "proposed",
        "requiresClinicianReview": True,
        "permittedActions": ["copy", "edit", "dismiss"],
        "prohibitedActions": ["send", "submit", "contact_site", "enroll_patient"],
        "transmitted": False,
    }
