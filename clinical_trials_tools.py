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
