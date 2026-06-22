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
