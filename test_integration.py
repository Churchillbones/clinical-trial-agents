"""Integration test: full clinician flow through the agent's tools.

Simulates search -> details/eligibility -> patient summary -> referral draft
through ``dispatch_tool``, which runs the same FunctionTool objects the agent
uses (framework argument validation, then the async wrapper and its error
mapping) over the real ``clinical_trials_tools`` functions, with the
ClinicalTrials.gov API mocked end to end by respx. No Azure or network access
required.

Each test uses respx's ``respx_mock`` fixture, a router of its own that is
rolled back afterwards, so routes cannot leak between tests.
"""

import httpx
import pytest

import clinical_trials_tools as ct
from main import dispatch_tool

CTG = "https://clinicaltrials.gov/api/v2"
NCT = "NCT06123456"

SEARCH_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": NCT,
                    "briefTitle": "Stellate Ganglion Block for Treatment-Resistant PTSD",
                },
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"]},
                "conditionsModule": {"conditions": ["PTSD"]},
            }
        }
    ],
    "totalCount": 1,
}

STUDY_DETAIL = {
    "protocolSection": {
        "identificationModule": {
            "nctId": NCT,
            "briefTitle": "Stellate Ganglion Block for Treatment-Resistant PTSD",
        },
        "descriptionModule": {
            "briefSummary": "This study tests a nerve block for PTSD symptoms.",
        },
        "statusModule": {"overallStatus": "RECRUITING"},
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: Age 18-65, PCL-5 >= 38",
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "65 Years",
        },
        "contactsLocationsModule": {
            "centralContacts": [
                {"name": "Dr. Smith", "phone": "503-555-0100"}
            ],
            "locations": [
                {
                    "facility": "Portland VAMC",
                    "city": "Portland",
                    "state": "Oregon",
                    "country": "United States",
                }
            ],
        },
    }
}


@pytest.fixture(autouse=True)
async def _fresh_shared_client():
    # The shared AsyncClient is bound to the event loop that first used it and
    # every test gets its own loop, so close it after each test.
    yield
    await ct.aclose_client()


async def test_full_referral_flow(respx_mock):
    search_route = respx_mock.get(f"{CTG}/studies").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE)
    )
    respx_mock.get(f"{CTG}/studies/{NCT}").mock(
        return_value=httpx.Response(200, json=STUDY_DETAIL)
    )

    # 1. Clinician searches for a trial.
    search = await dispatch_tool(
        "search_studies", {"condition": "PTSD", "location": "Portland"}
    )
    nct = search["studies"][0]["protocolSection"]["identificationModule"]["nctId"]
    assert nct == NCT
    # The schema default (status=RECRUITING) reached ClinicalTrials.gov.
    params = search_route.calls.last.request.url.params
    assert params["query.cond"] == "PTSD"
    assert params["filter.overallStatus"] == "RECRUITING"

    # 2. Clinician pulls full details and eligibility for the chosen study.
    details = await dispatch_tool("get_study_details", {"nct_id": nct})
    assert (
        details["protocolSection"]["eligibilityModule"]["minimumAge"] == "18 Years"
    )
    eligibility = await dispatch_tool("check_eligibility", {"nct_id": nct})
    assert eligibility["nctId"] == NCT
    assert eligibility["minimumAge"] == "18 Years"
    assert eligibility["maximumAge"] == "65 Years"
    assert eligibility["eligibilityCriteria"].startswith("Inclusion")

    # 3. Clinician generates a patient-friendly handout.
    summary = await dispatch_tool(
        "generate_trial_summary",
        {"nct_id": nct, "patient_context": "58-year-old male with PTSD"},
    )
    assert summary["nctId"] == nct
    assert summary["patientContext"] == "58-year-old male with PTSD"
    assert summary["plainLanguageSummary"]
    assert summary["contactInfo"]["locations"][0]["facility"] == "Portland VAMC"

    # 4. Clinician drafts a de-identified referral message.
    referral = await dispatch_tool(
        "draft_referral_message",
        {
            "nct_id": nct,
            "patient_summary": "58-year-old male veteran, treatment-resistant PTSD",
            "clinician_name": "Dr. Jane Smith",
            "clinician_facility": "Portland VAMC",
        },
    )
    assert nct in referral["messageBody"]
    assert "Dr. Jane Smith" in referral["messageBody"]
    assert "disclaimer" in referral


async def test_count_studies_returns_a_count_object(respx_mock):
    respx_mock.get(f"{CTG}/studies").mock(
        return_value=httpx.Response(200, json={"totalCount": 42, "studies": []})
    )

    assert await dispatch_tool("count_studies", {"condition": "PTSD"}) == {"count": 42}


async def test_dispatch_tool_unknown_returns_error():
    result = await dispatch_tool("not_a_real_tool", {})
    assert result == {
        "error": {
            "type": "invalid_input",
            "message": "Unknown tool: not_a_real_tool",
            "retryable": False,
        }
    }


@pytest.mark.respx(assert_all_called=False)
async def test_phi_in_referral_is_rejected_with_a_recoverable_error(respx_mock):
    route = respx_mock.get(url__startswith=CTG).mock(
        return_value=httpx.Response(200, json=STUDY_DETAIL)
    )

    result = await dispatch_tool(
        "draft_referral_message",
        {"nct_id": NCT, "patient_summary": "DOB: 01/02/1970, PTSD"},
    )

    error = result["error"]
    assert error["type"] == "phi_detected"
    assert error["retryable"] is False
    assert "patient_summary" in error["message"]
    assert "01/02/1970" not in error["message"]  # categories only, never the text
    assert not route.called


@pytest.mark.respx(assert_all_called=False)
async def test_bad_nct_format_is_rejected_before_any_upstream_call(respx_mock):
    route = respx_mock.get(url__startswith=CTG).mock(
        return_value=httpx.Response(200, json=STUDY_DETAIL)
    )

    result = await dispatch_tool("get_study_details", {"nct_id": "INVALID"})

    assert result["error"]["type"] == "invalid_input"
    assert result["error"]["retryable"] is False
    assert "NCT" in result["error"]["message"]
    assert not route.called


async def test_unknown_study_maps_to_not_found(respx_mock):
    respx_mock.get(f"{CTG}/studies/{NCT}").mock(
        return_value=httpx.Response(404, json={"message": "Study not found"})
    )

    result = await dispatch_tool("get_study_details", {"nct_id": NCT})

    assert set(result) == {"error"}
    assert result["error"]["type"] == "not_found"
    assert result["error"]["message"]
    assert isinstance(result["error"]["retryable"], bool)


async def test_upstream_failure_maps_to_upstream_unavailable(respx_mock):
    respx_mock.get(f"{CTG}/studies").mock(
        return_value=httpx.Response(500, json={"message": "boom-upstream-detail"})
    )

    result = await dispatch_tool("search_studies", {"condition": "PTSD"})

    assert set(result) == {"error"}
    error = result["error"]
    assert error["type"] == "upstream_unavailable"
    assert error["message"]
    assert "boom-upstream-detail" not in error["message"]
    assert isinstance(error["retryable"], bool)
