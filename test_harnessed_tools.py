"""Compatibility tests for harnessed ClinicalTrials.gov tools."""

import pytest

import clinical_trials_tools as tools

NCT = "NCT00000001"
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


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_search_keeps_existing_shape_and_adds_receipt(monkeypatch):
    seen = {}

    def fake_get(url, *, params=None, timeout=None):
        seen["params"] = params
        return FakeResponse({"studies": [], "totalCount": 0})

    monkeypatch.setattr(tools.httpx, "get", fake_get)
    result = tools.search_studies(condition="PTSD", page_size=200)
    assert result["studies"] == []
    assert result["totalCount"] == 0
    assert result["_harness"]["jobId"] == "find_recruiting_clinical_trials"
    assert seen["params"]["pageSize"] == 20


def test_eligibility_keeps_existing_fields_and_adds_boundary(monkeypatch):
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: FakeResponse(DETAIL))
    result = tools.check_eligibility(NCT)
    assert result["minimumAge"] == "18 Years"
    assert result["_harness"]["assessmentStatus"] == "clinician_review_required"


def test_summary_prioritizes_va_and_preserves_patient_context(monkeypatch):
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: FakeResponse(DETAIL))
    result = tools.generate_trial_summary(NCT, "58-year-old male with PTSD")
    assert result["patientContext"] == "58-year-old male with PTSD"
    assert result["contactInfo"]["locations"][0]["facility"] == "Portland VAMC"
    assert result["_harness"]["patientContextStatus"] == "validated_deidentified"


def test_referral_preserves_message_and_adds_authority():
    result = tools.draft_referral_message(NCT, "58-year-old male with PTSD")
    assert NCT in result["messageBody"]
    assert result["_harness"]["authority"]["transmitted"] is False


def test_referral_rejects_direct_identifier():
    with pytest.raises(ValueError, match="Potential PHI"):
        tools.draft_referral_message(NCT, "DOB: 01/02/1970, PTSD")


def test_count_studies_compatibility(monkeypatch):
    monkeypatch.setattr(
        tools.httpx,
        "get",
        lambda *a, **k: FakeResponse({"totalCount": 42}),
    )
    assert tools.count_studies(condition="PTSD") == 42
