"""Contract tests for the ClinicalTrials.gov tool layer.

Payloads come from tests_fixtures/ctg_v2_payloads.py, which are hand-built from
the documented API v2 shapes (not recorded from the live API). Covers the enums
array shape and cache, countTotal, retry/backoff, PHI screening of outbound
search inputs, payload projection and location capping, malformed payloads,
and the shared client. No test touches the network or sleeps for real.
"""

import asyncio
import json
import logging

import httpx
import pytest

import clinical_trials_tools as tools
from clinical_trials_tools import ClinicalTrialsApiError
from tests_fixtures.ctg_isolation import ctg_isolation  # noqa: F401  (autouse)
from tests_fixtures.ctg_v2_payloads import (
    enums_payload,
    search_page,
    site_locations,
    study_record,
)

STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
ENUMS_URL = f"{STUDIES_URL}/enums"
NCT = "NCT05123456"
DETAIL_URL = f"{STUDIES_URL}/{NCT}"
VA_FACILITIES = [
    "VA Portland Health Care System",
    "Minneapolis VA Medical Center",
    "VA Palo Alto Health Care System",
]


def ok(payload):
    return httpx.Response(200, json=payload)


def chained_messages(error):
    messages = []
    while error is not None:
        messages.append(str(error))
        error = error.__cause__ or error.__context__
    return messages


# --------------------------------------------------------------------------- #
# GET /studies/enums
# --------------------------------------------------------------------------- #
async def test_enums_array_shape_matches_type_case_insensitively(respx_mock):
    respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    assert await tools.list_enum_values("phase") == [
        "NA",
        "EARLY_PHASE1",
        "PHASE1",
        "PHASE2",
        "PHASE3",
        "PHASE4",
    ]
    assert await tools.list_enum_values(" StudyType ") == [
        "EXPANDED_ACCESS",
        "INTERVENTIONAL",
        "OBSERVATIONAL",
    ]


async def test_enums_fall_back_to_matching_pieces(respx_mock):
    respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    statuses = await tools.list_enum_values("OverallStatus")  # a piece of type "Status"
    assert "RECRUITING" in statuses
    assert "NOT_YET_RECRUITING" in statuses
    assert await tools.list_enum_values("stdage") == ["CHILD", "ADULT", "OLDER_ADULT"]


async def test_enums_type_match_wins_over_piece_match(respx_mock):
    payload = [
        {"type": "Other", "pieces": ["Sex"], "values": [{"value": "WRONG"}]},
        {"type": "Sex", "pieces": ["Gender"], "values": [{"value": "ALL"}]},
    ]
    respx_mock.get(ENUMS_URL).mock(return_value=ok(payload))
    assert await tools.list_enum_values("Sex") == ["ALL"]


async def test_enums_unknown_name_returns_empty_list(respx_mock):
    respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    assert await tools.list_enum_values("NotARealEnum") == []


async def test_enums_blank_name_is_rejected_without_a_request(respx_mock):
    route = respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    with pytest.raises(ValueError, match="enum_name is required"):
        await tools.list_enum_values("  ")
    assert route.call_count == 0


async def test_enums_legacy_dict_shape_is_tolerated(respx_mock):
    payload = {"Phase": ["PHASE1", "PHASE2"], "OverallStatus": [{"value": "RECRUITING"}]}
    respx_mock.get(ENUMS_URL).mock(return_value=ok(payload))
    assert await tools.list_enum_values("phase") == ["PHASE1", "PHASE2"]
    assert await tools.list_enum_values("OverallStatus") == ["RECRUITING"]
    assert await tools.list_enum_values("Sex") == []


async def test_enums_malformed_entries_and_values_are_skipped(respx_mock):
    payload = [
        None,
        "bad",
        {"type": None, "pieces": "Phase"},
        {
            "type": "Phase",
            "values": [None, {"value": ""}, {"value": "PHASE1"}, "PHASE2", {"legacyValue": "x"}],
        },
        {"type": "Sex", "values": "not-a-list"},
    ]
    respx_mock.get(ENUMS_URL).mock(return_value=ok(payload))
    assert await tools.list_enum_values("Phase") == ["PHASE1", "PHASE2"]
    assert await tools.list_enum_values("Sex") == []


@pytest.mark.parametrize("body", [b'"enums"', b"42", b"null"])
async def test_enums_body_that_is_not_a_collection_raises_api_error(respx_mock, body):
    respx_mock.get(ENUMS_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ClinicalTrialsApiError, match="unexpected enum list format"):
        await tools.list_enum_values("Phase")


async def test_enums_payload_is_cached_between_calls(respx_mock):
    route = respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    await tools.list_enum_values("Phase")
    await tools.list_enum_values("Sex")
    assert route.call_count == 1


async def test_enums_cache_expires_after_its_ttl(respx_mock, monkeypatch):
    route = respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    monkeypatch.setattr(tools, "ENUMS_CACHE_TTL_SECONDS", 0)
    await tools.list_enum_values("Phase")
    await tools.list_enum_values("Phase")
    assert route.call_count == 2


async def test_enums_cache_can_be_reset(respx_mock):
    route = respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    await tools.list_enum_values("Phase")
    tools.reset_enums_cache()
    await tools.list_enum_values("Phase")
    assert route.call_count == 2


async def test_empty_enums_payload_is_not_cached(respx_mock):
    route = respx_mock.get(ENUMS_URL).mock(side_effect=[ok([]), ok(enums_payload())])
    assert await tools.list_enum_values("Sex") == []
    assert await tools.list_enum_values("Sex") == ["FEMALE", "MALE", "ALL"]
    assert route.call_count == 2


# --------------------------------------------------------------------------- #
# GET /studies: query parameters and receipt
# --------------------------------------------------------------------------- #
async def test_search_sends_count_total_and_receipt_reports_the_total(respx_mock):
    route = respx_mock.get(STUDIES_URL).mock(
        return_value=ok(search_page([study_record()], total_count=137))
    )
    result = await tools.search_studies(condition="PTSD", phase="PHASE2")
    params = route.calls.last.request.url.params
    assert params["countTotal"] == "true"
    assert params["filter.advanced"] == "AREA[Phase]PHASE2"
    assert "fields" not in params  # projection is client-side
    assert result["totalCount"] == 137
    assert result["nextPageToken"] == "NF0g5JiEk_Ew"
    receipt = result["_harness"]
    assert receipt["totalCount"] == 137
    assert receipt["returnedCount"] == 1
    # Existing receipt fields keep their meaning.
    assert receipt["jobId"] == "find_recruiting_clinical_trials"
    assert receipt["query"] == {"condition": "PTSD", "status": "RECRUITING", "phase": "PHASE2", "pageSize": 10}
    assert receipt["source"]["system"] == "ClinicalTrials.gov API v2"
    assert "not an eligibility determination" in receipt["assessmentBoundary"]
    assert receipt["warnings"] == []
    assert receipt["recoveryOptions"] == []


async def test_search_missing_studies_key_is_an_empty_result_with_recovery(respx_mock):
    respx_mock.get(STUDIES_URL).mock(return_value=ok({"totalCount": 0}))
    result = await tools.search_studies(condition="xyz", location="St. Cloud")
    assert result["studies"] == []
    assert result["nextPageToken"] is None
    assert len(result["_harness"]["recoveryOptions"]) == 3


# --------------------------------------------------------------------------- #
# PHI screen on outbound search inputs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field", ["term", "condition", "intervention", "location", "sponsor", "age", "status", "phase"]
)
async def test_search_screens_inputs_before_any_request(respx_mock, field):
    route = respx_mock.get(STUDIES_URL).mock(return_value=ok(search_page([])))
    with pytest.raises(ValueError, match=f"Potential PHI detected in {field}: ssn") as excinfo:
        await tools.search_studies(**{field: "PTSD, SSN 123-45-6789"})
    assert "123-45-6789" not in str(excinfo.value)
    assert route.call_count == 0
    assert not respx_mock.calls


@pytest.mark.parametrize("field", ["term", "condition", "intervention", "sponsor", "status"])
async def test_count_screens_inputs_before_any_request(respx_mock, field):
    route = respx_mock.get(STUDIES_URL).mock(return_value=ok({"totalCount": 1}))
    with pytest.raises(ValueError, match=f"Potential PHI detected in {field}"):
        await tools.count_studies(**{field: "veteran@example.com"})
    assert route.call_count == 0


async def test_search_lets_identifier_like_trial_vocabulary_through(respx_mock):
    route = respx_mock.get(STUDIES_URL).mock(return_value=ok(search_page([], total_count=0)))
    await tools.search_studies(
        term="NCT05123456",
        intervention="mRNA-1273",
        location="Portland, OR 97239",
        phase="PHASE2",
    )
    params = route.calls.last.request.url.params
    assert params["query.term"] == "NCT05123456"
    assert params["query.intr"] == "mRNA-1273"
    assert params["query.locn"] == "Portland, OR 97239"


# --------------------------------------------------------------------------- #
# Projection and location capping
# --------------------------------------------------------------------------- #
async def test_search_projection_keeps_agent_fields_at_their_source_paths(respx_mock):
    respx_mock.get(STUDIES_URL).mock(return_value=ok(search_page([study_record()], total_count=1)))
    result = await tools.search_studies(condition="PTSD")
    assert set(result) == {"studies", "totalCount", "nextPageToken", "_harness"}
    study = result["studies"][0]
    assert set(study) == {"protocolSection", "_harness"}
    protocol = study["protocolSection"]
    assert protocol["identificationModule"] == {
        "nctId": NCT,
        "briefTitle": "Stellate Ganglion Block for Treatment-Resistant PTSD",
        "officialTitle": (
            "A Randomized, Sham-Controlled Trial of Stellate Ganglion Block "
            "for Veterans With Treatment-Resistant Posttraumatic Stress Disorder"
        ),
    }
    assert protocol["statusModule"] == {
        "overallStatus": "RECRUITING",
        "startDateStruct": {"date": "2025-03-01", "type": "ACTUAL"},
        "primaryCompletionDateStruct": {"date": "2028-09-30", "type": "ESTIMATED"},
    }
    assert protocol["sponsorCollaboratorsModule"] == {
        "leadSponsor": {"name": "VA Office of Research and Development"}
    }
    assert protocol["conditionsModule"] == {"conditions": ["Post-Traumatic Stress Disorder"]}
    assert protocol["armsInterventionsModule"] == {
        "interventions": [{"type": "DRUG", "name": "Ropivacaine"}, {"type": "OTHER", "name": "Saline"}]
    }
    assert protocol["designModule"] == {
        "studyType": "INTERVENTIONAL",
        "phases": ["PHASE2"],
        "enrollmentInfo": {"count": 240, "type": "ESTIMATED"},
    }
    assert protocol["descriptionModule"]["briefSummary"].startswith("This study tests")
    # Criteria text is left to get_study_details / check_eligibility.
    assert protocol["eligibilityModule"] == {
        "sex": "ALL",
        "minimumAge": "18 Years",
        "maximumAge": "70 Years",
        "stdAges": ["ADULT", "OLDER_ADULT"],
    }
    assert protocol["contactsLocationsModule"]["locations"][0] == {
        "facility": "VA Portland Health Care System",
        "city": "Portland",
        "state": "Oregon",
        "country": "United States",
        "status": "RECRUITING",
    }
    projection = result["_harness"]["projection"]
    assert projection["isFullRecord"] is False
    assert projection["view"] == "search_summary"
    assert projection["skippedMalformedStudies"] == 0


async def test_search_projection_shrinks_a_full_page(respx_mock):
    studies = [
        study_record(
            f"NCT05{number:06d}",
            locations=site_locations(60, va_positions=(57, 58, 59)),
            brief_summary="A long plain-language summary sentence. " * 60,
        )
        for number in range(20)
    ]
    raw_page = search_page(studies, total_count=412)
    raw_size = len(json.dumps(raw_page))
    respx_mock.get(STUDIES_URL).mock(return_value=ok(raw_page))
    result = await tools.search_studies(condition="PTSD", page_size=20)
    projected_size = len(json.dumps(result))
    assert len(result["studies"]) == 20
    assert projected_size < raw_size / 10
    assert projected_size < 60_000


async def test_search_caps_locations_va_first_and_records_the_total(respx_mock):
    locations = site_locations(60, va_positions=(57, 58, 59))  # VA sites listed last
    respx_mock.get(STUDIES_URL).mock(
        return_value=ok(search_page([study_record(locations=locations)], total_count=1))
    )
    result = await tools.search_studies(condition="PTSD")
    study = result["studies"][0]
    facilities = [
        location["facility"]
        for location in study["protocolSection"]["contactsLocationsModule"]["locations"]
    ]
    assert len(facilities) == tools.SEARCH_MAX_LOCATIONS
    assert facilities[:3] == VA_FACILITIES
    assert study["_harness"] == {
        "locationsTotal": 60,
        "locationsReturned": tools.SEARCH_MAX_LOCATIONS,
        "locationsTruncated": True,
        "briefSummaryTruncated": False,
    }


async def test_search_truncates_a_long_brief_summary(respx_mock):
    record = study_record(brief_summary="word " * 400)
    respx_mock.get(STUDIES_URL).mock(return_value=ok(search_page([record], total_count=1)))
    result = await tools.search_studies(condition="PTSD")
    study = result["studies"][0]
    summary = study["protocolSection"]["descriptionModule"]["briefSummary"]
    assert len(summary) <= tools.SEARCH_SUMMARY_MAX_CHARS + 1
    assert summary.endswith("…")
    assert study["_harness"]["briefSummaryTruncated"] is True


async def test_details_keep_protocol_and_has_results_and_drop_other_sections(respx_mock):
    respx_mock.get(DETAIL_URL).mock(return_value=ok(study_record()))
    result = await tools.get_study_details(NCT)
    assert set(result) == {"protocolSection", "hasResults", "_harness"}
    assert result["hasResults"] is True
    protocol = result["protocolSection"]
    assert protocol["eligibilityModule"]["eligibilityCriteria"].startswith("Inclusion Criteria")
    assert protocol["outcomesModule"]["primaryOutcomes"]
    receipt = result["_harness"]
    assert receipt["nctId"] == NCT
    assert receipt["projection"]["isFullRecord"] is False
    assert receipt["projection"]["omittedSections"] == [
        "annotationSection",
        "derivedSection",
        "documentSection",
        "resultsSection",
    ]
    assert "assessmentBoundary" in receipt and "source" in receipt


async def test_details_cap_locations_va_first_and_record_counts(respx_mock):
    record = study_record(locations=site_locations(80, va_positions=(40, 79)))
    respx_mock.get(DETAIL_URL).mock(return_value=ok(record))
    result = await tools.get_study_details(NCT)
    locations = result["protocolSection"]["contactsLocationsModule"]["locations"]
    assert len(locations) == tools.DETAILS_MAX_LOCATIONS
    assert [location["facility"] for location in locations[:2]] == VA_FACILITIES[:2]
    assert locations[0]["contacts"]  # details keep site contacts
    receipt = result["_harness"]
    assert receipt["locationsTotal"] == 80
    assert receipt["locationsReturned"] == tools.DETAILS_MAX_LOCATIONS
    assert receipt["locationsTruncated"] is True
    # Other contactsLocationsModule fields are untouched.
    assert result["protocolSection"]["contactsLocationsModule"]["centralContacts"]


async def test_details_with_few_locations_are_not_truncated(respx_mock):
    respx_mock.get(DETAIL_URL).mock(return_value=ok(study_record()))
    receipt = (await tools.get_study_details(NCT))["_harness"]
    assert (receipt["locationsTotal"], receipt["locationsReturned"]) == (3, 3)
    assert receipt["locationsTruncated"] is False


async def test_summary_works_from_projected_details(respx_mock):
    record = study_record(locations=site_locations(40, va_positions=(39,)))
    respx_mock.get(DETAIL_URL).mock(return_value=ok(record))
    summary = await tools.generate_trial_summary(NCT, patient_context="62-year-old Veteran with PTSD")
    assert summary["title"] == "Stellate Ganglion Block for Treatment-Resistant PTSD"
    assert summary["plainLanguageSummary"].startswith("This study tests")
    assert summary["whatParticipationInvolves"].startswith("Participants are randomized")
    assert summary["overallStatus"] == "RECRUITING"
    highlights = summary["eligibilityHighlights"]
    assert highlights["criteria"].startswith("Inclusion Criteria")
    assert highlights["healthyVolunteers"] is False
    assert summary["contactInfo"]["centralContacts"][0]["role"] == "CONTACT"
    locations = summary["contactInfo"]["locations"]
    assert len(locations) == tools.DETAILS_MAX_LOCATIONS
    assert locations[0]["facility"] == VA_FACILITIES[0]
    receipt = summary["_harness"]
    assert receipt["locationsTotal"] == 40
    assert receipt["locationsTruncated"] is True
    assert receipt["source"]["system"] == "ClinicalTrials.gov API v2"
    assert receipt["evidenceAnchors"][0] == f"ctg:{NCT}:protocolSection.descriptionModule.briefSummary"


# --------------------------------------------------------------------------- #
# Retry and backoff
# --------------------------------------------------------------------------- #
async def test_429_then_success_is_retried_honouring_retry_after(respx_mock, ctg_isolation):
    route = respx_mock.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            ok(search_page([], total_count=0)),
        ]
    )
    result = await tools.search_studies(condition="PTSD")
    assert result["totalCount"] == 0
    assert route.call_count == 2
    assert ctg_isolation.sleeps == [2.0]


async def test_retry_after_is_capped(respx_mock, ctg_isolation):
    respx_mock.get(STUDIES_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3600"}), ok({"totalCount": 3})]
    )
    assert await tools.count_studies(condition="PTSD") == 3
    assert ctg_isolation.sleeps == [tools.RETRY_AFTER_MAX_SECONDS]


async def test_retry_after_http_date_falls_back_to_backoff(respx_mock, ctg_isolation):
    respx_mock.get(STUDIES_URL).mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            ok({"totalCount": 3}),
        ]
    )
    assert await tools.count_studies(condition="PTSD") == 3
    assert len(ctg_isolation.sleeps) == 1
    assert 0.25 <= ctg_isolation.sleeps[0] <= 0.5


@pytest.mark.parametrize("status", [502, 503, 504])
async def test_persistent_unavailability_gives_up_after_three_attempts(
    respx_mock, ctg_isolation, status
):
    route = respx_mock.get(STUDIES_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ClinicalTrialsApiError) as excinfo:
        await tools.search_studies(condition="sentinel-condition")
    error = excinfo.value
    assert error.status_code == status
    assert error.retryable is True
    assert str(error) == (
        f"ClinicalTrials.gov is temporarily unavailable (HTTP {status}) after 3 attempts"
    )
    assert route.call_count == 3
    first, second = ctg_isolation.sleeps
    assert 0.25 <= first <= 0.5
    assert 0.5 <= second <= 1.0
    # No exception in the chain (as logged by logger.exception) carries the
    # request URL's query values, e.g. via an httpx.HTTPStatusError message.
    assert not any("sentinel" in message for message in chained_messages(error))


async def test_persistent_rate_limiting_is_a_retryable_error(respx_mock, ctg_isolation):
    route = respx_mock.get(STUDIES_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ClinicalTrialsApiError, match="rate limiting") as excinfo:
        await tools.count_studies(condition="PTSD")
    assert excinfo.value.status_code == 429
    assert excinfo.value.retryable is True
    assert route.call_count == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
async def test_non_transient_statuses_are_not_retried(respx_mock, ctg_isolation, status):
    route = respx_mock.get(STUDIES_URL).mock(
        return_value=httpx.Response(status, json={"message": "echo: SSN 123-45-6789"})
    )
    with pytest.raises(ClinicalTrialsApiError) as excinfo:
        await tools.search_studies(condition="PTSD")
    assert excinfo.value.status_code == status
    assert excinfo.value.retryable is False
    assert "123-45-6789" not in str(excinfo.value)  # upstream bodies never reach the message
    assert route.call_count == 1
    assert ctg_isolation.sleeps == []


async def test_eligibility_unknown_id_is_not_found_and_not_retried(respx_mock):
    route = respx_mock.get(DETAIL_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(ClinicalTrialsApiError, match=f"Study {NCT} was not found") as excinfo:
        await tools.check_eligibility(NCT)
    assert excinfo.value.status_code == 404
    assert route.call_count == 1


async def test_transport_error_is_retried_then_succeeds(respx_mock, ctg_isolation):
    route = respx_mock.get(DETAIL_URL).mock(
        side_effect=[httpx.ConnectError("connection refused"), ok(study_record())]
    )
    result = await tools.get_study_details(NCT)
    assert result["_harness"]["nctId"] == NCT
    assert route.call_count == 2
    assert len(ctg_isolation.sleeps) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (httpx.ReadTimeout("timed out"), "did not respond in time after 3 attempts"),
        (httpx.ConnectError("connection refused"), "Could not reach ClinicalTrials.gov after 3 attempts"),
        (httpx.RemoteProtocolError("server disconnected"), "Could not reach ClinicalTrials.gov"),
    ],
)
async def test_exhausted_transport_errors_raise_retryable_error(
    respx_mock, ctg_isolation, error, message
):
    route = respx_mock.get(DETAIL_URL).mock(side_effect=error)
    with pytest.raises(ClinicalTrialsApiError, match=message) as excinfo:
        await tools.get_study_details(NCT)
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code is None
    assert isinstance(excinfo.value.__cause__, type(error))
    assert route.call_count == 3
    assert len(ctg_isolation.sleeps) == 2


async def test_non_transient_transport_error_is_not_retried(respx_mock, ctg_isolation):
    route = respx_mock.get(DETAIL_URL).mock(side_effect=httpx.UnsupportedProtocol("bad scheme"))
    with pytest.raises(ClinicalTrialsApiError) as excinfo:
        await tools.get_study_details(NCT)
    assert excinfo.value.retryable is False
    assert route.call_count == 1
    assert ctg_isolation.sleeps == []


def test_backoff_grows_exponentially_with_jitter():
    bounds = {1: (0.25, 0.5), 2: (0.5, 1.0), 3: (1.0, 2.0), 10: (2.0, tools.BACKOFF_MAX_SECONDS)}
    for attempt, (low, high) in bounds.items():
        delays = {tools._backoff_seconds(attempt) for _ in range(50)}
        assert all(low <= delay <= high for delay in delays)
        assert len(delays) > 1  # jittered, not a fixed schedule


async def test_logs_never_include_tool_arguments(respx_mock, caplog):
    respx_mock.get(STUDIES_URL).mock(
        side_effect=[httpx.Response(503), ok(search_page([None], total_count=1))]
    )
    with caplog.at_level(logging.DEBUG, logger="clinical_trials_tools"):
        await tools.search_studies(condition="sentinel-condition", location="sentinel-place")
    assert "HTTP 503" in caplog.text
    assert "malformed" in caplog.text
    assert "sentinel" not in caplog.text


# --------------------------------------------------------------------------- #
# Malformed upstream payloads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", [b"[]", b'"studies"', b"null", b"42"])
async def test_search_body_that_is_not_an_object_raises_api_error(respx_mock, body):
    respx_mock.get(STUDIES_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ClinicalTrialsApiError, match="unexpected search response format"):
        await tools.search_studies(condition="PTSD")


@pytest.mark.parametrize("studies", [{NCT: {}}, "studies", 7])
async def test_search_studies_that_are_not_a_list_raise_api_error(respx_mock, studies):
    respx_mock.get(STUDIES_URL).mock(return_value=ok({"studies": studies, "totalCount": 1}))
    with pytest.raises(ClinicalTrialsApiError, match="unexpected search response format"):
        await tools.search_studies(condition="PTSD")


async def test_search_skips_non_dict_study_entries_and_says_so(respx_mock):
    payload = search_page([None, "bad", 42, ["nested"], study_record()], total_count=5)
    respx_mock.get(STUDIES_URL).mock(return_value=ok(payload))
    result = await tools.search_studies(condition="PTSD")
    assert len(result["studies"]) == 1
    receipt = result["_harness"]
    assert receipt["returnedCount"] == 1
    assert receipt["projection"]["skippedMalformedStudies"] == 4
    assert any("malformed" in warning for warning in receipt["warnings"])


async def test_search_tolerates_wrongly_typed_modules_and_fields(respx_mock):
    broken = {
        "protocolSection": {
            "identificationModule": "bad",
            "statusModule": ["bad"],
            "descriptionModule": {"briefSummary": {"text": "bad"}},
            "contactsLocationsModule": {"locations": {"facility": "bad"}},
            "armsInterventionsModule": {"interventions": [None, "bad", {"name": "Saline", "description": "x"}]},
        }
    }
    payload = search_page([broken, {"protocolSection": "bad"}, {}], total_count=3)
    respx_mock.get(STUDIES_URL).mock(return_value=ok(payload))
    result = await tools.search_studies(condition="PTSD")
    first, second, third = result["studies"]
    assert first["protocolSection"] == {"armsInterventionsModule": {"interventions": [{"name": "Saline"}]}}
    assert first["_harness"]["locationsTotal"] == 0
    assert second["protocolSection"] == {}
    assert third["protocolSection"] == {}


@pytest.mark.parametrize("body", [b"[]", b"null", b'"study"'])
async def test_details_body_that_is_not_an_object_raises_api_error(respx_mock, body):
    respx_mock.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(ClinicalTrialsApiError, match="unexpected study record format"):
        await tools.get_study_details(NCT)


async def test_details_tolerate_malformed_locations_and_protocol(respx_mock):
    record = study_record()
    record["protocolSection"]["contactsLocationsModule"]["locations"] = "not-a-list"
    respx_mock.get(DETAIL_URL).mock(side_effect=[ok(record), ok({"protocolSection": "bad"})])
    first = await tools.get_study_details(NCT)
    assert first["protocolSection"]["contactsLocationsModule"]["locations"] == []
    assert first["_harness"]["locationsTotal"] == 0
    second = await tools.get_study_details(NCT)
    assert second["protocolSection"] == "bad"  # passed through, not interpreted
    assert second["_harness"]["nctId"] == NCT


async def test_non_json_body_raises_api_error_chained_to_the_decode_error(respx_mock):
    respx_mock.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>", headers={"Content-Type": "text/html"})
    )
    with pytest.raises(ClinicalTrialsApiError, match="not valid JSON") as excinfo:
        await tools.get_study_details(NCT)
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert excinfo.value.retryable is False
    assert excinfo.value.status_code is None


async def test_eligibility_body_that_is_not_an_object_raises_api_error(respx_mock):
    respx_mock.get(DETAIL_URL).mock(return_value=ok(["not", "an", "object"]))
    with pytest.raises(ClinicalTrialsApiError, match="unexpected eligibility record format"):
        await tools.check_eligibility(NCT)


@pytest.mark.parametrize(
    "payload",
    [{"studies": []}, {"totalCount": None}, {"totalCount": "42"}, {"totalCount": True}, {"totalCount": -1}, [], "x"],
)
async def test_count_requires_a_numeric_total(respx_mock, payload):
    respx_mock.get(STUDIES_URL).mock(return_value=ok(payload))
    with pytest.raises(ClinicalTrialsApiError):
        await tools.count_studies(condition="PTSD")


# --------------------------------------------------------------------------- #
# Shared client and non-blocking I/O
# --------------------------------------------------------------------------- #
async def test_pending_upstream_call_does_not_block_the_event_loop(respx_mock):
    release = asyncio.Event()

    async def slow_upstream(request):
        await release.wait()
        return ok({"totalCount": 7})

    respx_mock.get(STUDIES_URL).mock(side_effect=slow_upstream)
    pending = asyncio.create_task(tools.count_studies(condition="PTSD"))
    for _ in range(5):
        await asyncio.sleep(0)  # other coroutines (e.g. a readiness probe) keep running
    assert not pending.done()
    release.set()
    assert await pending == 7


async def test_get_client_is_shared_and_configured():
    client = tools.get_client()
    assert tools.get_client() is client
    assert client.timeout.connect == 5.0
    assert client.timeout.read == 15.0
    user_agent = client.headers["User-Agent"]
    assert user_agent.startswith("clinical-trials-tools/")
    assert "@" not in user_agent  # generic: no contact or personal details


async def test_requests_carry_the_user_agent(respx_mock):
    route = respx_mock.get(ENUMS_URL).mock(return_value=ok(enums_payload()))
    await tools.list_enum_values("Phase")
    assert route.calls.last.request.headers["User-Agent"] == tools.USER_AGENT


async def test_aclose_client_closes_and_resets_the_shared_client():
    client = tools.get_client()
    await tools.aclose_client()
    assert client.is_closed
    replacement = tools.get_client()
    assert replacement is not client
    assert not replacement.is_closed
    await tools.aclose_client()
    await tools.aclose_client()  # safe to call twice


async def test_a_client_closed_elsewhere_is_replaced():
    client = tools.get_client()
    await client.aclose()
    assert tools.get_client() is not client


def test_client_is_recreated_when_the_event_loop_changes():
    async def current_client():
        return tools.get_client()

    first = asyncio.run(current_client())
    second = asyncio.run(current_client())
    assert first is not second
