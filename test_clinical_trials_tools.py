"""Tests for ClinicalTrials.gov API tool functions."""

import pytest
import httpx
import respx

from clinical_trials_tools import (
    check_eligibility,
    count_studies,
    get_study_details,
    list_enum_values,
    search_studies,
)

MOCK_SEARCH_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00000001",
                    "briefTitle": "Test Study",
                },
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"]},
                "conditionsModule": {"conditions": ["PTSD"]},
                "contactsLocationsModule": {
                    "locations": [
                        {
                            "facility": "Portland VAMC",
                            "city": "Portland",
                            "state": "Oregon",
                            "country": "United States",
                        }
                    ]
                },
            }
        }
    ],
    "totalCount": 1,
}


@respx.mock
def test_search_studies_by_condition():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    result = search_studies(condition="PTSD", status="RECRUITING")
    assert result["totalCount"] == 1
    assert (
        result["studies"][0]["protocolSection"]["identificationModule"]["nctId"]
        == "NCT00000001"
    )


@respx.mock
def test_search_studies_defaults_to_recruiting():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    search_studies(condition="diabetes")
    assert "RECRUITING" in str(route.calls[0].request.url)


@respx.mock
def test_search_studies_with_location():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    search_studies(condition="PTSD", location="Portland VA")
    assert "Portland" in str(route.calls[0].request.url)


MOCK_STUDY_DETAIL = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT00000001",
            "briefTitle": "Test Study",
        },
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: Age 18-65\nExclusion: Pregnant",
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "65 Years",
        },
    }
}


@respx.mock
def test_get_study_details_returns_full_record():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = get_study_details("NCT00000001")
    assert result["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"
    assert "eligibilityModule" in result["protocolSection"]


@respx.mock
def test_get_study_details_invalid_nct_raises():
    respx.get("https://clinicaltrials.gov/api/v2/studies/INVALID").mock(
        return_value=httpx.Response(404, json={"error": "Not found"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        get_study_details("INVALID")


@respx.mock
def test_check_eligibility_returns_eligibility_fields():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = check_eligibility("NCT00000001")
    assert "eligibilityCriteria" in result
    assert result["minimumAge"] == "18 Years"
    assert result["maximumAge"] == "65 Years"
    assert result["sex"] == "ALL"


@respx.mock
def test_count_studies_returns_total():
    respx.get("https://clinicaltrials.gov/api/v2/stats/size").mock(
        return_value=httpx.Response(200, json={"totalCount": 42})
    )
    result = count_studies(condition="PTSD", status="RECRUITING")
    assert result == 42


@respx.mock
def test_count_studies_no_filters():
    respx.get("https://clinicaltrials.gov/api/v2/stats/size").mock(
        return_value=httpx.Response(200, json={"totalCount": 500000})
    )
    result = count_studies()
    assert result == 500000


@respx.mock
def test_list_enum_values_returns_values():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(
            200,
            json={
                "Phase": ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"],
                "OverallStatus": ["RECRUITING", "COMPLETED"],
            },
        )
    )
    result = list_enum_values("Phase")
    assert "PHASE2" in result


@respx.mock
def test_list_enum_values_unknown_enum():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(200, json={"Phase": ["PHASE1"]})
    )
    result = list_enum_values("Nonexistent")
    assert result == []
