"""Compatibility tests for harnessed ClinicalTrials.gov tools."""

import httpx
import pytest

import clinical_trials_tools as tools
from tests_fixtures.ctg_isolation import ctg_isolation  # noqa: F401  (autouse)

NCT = "NCT00000001"
STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
DETAIL_URL = f"{STUDIES_URL}/{NCT}"
DETAIL = {
    "protocolSection": {
        "identificationModule": {"nctId": NCT, "briefTitle": "Test Study"},
        "descriptionModule": {"briefSummary": "A test study."},
        "statusModule": {"overallStatus": "RECRUITING"},
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: age 18-65",
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "65 Years",
        },
        "contactsLocationsModule": {
            "locations": [
                {"facility": "University Hospital"},
                {"facility": "Portland VAMC"},
            ]
        },
    }
}


def mock_json(respx_mock, url, payload):
    return respx_mock.get(url).mock(return_value=httpx.Response(200, json=payload))


async def test_search_keeps_existing_shape_and_adds_receipt(respx_mock):
    route = mock_json(respx_mock, STUDIES_URL, {"studies": [], "totalCount": 0})
    result = await tools.search_studies(condition="PTSD", page_size=200)
    assert result["studies"] == []
    assert result["totalCount"] == 0
    assert result["_harness"]["jobId"] == "find_recruiting_clinical_trials"
    assert route.calls.last.request.url.params["pageSize"] == "20"


@pytest.mark.parametrize("page_size", [None, "invalid"])
async def test_search_invalid_page_size_falls_back_to_default(respx_mock, page_size):
    route = mock_json(respx_mock, STUDIES_URL, {"studies": [], "totalCount": 0})
    await tools.search_studies(condition="PTSD", page_size=page_size)
    assert route.calls.last.request.url.params["pageSize"] == "10"


async def test_get_study_details_handles_explicitly_null_modules(respx_mock):
    mock_json(respx_mock, DETAIL_URL, {"protocolSection": None})
    result = await tools.get_study_details(NCT)
    assert result["_harness"]["nctId"] == NCT


async def test_eligibility_keeps_existing_fields_and_adds_boundary(respx_mock):
    mock_json(respx_mock, DETAIL_URL, DETAIL)
    result = await tools.check_eligibility(NCT)
    assert result["minimumAge"] == "18 Years"
    assert result["_harness"]["assessmentStatus"] == "clinician_review_required"


async def test_eligibility_handles_explicitly_null_modules(respx_mock):
    mock_json(respx_mock, DETAIL_URL, {"protocolSection": {"eligibilityModule": None}})
    result = await tools.check_eligibility(NCT)
    # Absent source fields are reported as None, never as invented defaults.
    assert result["eligibilityCriteria"] is None
    assert result["minimumAge"] is None
    assert result["sex"] is None
    assert result["healthyVolunteers"] is None


async def test_eligibility_passes_source_values_through_unchanged(respx_mock):
    payload = {
        "protocolSection": {
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion: age 18-65",
                "healthyVolunteers": False,
                "sex": "FEMALE",
                "minimumAge": "18 Years",
                "stdAges": ["ADULT"],
            }
        }
    }
    mock_json(respx_mock, DETAIL_URL, payload)
    result = await tools.check_eligibility(NCT)
    assert result["healthyVolunteers"] is False  # the API's boolean, not "No"
    assert result["sex"] == "FEMALE"
    assert result["maximumAge"] is None  # not stated in the source, so not "N/A"
    assert result["stdAges"] == ["ADULT"]


async def test_summary_prioritizes_va_and_preserves_patient_context(respx_mock):
    mock_json(respx_mock, DETAIL_URL, DETAIL)
    result = await tools.generate_trial_summary(NCT, "58-year-old male with PTSD")
    assert result["patientContext"] == "58-year-old male with PTSD"
    assert result["contactInfo"]["locations"][0]["facility"] == "Portland VAMC"
    assert result["_harness"]["patientContextStatus"] == "validated_deidentified"


async def test_summary_handles_explicitly_null_modules_and_locations(respx_mock):
    payload = {
        "protocolSection": {
            "identificationModule": None,
            "descriptionModule": None,
            "eligibilityModule": None,
            "statusModule": None,
            "contactsLocationsModule": {"locations": [None, "bad"]},
        }
    }
    mock_json(respx_mock, DETAIL_URL, payload)
    result = await tools.generate_trial_summary(NCT)
    assert result["nctId"] == NCT
    assert result["title"] == ""
    assert result["contactInfo"]["locations"] == []
    assert result["eligibilityHighlights"] == {
        "criteria": None,
        "sex": None,
        "minimumAge": None,
        "maximumAge": None,
        "healthyVolunteers": None,
    }


async def test_summary_tolerates_malformed_upstream_nct_id_like_details(respx_mock):
    payload = {"protocolSection": {"identificationModule": {"nctId": "NCT-garbled"}}}
    mock_json(respx_mock, DETAIL_URL, payload)
    details = await tools.get_study_details(NCT)
    summary = await tools.generate_trial_summary(NCT)
    # Both fall back to the requested (validated) id instead of failing.
    assert details["_harness"]["nctId"] == NCT
    assert summary["nctId"] == NCT
    assert summary["link"] == f"https://clinicaltrials.gov/study/{NCT}"


async def test_referral_preserves_message_and_adds_authority():
    result = await tools.draft_referral_message(NCT, "58-year-old male with PTSD")
    assert NCT in result["messageBody"]
    assert result["_harness"]["authority"]["transmitted"] is False


async def test_referral_rejects_direct_identifier():
    with pytest.raises(ValueError, match="Potential PHI"):
        await tools.draft_referral_message(NCT, "DOB: 01/02/1970, PTSD")


async def test_count_studies_compatibility(respx_mock):
    mock_json(respx_mock, STUDIES_URL, {"totalCount": 42})
    assert await tools.count_studies(condition="PTSD") == 42


async def test_count_studies_missing_total_is_an_error_not_zero(respx_mock):
    mock_json(respx_mock, STUDIES_URL, {"studies": []})
    with pytest.raises(tools.ClinicalTrialsApiError, match="did not return a study count"):
        await tools.count_studies(condition="PTSD")
