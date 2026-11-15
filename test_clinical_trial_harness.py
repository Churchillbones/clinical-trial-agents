"""Unit tests for the deterministic clinical-trial harness."""

import pytest

from clinical_trial_harness import (
    build_eligibility_receipt,
    build_search_receipt,
    clinical_trials_anchor,
    detect_potential_phi,
    prioritize_va_locations,
    referral_authority_envelope,
    require_deidentified_text,
    validate_nct_id,
)


def test_validate_nct_id_normalizes_case():
    assert validate_nct_id("nct01234567") == "NCT01234567"


def test_validate_nct_id_rejects_invalid_value():
    with pytest.raises(ValueError, match="NCT########"):
        validate_nct_id("123")


@pytest.mark.parametrize("value", [None, 12345678, object()])
def test_validate_nct_id_rejects_non_string_values_without_attribute_error(value):
    with pytest.raises(ValueError, match="NCT########"):
        validate_nct_id(value)


def test_phi_screen_detects_direct_identifiers_without_echoing_them():
    findings = detect_potential_phi("DOB: 01/02/1970 and SSN 123-45-6789")
    assert findings == ["ssn", "date_of_birth"]


def test_phi_screen_allows_deidentified_clinical_context():
    require_deidentified_text(
        "58-year-old male Veteran with PTSD taking sertraline",
        field_name="patient_summary",
    )


def test_phi_screen_rejects_email():
    with pytest.raises(ValueError, match="email_address"):
        require_deidentified_text(
            "Contact the patient at veteran@example.com",
            field_name="patient_summary",
        )


# The screen also runs on search terms sent to ClinicalTrials.gov, so ordinary
# trial vocabulary must pass.
@pytest.mark.parametrize(
    "text",
    [
        "NCT01234567",
        "PHASE2",
        "97239",
        "97239-2964",
        "Portland, OR 97239",
        "mRNA-1273",  # previously mistaken for an "MRN" label
        "mRNA-based vaccine",
        "patient identified with treatment-resistant PTSD",  # previously "patient id"
        "patient idiopathic pulmonary fibrosis",
        "MK-3475",
        "HbA1c 7.5-12%",
        "dobutamine stress echo 10/20/30",
        "newborn screening 01/02/2026",
        "Department of Veterans Affairs",
        "MRN pending",
    ],
)
def test_phi_screen_allows_legitimate_search_terms(text):
    assert detect_potential_phi(text) == []


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("SSN 123456789", "ssn"),
        ("SSN: 123 45 6789", "ssn"),
        ("social security number 123456789", "ssn"),
        ("DOB 1970-01-02", "date_of_birth"),
        ("D.O.B. 01/02/1970", "date_of_birth"),
        ("DOB: March 3, 1961", "date_of_birth"),
        ("born 01/02/1970", "date_of_birth"),
        ("MRN1234567", "medical_record_identifier"),
        ("patient ID: A123456", "medical_record_identifier"),
        ("patient id number 12345", "medical_record_identifier"),
        ("medical record no. 12345", "medical_record_identifier"),
        ("patient name is John Smith", "labeled_patient_name"),
    ],
)
def test_phi_screen_detects_labelled_identifier_variants(text, category):
    assert category in detect_potential_phi(text)


def test_phi_screen_does_not_require_dashes_in_an_unlabelled_ssn_to_pass():
    # Nine bare digits without a label are too ambiguous to block.
    assert detect_potential_phi("123456789") == []


def test_phi_screen_accepts_non_string_values_without_type_error():
    assert detect_potential_phi(58) == []
    assert detect_potential_phi(5035550100) == ["phone_number"]


def test_phi_rejection_message_never_echoes_the_identifier():
    with pytest.raises(ValueError) as excinfo:
        require_deidentified_text("SSN 123-45-6789", field_name="term")
    assert "123-45-6789" not in str(excinfo.value)
    assert "term" in str(excinfo.value)


def test_search_receipt_marks_results_as_clinician_review_candidates():
    receipt = build_search_receipt(
        query={"condition": "PTSD", "status": "RECRUITING", "age": "58"},
        result={"studies": [{"protocolSection": {}}], "totalCount": 1},
        retrieved_at="2026-07-18T00:00:00+00:00",
    )
    assert receipt["jobId"] == "find_recruiting_clinical_trials"
    assert receipt["returnedCount"] == 1
    assert "not an eligibility determination" in receipt["assessmentBoundary"]
    assert receipt["warnings"]


def test_search_receipt_proposes_recovery_for_empty_location_search():
    receipt = build_search_receipt(
        query={"condition": "PTSD", "location": "St. Cloud"},
        result={"studies": [], "totalCount": 0},
    )
    assert len(receipt["recoveryOptions"]) == 3


def test_eligibility_receipt_prohibits_eligibility_conclusion():
    receipt = build_eligibility_receipt("NCT01234567")
    assert "eligible" in receipt["prohibitedConclusions"]
    assert all(
        anchor.startswith("ctg:NCT01234567:")
        for anchor in receipt["evidenceAnchors"]
    )


def test_clinical_trials_anchor_is_stable():
    assert clinical_trials_anchor(
        "nct01234567",
        "protocolSection.statusModule.overallStatus",
    ) == "ctg:NCT01234567:protocolSection.statusModule.overallStatus"


def test_prioritize_va_locations_is_stable():
    locations = [
        {"facility": "University Hospital"},
        {"facility": "St. Cloud VA Medical Center"},
        {"facility": "Portland VAMC"},
    ]
    ordered = prioritize_va_locations(locations)
    assert [item["facility"] for item in ordered] == [
        "St. Cloud VA Medical Center",
        "Portland VAMC",
        "University Hospital",
    ]


def test_prioritize_va_locations_handles_none_and_filters_invalid_entries():
    assert prioritize_va_locations(None) == []
    ordered = prioritize_va_locations(
        [None, "bad", {"facility": None}, {"facility": "Portland VAMC"}]
    )
    assert ordered == [{"facility": "Portland VAMC"}, {"facility": None}]


def test_referral_authority_is_draft_only():
    envelope = referral_authority_envelope()
    assert envelope["reviewState"] == "proposed"
    assert envelope["transmitted"] is False
    assert "send" in envelope["prohibitedActions"]
