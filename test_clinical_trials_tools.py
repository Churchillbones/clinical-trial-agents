"""Tests for ClinicalTrials.gov API tool functions."""

import pytest
import httpx
import respx

from clinical_trials_tools import (
    ClinicalTrialsApiError,
    check_eligibility,
    count_studies,
    draft_referral_message,
    generate_trial_summary,
    get_study_details,
    list_enum_values,
    search_studies,
)
from tests_fixtures.ctg_isolation import ctg_isolation  # noqa: F401  (autouse)
from tests_fixtures.ctg_v2_payloads import enums_payload

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
async def test_search_studies_by_condition():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    result = await search_studies(condition="PTSD", status="RECRUITING")
    assert result["totalCount"] == 1
    assert (
        result["studies"][0]["protocolSection"]["identificationModule"]["nctId"]
        == "NCT00000001"
    )


@respx.mock
async def test_search_studies_defaults_to_recruiting():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    await search_studies(condition="diabetes")
    assert route.calls[0].request.url.params["filter.overallStatus"] == "RECRUITING"


@respx.mock
async def test_search_studies_with_location():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    await search_studies(condition="PTSD", location="Portland VA")
    assert route.calls[0].request.url.params["query.locn"] == "Portland VA"


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
async def test_get_study_details_returns_protocol_record():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = await get_study_details("NCT00000001")
    assert result["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"
    assert "eligibilityModule" in result["protocolSection"]


@respx.mock
async def test_get_study_details_invalid_format_raises_before_any_request():
    route = respx.get(url__startswith="https://clinicaltrials.gov/api/v2/studies")
    with pytest.raises(ValueError, match="NCT########"):
        await get_study_details("INVALID")
    assert route.call_count == 0


@respx.mock
async def test_get_study_details_unknown_id_raises_not_found():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies/NCT09999999").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    with pytest.raises(ClinicalTrialsApiError) as excinfo:
        await get_study_details("NCT09999999")
    assert excinfo.value.status_code == 404
    assert excinfo.value.retryable is False
    assert str(excinfo.value) == "Study NCT09999999 was not found on ClinicalTrials.gov"
    assert route.call_count == 1  # a 404 is never retried


@respx.mock
async def test_check_eligibility_returns_eligibility_fields():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = await check_eligibility("NCT00000001")
    assert "eligibilityCriteria" in result
    assert result["minimumAge"] == "18 Years"
    assert result["maximumAge"] == "65 Years"
    assert result["sex"] == "ALL"


@respx.mock
async def test_count_studies_returns_total():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json={"totalCount": 42})
    )
    result = await count_studies(condition="PTSD", status="RECRUITING")
    assert result == 42


@respx.mock
async def test_count_studies_no_filters():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json={"totalCount": 500000})
    )
    result = await count_studies()
    assert result == 500000


@respx.mock
async def test_list_enum_values_returns_values():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(200, json=enums_payload())
    )
    result = await list_enum_values("Phase")
    assert result == ["NA", "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"]


@respx.mock
async def test_list_enum_values_unknown_enum():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(200, json=enums_payload())
    )
    result = await list_enum_values("Nonexistent")
    assert result == []


@respx.mock
async def test_search_studies_api_timeout(ctg_isolation):
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        side_effect=httpx.ConnectTimeout("Timeout")
    )
    with pytest.raises(ClinicalTrialsApiError) as excinfo:
        await search_studies(condition="PTSD")
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code is None
    assert isinstance(excinfo.value.__cause__, httpx.ConnectTimeout)
    assert route.call_count == 3
    assert len(ctg_isolation.sleeps) == 2


@respx.mock
async def test_search_studies_empty_results():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json={"studies": [], "totalCount": 0})
    )
    result = await search_studies(condition="xyznonexistent")
    assert result["totalCount"] == 0
    assert result["studies"] == []


@respx.mock
async def test_generate_trial_summary_returns_plain_language():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = await generate_trial_summary("NCT00000001")
    assert "nctId" in result
    assert "plainLanguageSummary" in result
    assert "contactInfo" in result


@respx.mock
async def test_generate_trial_summary_with_patient_context():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = await generate_trial_summary(
        "NCT00000001", patient_context="58-year-old male with PTSD"
    )
    assert result["patientContext"] == "58-year-old male with PTSD"


async def test_draft_referral_message_structure():
    result = await draft_referral_message(
        nct_id="NCT00000001",
        patient_summary="58-year-old male veteran, treatment-resistant PTSD, failed PE + sertraline",
        clinician_name="Dr. Jane Smith",
        clinician_facility="Portland VAMC",
    )
    assert result["nctId"] == "NCT00000001"
    assert "NCT00000001" in result["messageBody"]
    assert "Dr. Jane Smith" in result["messageBody"]
    assert "Portland VAMC" in result["messageBody"]


async def test_draft_referral_message_no_phi_warning():
    result = await draft_referral_message(
        nct_id="NCT00000001",
        patient_summary="62-year-old female with diabetes",
    )
    assert "disclaimer" in result
    assert (
        "PHI" in result["disclaimer"]
        or "identifiable" in result["disclaimer"].lower()
    )
