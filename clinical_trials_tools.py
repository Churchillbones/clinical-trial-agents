"""ClinicalTrials.gov API v2 tool implementations."""

import httpx

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
    """Search ClinicalTrials.gov for studies matching criteria."""
    params: dict[str, str | int] = {"pageSize": min(page_size, 20)}
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
    return resp.json()


def get_study_details(nct_id: str) -> dict:
    """Retrieve the complete record for a single study by NCT ID."""
    resp = httpx.get(f"{CTG_BASE_URL}/studies/{nct_id}", timeout=15.0)
    resp.raise_for_status()
    return resp.json()


def check_eligibility(nct_id: str) -> dict:
    """Retrieve just the eligibility section for a study."""
    resp = httpx.get(
        f"{CTG_BASE_URL}/studies/{nct_id}",
        params={"fields": "EligibilityCriteria,EligibilityModule"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    module = data.get("protocolSection", {}).get("eligibilityModule", {})
    return {
        "nctId": nct_id,
        "eligibilityCriteria": module.get("eligibilityCriteria", ""),
        "sex": module.get("sex", "ALL"),
        "minimumAge": module.get("minimumAge", "N/A"),
        "maximumAge": module.get("maximumAge", "N/A"),
        "healthyVolunteers": module.get("healthyVolunteers", "No"),
        "stdAges": module.get("stdAges", []),
    }


def count_studies(
    term: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    status: str | None = None,
) -> int:
    """Count studies matching the given criteria."""
    params: dict[str, str] = {}
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

    resp = httpx.get(f"{CTG_BASE_URL}/stats/size", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("totalCount", 0)


def list_enum_values(enum_name: str) -> list[str]:
    """List valid values for a ClinicalTrials.gov coded field."""
    resp = httpx.get(f"{CTG_BASE_URL}/studies/enums", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    return data.get(enum_name, [])


def generate_trial_summary(nct_id: str, patient_context: str | None = None) -> dict:
    """Build a structured, patient-friendly summary payload for a study.

    Fetches the full study record and extracts the sections a clinician needs
    to share with a patient. The agent renders this into plain-language prose.
    """
    study = get_study_details(nct_id)
    protocol = study.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    desc = protocol.get("descriptionModule", {})
    elig = protocol.get("eligibilityModule", {})
    status = protocol.get("statusModule", {})
    contacts_locations = protocol.get("contactsLocationsModule", {})
    resolved_id = ident.get("nctId", nct_id)

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
            "centralContacts": contacts_locations.get("centralContacts", []),
            "locations": contacts_locations.get("locations", []),
        },
        "link": f"https://clinicaltrials.gov/study/{resolved_id}",
        "patientContext": patient_context,
    }


def draft_referral_message(
    nct_id: str,
    patient_summary: str,
    clinician_name: str | None = None,
    clinician_facility: str | None = None,
) -> dict:
    """Draft a de-identified referral message to a study coordinator.

    Returns a structured draft for the clinician to review and send. This
    function never transmits anything.
    """
    study_url = f"https://clinicaltrials.gov/study/{nct_id}"
    signature_name = clinician_name or "[Referring Clinician]"
    signature_facility = clinician_facility or "[VA Facility]"

    message_body = (
        f"To the Study Coordinator for {nct_id},\n\n"
        f"I am a clinician at {signature_facility} writing to inquire about "
        f"enrollment in your study ({nct_id}, {study_url}).\n\n"
        f"I am caring for a patient who may be a candidate. De-identified "
        f"clinical context: {patient_summary}\n\n"
        f"Could you share the current enrollment status, key eligibility "
        f"requirements, and the preferred process for referring a patient?\n\n"
        f"Thank you,\n"
        f"{signature_name}\n"
        f"{signature_facility}"
    )

    return {
        "nctId": nct_id,
        "messageBody": message_body,
        "disclaimer": (
            "Review before sending. Do NOT include PHI (patient name, DOB, SSN, "
            "MRN, or other identifiable details) in this message. Share "
            "identifiable information only through approved secure channels."
        ),
    }
