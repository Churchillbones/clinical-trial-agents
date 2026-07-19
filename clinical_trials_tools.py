"""ClinicalTrials.gov API v2 tool implementations with clinical safety receipts."""

from __future__ import annotations

import httpx

from clinical_trial_harness import (
    build_eligibility_receipt,
    build_search_receipt,
    clinical_trials_anchor,
    prioritize_va_locations,
    referral_authority_envelope,
    require_deidentified_text,
    utc_now_iso,
    validate_nct_id,
)

CTG_BASE_URL = "https://clinicaltrials.gov/api/v2"


def search_studies(
    term: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    location: str | None = None,
    sponsor: str | None = None,
    age: str | None = None,
    sex: str | None = None,
    status: str | None = "RECRUITING",
    phase: str | None = None,
    page_size: int = 10,
) -> dict:
    """Search ClinicalTrials.gov and attach a bounded search proof receipt."""
    try:
        bounded_page_size = max(1, min(int(page_size), 20))
    except (TypeError, ValueError):
        bounded_page_size = 10

    params: dict[str, str | int] = {"pageSize": bounded_page_size}
    if term:
        params["query.term"] = term
    if condition:
        params["query.cond"] = condition
    if intervention:
        params["query.intr"] = intervention
    if location:
        params["query.locn"] = location
    if sponsor:
        params["query.spons"] = sponsor
    if status:
        params["filter.overallStatus"] = status
    if phase:
        params["filter.advanced"] = f"AREA[Phase]{phase}"

    resp = httpx.get(f"{CTG_BASE_URL}/studies", params=params, timeout=15.0)
    resp.raise_for_status()
    result = resp.json()
    result["_harness"] = build_search_receipt(
        query={
            "term": term,
            "condition": condition,
            "intervention": intervention,
            "location": location,
            "sponsor": sponsor,
            "age": age,
            "sex": sex,
            "status": status,
            "phase": phase,
            "pageSize": bounded_page_size,
        },
        result=result,
    )
    return result


def get_study_details(nct_id: str) -> dict:
    """Retrieve the complete record for a single study by NCT ID."""
    normalized_requested_id = validate_nct_id(nct_id)
    resp = httpx.get(
        f"{CTG_BASE_URL}/studies/{normalized_requested_id}", timeout=15.0
    )
    resp.raise_for_status()
    result = resp.json()
    protocol = result.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    resolved_id = ident.get("nctId") or normalized_requested_id
    try:
        normalized_id = validate_nct_id(resolved_id)
    except ValueError:
        normalized_id = str(resolved_id)
    result["_harness"] = {
        "source": {
            "system": "ClinicalTrials.gov API v2",
            "retrievedAt": utc_now_iso(),
        },
        "nctId": normalized_id,
        "assessmentBoundary": (
            "Study details are source facts. Any patient-specific fit remains a "
            "clinician-reviewed interpretation."
        ),
    }
    return result


def check_eligibility(nct_id: str) -> dict:
    """Retrieve eligibility source fields without determining eligibility."""
    normalized_id = validate_nct_id(nct_id)
    resp = httpx.get(
        f"{CTG_BASE_URL}/studies/{normalized_id}",
        params={"fields": "EligibilityCriteria,EligibilityModule"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    protocol = data.get("protocolSection") or {}
    module = protocol.get("eligibilityModule") or {}
    return {
        "nctId": normalized_id,
        "eligibilityCriteria": module.get("eligibilityCriteria", ""),
        "sex": module.get("sex", "ALL"),
        "minimumAge": module.get("minimumAge", "N/A"),
        "maximumAge": module.get("maximumAge", "N/A"),
        "healthyVolunteers": module.get("healthyVolunteers", "No"),
        "stdAges": module.get("stdAges", []),
        "_harness": build_eligibility_receipt(normalized_id),
    }


def count_studies(
    term: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    status: str | None = None,
) -> int:
    """Count studies matching the given criteria."""
    params: dict[str, str | int] = {}
    if term:
        params["query.term"] = term
    if condition:
        params["query.cond"] = condition
    if intervention:
        params["query.intr"] = intervention
    if sponsor:
        params["query.spons"] = sponsor
    if status:
        params["filter.overallStatus"] = status
    params["countTotal"] = "true"
    params["pageSize"] = 1

    resp = httpx.get(f"{CTG_BASE_URL}/studies", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("totalCount", 0)


def list_enum_values(enum_name: str) -> list[str]:
    """List valid values for a ClinicalTrials.gov coded field."""
    resp = httpx.get(f"{CTG_BASE_URL}/studies/enums", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    return data.get(enum_name, [])


def generate_trial_summary(nct_id: str, patient_context: str | None = None) -> dict:
    """Build a source-grounded, patient-friendly summary payload for a study."""
    normalized_id = validate_nct_id(nct_id)
    require_deidentified_text(patient_context, field_name="patient_context")

    study = get_study_details(normalized_id)
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    desc = protocol.get("descriptionModule") or {}
    elig = protocol.get("eligibilityModule") or {}
    status = protocol.get("statusModule") or {}
    contacts_locations = protocol.get("contactsLocationsModule") or {}
    resolved_id = validate_nct_id(ident.get("nctId") or normalized_id)
    locations = prioritize_va_locations(contacts_locations.get("locations") or [])

    return {
        "nctId": resolved_id,
        "title": ident.get("briefTitle", ""),
        "plainLanguageSummary": desc.get("briefSummary", ident.get("briefTitle", "")),
        "whatParticipationInvolves": desc.get("detailedDescription", ""),
        "eligibilityHighlights": {
            "criteria": elig.get("eligibilityCriteria", ""),
            "sex": elig.get("sex", "ALL"),
            "minimumAge": elig.get("minimumAge", "N/A"),
            "maximumAge": elig.get("maximumAge", "N/A"),
            "healthyVolunteers": elig.get("healthyVolunteers", "No"),
        },
        "overallStatus": status.get("overallStatus", ""),
        "contactInfo": {
            "centralContacts": contacts_locations.get("centralContacts") or [],
            "locations": locations,
        },
        "link": f"https://clinicaltrials.gov/study/{resolved_id}",
        "patientContext": patient_context,
        "_harness": {
            "patientContextStatus": (
                "validated_deidentified" if patient_context else "not_supplied"
            ),
            "assessmentBoundary": (
                "This handout describes the study and visible criteria; it does "
                "not establish patient eligibility."
            ),
            "evidenceAnchors": [
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.descriptionModule.briefSummary",
                ),
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.eligibilityModule.eligibilityCriteria",
                ),
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.contactsLocationsModule.locations",
                ),
            ],
        },
    }


def draft_referral_message(
    nct_id: str,
    patient_summary: str,
    clinician_name: str | None = None,
    clinician_facility: str | None = None,
) -> dict:
    """Draft a de-identified referral message without transmitting anything."""
    normalized_id = validate_nct_id(nct_id)
    require_deidentified_text(patient_summary, field_name="patient_summary")

    study_url = f"https://clinicaltrials.gov/study/{normalized_id}"
    signature_name = clinician_name or "[Referring Clinician]"
    signature_facility = clinician_facility or "[VA Facility]"

    message_body = (
        f"To the Study Coordinator for {normalized_id},\n\n"
        f"I am a clinician at {signature_facility} writing to inquire about "
        f"enrollment in your study ({normalized_id}, {study_url}).\n\n"
        f"I am caring for a patient who may be a candidate. De-identified "
        f"clinical context: {patient_summary}\n\n"
        f"Could you share the current enrollment status, key eligibility "
        f"requirements, and the preferred process for referring a patient?\n\n"
        f"Thank you,\n"
        f"{signature_name}\n"
        f"{signature_facility}"
    )

    return {
        "nctId": normalized_id,
        "messageBody": message_body,
        "disclaimer": (
            "Review before sending. Do NOT include PHI (patient name, DOB, SSN, "
            "MRN, or other identifiable details) in this message. Share "
            "identifiable information only through approved secure channels."
        ),
        "_harness": {
            "phiValidation": "passed_deterministic_screen",
            "authority": referral_authority_envelope(),
        },
    }
