"""Integration test: full clinician flow through the agent tool dispatch.

Simulates search -> details/eligibility -> patient summary -> referral draft
using the ``dispatch_tool`` wiring from main.py with the ClinicalTrials.gov
API mocked end to end. No Azure or network access required.
"""

import httpx
import respx

from main import dispatch_tool

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


@respx.mock
def test_full_referral_flow():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE)
    )
    respx.get(f"https://clinicaltrials.gov/api/v2/studies/{NCT}").mock(
        return_value=httpx.Response(200, json=STUDY_DETAIL)
    )

    # 1. Clinician searches for a trial.
    search = dispatch_tool(
        "search_studies", {"condition": "PTSD", "location": "Portland"}
    )
    nct = search["studies"][0]["protocolSection"]["identificationModule"]["nctId"]
    assert nct == NCT

    # 2. Clinician pulls eligibility / full details for the chosen study.
    details = dispatch_tool("get_study_details", {"nct_id": nct})
    assert (
        details["protocolSection"]["eligibilityModule"]["minimumAge"] == "18 Years"
    )

    # 3. Clinician generates a patient-friendly handout.
    summary = dispatch_tool(
        "generate_trial_summary",
        {"nct_id": nct, "patient_context": "58-year-old male with PTSD"},
    )
    assert summary["nctId"] == nct
    assert summary["patientContext"] == "58-year-old male with PTSD"
    assert summary["plainLanguageSummary"]
    assert summary["contactInfo"]["locations"][0]["facility"] == "Portland VAMC"

    # 4. Clinician drafts a de-identified referral message.
    referral = dispatch_tool(
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


def test_dispatch_tool_unknown_returns_error():
    result = dispatch_tool("not_a_real_tool", {})
    assert result == {"error": "Unknown tool: not_a_real_tool"}


@respx.mock
def test_dispatch_tool_wraps_tool_exception():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    result = dispatch_tool("search_studies", {"condition": "PTSD"})
    assert "error" in result
    assert result["error"].startswith("HTTPStatusError")
