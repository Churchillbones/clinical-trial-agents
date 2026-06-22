"""Tests for ClinicalTrials.gov API tool functions."""

import pytest
import httpx
import respx

from clinical_trials_tools import search_studies

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
