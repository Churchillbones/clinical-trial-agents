"""ClinicalTrials.gov API v2 tool implementations with clinical safety receipts.

All network I/O is async on one shared ``httpx.AsyncClient`` so a slow upstream
never blocks the server's event loop. Transient upstream failures are retried,
and every upstream failure is raised as ``ClinicalTrialsApiError`` with a
message that is safe to show a language model or clinician. Input validation
and PHI screening failures raise ``ValueError``. Tool arguments and upstream
bodies are never logged (they may contain PHI).

Search results and study details are projected client-side to what the agent
needs instead of returning complete records; ``_harness.projection`` says so.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import Any

import httpx

from clinical_trial_harness import (
    build_eligibility_receipt,
    build_search_receipt,
    clinical_trials_anchor,
    prioritize_va_locations,
    referral_authority_envelope,
    require_deidentified_text,
    utc_now_iso,
    validate_nct_id,
)

logger = logging.getLogger(__name__)

CTG_BASE_URL = "https://clinicaltrials.gov/api/v2"
USER_AGENT = (
    "clinical-trials-tools/1.0 (ClinicalTrials.gov API v2 client) "
    f"python-httpx/{httpx.__version__}"
)
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
HTTP_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)

# Retry policy: only transient failures, at most MAX_ATTEMPTS calls in total.
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 4.0
RETRY_AFTER_MAX_SECONDS = 5.0

# /studies/enums is static reference data, so it is fetched at most this often.
ENUMS_CACHE_TTL_SECONDS = 12 * 60 * 60

# Client-side projection limits (described in each result's _harness).
SEARCH_MAX_LOCATIONS = 5
SEARCH_SUMMARY_MAX_CHARS = 500
DETAILS_MAX_LOCATIONS = 25

# Indirection so tests can replace the backoff sleep and never sleep for real.
_sleep = asyncio.sleep


class ClinicalTrialsApiError(Exception):
    """A ClinicalTrials.gov failure: error status, transport error or bad body.

    ``str(error)`` is safe to show a language model or clinician: it never
    contains tool arguments, patient text or upstream bodies. ``status_code``
    is the upstream HTTP status when one caused the failure, else ``None``.
    ``retryable`` is True when the same call may succeed later (rate limited,
    upstream unavailable, timeout or network failure).
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


# --------------------------------------------------------------------------- #
# Shared HTTP client and request helper
# --------------------------------------------------------------------------- #
_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_client() -> httpx.AsyncClient:
    """Return the shared ClinicalTrials.gov client, creating it on first use.

    Creation is lazy so TLS trust configured at startup (``truststore`` in
    main.py) applies. The client is replaced if it was closed or if the running
    event loop changed (pytest runs each test on its own loop), because pooled
    connections belong to the loop that opened them.
    """
    global _client, _client_loop
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if (
        _client is None
        or _client.is_closed
        or (loop is not None and loop is not _client_loop)
    ):
        _client = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            limits=HTTP_LIMITS,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        _client_loop = loop
    return _client


async def aclose_client() -> None:
    """Close and forget the shared client (call on shutdown and between tests)."""
    global _client, _client_loop
    client, _client, _client_loop = _client, None, None
    if client is not None:
        await client.aclose()


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter for a 1-based attempt: ~0.5s, ~1s, ~2s."""
    ceiling = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
    return random.uniform(ceiling / 2, ceiling)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """A numeric Retry-After header in seconds (capped); None if absent or a date."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_MAX_SECONDS)


def _status_error(
    status: int, attempts: int, not_found: str | None
) -> ClinicalTrialsApiError:
    """Map a final non-2xx upstream status to a safe, actionable error."""
    retryable = status in RETRYABLE_STATUS_CODES
    if status == 404 and not_found:
        message = not_found
    elif status == 429:
        message = (
            f"ClinicalTrials.gov is rate limiting requests (HTTP 429) after "
            f"{attempts} attempts; try again shortly"
        )
    elif retryable:
        message = (
            f"ClinicalTrials.gov is temporarily unavailable (HTTP {status}) after "
            f"{attempts} attempts"
        )
    elif status >= 500:
        message = f"ClinicalTrials.gov returned a server error (HTTP {status})"
    elif status == 400:
        message = (
            "ClinicalTrials.gov rejected the request as invalid (HTTP 400); "
            "check the search values"
        )
    elif status >= 400:
        message = f"ClinicalTrials.gov rejected the request (HTTP {status})"
    else:
        message = f"ClinicalTrials.gov returned an unexpected response (HTTP {status})"
    return ClinicalTrialsApiError(message, status_code=status, retryable=retryable)


async def _get_json(
    path: str, *, params: dict[str, Any] | None = None, not_found: str | None = None
) -> Any:
    """GET ``CTG_BASE_URL + path`` and return the decoded JSON body.

    Retries HTTP 429/502/503/504 and transient transport errors (timeouts,
    network errors) up to MAX_ATTEMPTS calls in total, waiting a capped numeric
    Retry-After or else a jittered exponential backoff. Any failure is raised
    as ClinicalTrialsApiError; ``not_found`` is the message for an HTTP 404.
    Logs carry only the path, status, attempt and error type.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            response = await get_client().get(CTG_BASE_URL + path, params=params)
        except _RETRYABLE_TRANSPORT_ERRORS as exc:
            error_type = type(exc).__name__
            if attempt >= MAX_ATTEMPTS:
                logger.warning(
                    "ClinicalTrials.gov %s: %s on attempt %d/%d; giving up",
                    path, error_type, attempt, MAX_ATTEMPTS,
                )
                if isinstance(exc, httpx.TimeoutException):
                    message = (
                        f"ClinicalTrials.gov did not respond in time after "
                        f"{attempt} attempts"
                    )
                else:
                    message = f"Could not reach ClinicalTrials.gov after {attempt} attempts"
                raise ClinicalTrialsApiError(message, retryable=True) from exc
            delay = _backoff_seconds(attempt)
            logger.warning(
                "ClinicalTrials.gov %s: %s on attempt %d/%d; retrying in %.2fs",
                path, error_type, attempt, MAX_ATTEMPTS, delay,
            )
            await _sleep(delay)
            continue
        except httpx.RequestError as exc:
            logger.warning(
                "ClinicalTrials.gov %s: %s (not retried)", path, type(exc).__name__
            )
            raise ClinicalTrialsApiError(
                "The request to ClinicalTrials.gov could not be completed"
            ) from exc

        status = response.status_code
        if status in RETRYABLE_STATUS_CODES and attempt < MAX_ATTEMPTS:
            delay = _retry_after_seconds(response)
            if delay is None:
                delay = _backoff_seconds(attempt)
            logger.warning(
                "ClinicalTrials.gov %s: HTTP %d on attempt %d/%d; retrying in %.2fs",
                path, status, attempt, MAX_ATTEMPTS, delay,
            )
            await _sleep(delay)
            continue
        if not response.is_success:
            level = logging.WARNING if status >= 500 or status == 429 else logging.INFO
            logger.log(
                level,
                "ClinicalTrials.gov %s: HTTP %d on attempt %d/%d; giving up",
                path, status, attempt, MAX_ATTEMPTS,
            )
            raise _status_error(status, attempt, not_found)
        try:
            return response.json()
        except ValueError as exc:
            logger.warning("ClinicalTrials.gov %s: response body is not JSON", path)
            raise ClinicalTrialsApiError(
                "ClinicalTrials.gov returned a response that is not valid JSON"
            ) from exc


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Copy the ``keys`` that have a value in ``source``; never invent missing ones."""
    return {key: source[key] for key in keys if source.get(key) is not None}


def _expect_object(payload: Any, what: str) -> dict[str, Any]:
    """Reject a JSON body that is not an object rather than failing later."""
    if not isinstance(payload, dict):
        raise ClinicalTrialsApiError(f"ClinicalTrials.gov returned an unexpected {what} format")
    return payload


def _not_found(nct_id: str) -> str:
    return f"Study {nct_id} was not found on ClinicalTrials.gov"


def _va_first_locations(locations: Any, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Valid source locations, VA first, capped to ``limit``; plus how many there were."""
    ordered = prioritize_va_locations(_as_list(locations))
    return ordered[:limit], len(ordered)


def _resolved_nct_id(record: dict[str, Any], requested_id: str) -> str:
    """The record's own NCT id if well-formed, else the (validated) id requested."""
    ident = _as_dict(_as_dict(record.get("protocolSection")).get("identificationModule"))
    try:
        return validate_nct_id(ident.get("nctId") or requested_id)
    except ValueError:
        return requested_id


def _screen_outbound_text(**fields: Any) -> None:
    """PHI-screen model-supplied values before any of them can leave the pod."""
    for field_name, value in fields.items():
        require_deidentified_text(value, field_name=field_name)


def _project_search_study(study: dict[str, Any]) -> dict[str, Any]:
    """Compact view of one search hit that keeps the source protocolSection paths."""
    protocol = _as_dict(study.get("protocolSection"))
    ident = _as_dict(protocol.get("identificationModule"))
    status = _as_dict(protocol.get("statusModule"))
    sponsors = _as_dict(protocol.get("sponsorCollaboratorsModule"))
    conditions = _as_dict(protocol.get("conditionsModule"))
    arms = _as_dict(protocol.get("armsInterventionsModule"))
    design = _as_dict(protocol.get("designModule"))
    description = _as_dict(protocol.get("descriptionModule"))
    eligibility = _as_dict(protocol.get("eligibilityModule"))
    contacts = _as_dict(protocol.get("contactsLocationsModule"))

    summary = description.get("briefSummary")
    summary_truncated = isinstance(summary, str) and len(summary) > SEARCH_SUMMARY_MAX_CHARS
    if summary_truncated:
        summary = summary[:SEARCH_SUMMARY_MAX_CHARS].rstrip() + "…"
    lead_sponsor = _pick(_as_dict(sponsors.get("leadSponsor")), "name")
    interventions = [
        _pick(item, "type", "name")
        for item in _as_list(arms.get("interventions"))
        if isinstance(item, dict)
    ]
    locations, locations_total = _va_first_locations(
        contacts.get("locations"), SEARCH_MAX_LOCATIONS
    )

    projected = {
        "identificationModule": _pick(ident, "nctId", "briefTitle", "officialTitle"),
        "statusModule": _pick(
            status, "overallStatus", "startDateStruct", "primaryCompletionDateStruct"
        ),
        "sponsorCollaboratorsModule": {"leadSponsor": lead_sponsor} if lead_sponsor else {},
        "conditionsModule": _pick(conditions, "conditions"),
        "armsInterventionsModule": {"interventions": interventions} if interventions else {},
        "designModule": _pick(design, "studyType", "phases", "enrollmentInfo"),
        "descriptionModule": {"briefSummary": summary} if isinstance(summary, str) else {},
        "eligibilityModule": _pick(eligibility, "sex", "minimumAge", "maximumAge", "stdAges"),
        "contactsLocationsModule": (
            {
                "locations": [
                    _pick(location, "facility", "city", "state", "country", "status")
                    for location in locations
                ]
            }
            if locations
            else {}
        ),
    }
    return {
        "protocolSection": {name: value for name, value in projected.items() if value},
        "_harness": {
            "locationsTotal": locations_total,
            "locationsReturned": len(locations),
            "locationsTruncated": locations_total > len(locations),
            "briefSummaryTruncated": summary_truncated,
        },
    }


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
async def search_studies(
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
    """Search ClinicalTrials.gov; return compact candidates and a search receipt.

    Each study is a projection of its source record (``_harness.projection``);
    get_study_details returns one study's protocol record.
    """
    _screen_outbound_text(
        term=term,
        condition=condition,
        intervention=intervention,
        location=location,
        sponsor=sponsor,
        age=age,
        sex=sex,
        status=status,
        phase=phase,
    )
    try:
        bounded_page_size = max(1, min(int(page_size), 20))
    except (TypeError, ValueError):
        bounded_page_size = 10

    params: dict[str, str | int] = {"pageSize": bounded_page_size, "countTotal": "true"}
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

    payload = _expect_object(await _get_json("/studies", params=params), "search response")
    raw_studies = payload.get("studies")
    if raw_studies is None:
        raw_studies = []
    if not isinstance(raw_studies, list):
        raise ClinicalTrialsApiError(
            "ClinicalTrials.gov returned an unexpected search response format"
        )
    studies = [_project_search_study(study) for study in raw_studies if isinstance(study, dict)]
    skipped = len(raw_studies) - len(studies)
    if skipped:
        logger.warning("ClinicalTrials.gov /studies: skipped %d malformed study entries", skipped)

    result: dict[str, Any] = {
        "studies": studies,
        "totalCount": payload.get("totalCount"),
        "nextPageToken": payload.get("nextPageToken"),
    }
    receipt = build_search_receipt(
        query={
            "term": term,
            "condition": condition,
            "intervention": intervention,
            "location": location,
            "sponsor": sponsor,
            "age": age,
            "sex": sex,
            "status": status,
            "phase": phase,
            "pageSize": bounded_page_size,
        },
        result=result,
    )
    if skipped:
        receipt["warnings"].append(
            f"{skipped} malformed study entries in the ClinicalTrials.gov response "
            "were omitted."
        )
    receipt["projection"] = {
        "view": "search_summary",
        "isFullRecord": False,
        "note": (
            "Compact per-study view: full eligibility criteria, contacts, outcomes, "
            "results and other record sections are omitted, briefSummary may be "
            "truncated, and locations are VA-first and capped (see each study's "
            "_harness). Use get_study_details or check_eligibility for a study's "
            "source record."
        ),
        "briefSummaryMaxChars": SEARCH_SUMMARY_MAX_CHARS,
        "maxLocationsPerStudy": SEARCH_MAX_LOCATIONS,
        "skippedMalformedStudies": skipped,
    }
    result["_harness"] = receipt
    return result


async def get_study_details(nct_id: str) -> dict:
    """Retrieve one study's protocol record by NCT ID (projected; see _harness)."""
    requested_id = validate_nct_id(nct_id)
    record = _expect_object(
        await _get_json(f"/studies/{requested_id}", not_found=_not_found(requested_id)),
        "study record",
    )
    result: dict[str, Any] = {
        key: record[key] for key in ("protocolSection", "hasResults") if key in record
    }
    omitted_sections = sorted(key for key in record if key not in result)

    locations_total = locations_returned = 0
    protocol = record.get("protocolSection")
    contacts = protocol.get("contactsLocationsModule") if isinstance(protocol, dict) else None
    if isinstance(contacts, dict) and "locations" in contacts:
        locations, locations_total = _va_first_locations(
            contacts["locations"], DETAILS_MAX_LOCATIONS
        )
        locations_returned = len(locations)
        result["protocolSection"] = {
            **protocol,
            "contactsLocationsModule": {**contacts, "locations": locations},
        }

    result["_harness"] = {
        "source": {
            "system": "ClinicalTrials.gov API v2",
            "retrievedAt": utc_now_iso(),
        },
        "nctId": _resolved_nct_id(record, requested_id),
        "assessmentBoundary": (
            "Study details are source facts. Any patient-specific fit remains a "
            "clinician-reviewed interpretation."
        ),
        "projection": {
            "view": "study_details",
            "isFullRecord": False,
            "note": (
                "protocolSection and hasResults only: results, derived, document and "
                "annotation sections are omitted, and locations are VA-first and "
                "capped at maxLocations."
            ),
            "omittedSections": omitted_sections,
            "maxLocations": DETAILS_MAX_LOCATIONS,
        },
        "locationsTotal": locations_total,
        "locationsReturned": locations_returned,
        "locationsTruncated": locations_total > locations_returned,
    }
    return result


async def check_eligibility(nct_id: str) -> dict:
    """Retrieve eligibility source fields without determining eligibility.

    Fields absent from the source record are None, never filled with defaults.
    """
    normalized_id = validate_nct_id(nct_id)
    data = _expect_object(
        await _get_json(
            f"/studies/{normalized_id}",
            params={"fields": "EligibilityCriteria,EligibilityModule"},
            not_found=_not_found(normalized_id),
        ),
        "eligibility record",
    )
    protocol = _as_dict(data.get("protocolSection"))
    module = _as_dict(protocol.get("eligibilityModule"))
    return {
        "nctId": normalized_id,
        "eligibilityCriteria": module.get("eligibilityCriteria"),
        "sex": module.get("sex"),
        "minimumAge": module.get("minimumAge"),
        "maximumAge": module.get("maximumAge"),
        "healthyVolunteers": module.get("healthyVolunteers"),
        "stdAges": module.get("stdAges"),
        "_harness": build_eligibility_receipt(normalized_id),
    }


async def count_studies(
    term: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    status: str | None = None,
) -> int:
    """Count studies matching the given criteria."""
    _screen_outbound_text(
        term=term,
        condition=condition,
        intervention=intervention,
        sponsor=sponsor,
        status=status,
    )
    params: dict[str, str | int] = {}
    if term:
        params["query.term"] = term
    if condition:
        params["query.cond"] = condition
    if intervention:
        params["query.intr"] = intervention
    if sponsor:
        params["query.spons"] = sponsor
    if status:
        params["filter.overallStatus"] = status
    params["countTotal"] = "true"
    params["pageSize"] = 1

    payload = _expect_object(await _get_json("/studies", params=params), "study count")
    total = payload.get("totalCount")
    # A missing count is an upstream failure, not "no matching studies".
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ClinicalTrialsApiError("ClinicalTrials.gov did not return a study count")
    return total


_enums_cache: tuple[float, list[Any] | dict[str, Any]] | None = None


def reset_enums_cache() -> None:
    """Forget the cached /studies/enums payload (used by tests)."""
    global _enums_cache
    _enums_cache = None


async def _get_enums_payload() -> list[Any] | dict[str, Any]:
    """The /studies/enums payload, cached in-process for ENUMS_CACHE_TTL_SECONDS."""
    global _enums_cache
    if _enums_cache is not None:
        fetched_at, payload = _enums_cache
        if time.monotonic() - fetched_at < ENUMS_CACHE_TTL_SECONDS:
            return payload
    payload = await _get_json("/studies/enums")
    if not isinstance(payload, (list, dict)):
        raise ClinicalTrialsApiError("ClinicalTrials.gov returned an unexpected enum list format")
    if payload:  # never pin an empty answer for the whole TTL
        _enums_cache = (time.monotonic(), payload)
    return payload


def _enum_value_strings(values: Any) -> list[str]:
    """The ``value`` strings of v2 enum items; plain string items are accepted too."""
    result = []
    for item in _as_list(values):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, str) and value:
            result.append(value)
    return result


async def list_enum_values(enum_name: str) -> list[str]:
    """List valid values for a ClinicalTrials.gov coded field.

    ``enum_name`` matches an enum ``type`` (e.g. "Phase", "Status") or one of
    its ``pieces`` (e.g. "OverallStatus"), case-insensitively. Unknown names
    return an empty list.
    """
    wanted = str(enum_name or "").strip().lower()
    if not wanted:
        raise ValueError("enum_name is required, e.g. 'Phase' or 'OverallStatus'")
    payload = await _get_enums_payload()

    if isinstance(payload, dict):  # legacy {"Phase": [...]} shape, tolerated defensively
        for key, values in payload.items():
            if str(key).lower() == wanted:
                return _enum_value_strings(values)
        return []

    # v2 shape: [{"type": "Phase", "pieces": ["Phase"], "values": [{"value": ...}]}]
    entries = [entry for entry in payload if isinstance(entry, dict)]
    for entry in entries:
        if str(entry.get("type") or "").lower() == wanted:
            return _enum_value_strings(entry.get("values"))
    for entry in entries:
        if any(str(piece).lower() == wanted for piece in _as_list(entry.get("pieces"))):
            return _enum_value_strings(entry.get("values"))
    return []


async def generate_trial_summary(nct_id: str, patient_context: str | None = None) -> dict:
    """Build a source-grounded, patient-friendly summary payload for a study."""
    normalized_id = validate_nct_id(nct_id)
    require_deidentified_text(patient_context, field_name="patient_context")

    study = await get_study_details(normalized_id)
    details_receipt = study["_harness"]
    # Same tolerant id resolution as get_study_details (never re-validated here).
    resolved_id = details_receipt["nctId"]
    protocol = _as_dict(study.get("protocolSection"))
    ident = _as_dict(protocol.get("identificationModule"))
    desc = _as_dict(protocol.get("descriptionModule"))
    elig = _as_dict(protocol.get("eligibilityModule"))
    status = _as_dict(protocol.get("statusModule"))
    contacts_locations = _as_dict(protocol.get("contactsLocationsModule"))
    locations = prioritize_va_locations(_as_list(contacts_locations.get("locations")))

    return {
        "nctId": resolved_id,
        "title": ident.get("briefTitle") or "",
        "plainLanguageSummary": desc.get("briefSummary") or ident.get("briefTitle") or "",
        "whatParticipationInvolves": desc.get("detailedDescription") or "",
        "eligibilityHighlights": {
            # Absent source fields stay None instead of defaults like "ALL" or "No".
            "criteria": elig.get("eligibilityCriteria"),
            "sex": elig.get("sex"),
            "minimumAge": elig.get("minimumAge"),
            "maximumAge": elig.get("maximumAge"),
            "healthyVolunteers": elig.get("healthyVolunteers"),
        },
        "overallStatus": status.get("overallStatus") or "",
        "contactInfo": {
            "centralContacts": _as_list(contacts_locations.get("centralContacts")),
            "locations": locations,
        },
        "link": f"https://clinicaltrials.gov/study/{resolved_id}",
        "patientContext": patient_context,
        "_harness": {
            "patientContextStatus": (
                "validated_deidentified" if patient_context else "not_supplied"
            ),
            "assessmentBoundary": (
                "This handout describes the study and visible criteria; it does "
                "not establish patient eligibility."
            ),
            "evidenceAnchors": [
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.descriptionModule.briefSummary",
                ),
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.eligibilityModule.eligibilityCriteria",
                ),
                clinical_trials_anchor(
                    resolved_id,
                    "protocolSection.contactsLocationsModule.locations",
                ),
            ],
            "source": details_receipt["source"],
            "locationsTotal": details_receipt["locationsTotal"],
            "locationsReturned": details_receipt["locationsReturned"],
            "locationsTruncated": details_receipt["locationsTruncated"],
        },
    }


async def draft_referral_message(
    nct_id: str,
    patient_summary: str,
    clinician_name: str | None = None,
    clinician_facility: str | None = None,
) -> dict:
    """Draft a de-identified referral message without transmitting anything."""
    normalized_id = validate_nct_id(nct_id)
    require_deidentified_text(patient_summary, field_name="patient_summary")

    study_url = f"https://clinicaltrials.gov/study/{normalized_id}"
    signature_name = clinician_name or "[Referring Clinician]"
    signature_facility = clinician_facility or "[VA Facility]"

    message_body = (
        f"To the Study Coordinator for {normalized_id},\n\n"
        f"I am a clinician at {signature_facility} writing to inquire about "
        f"enrollment in your study ({normalized_id}, {study_url}).\n\n"
        f"I am caring for a patient who may be a candidate. De-identified "
        f"clinical context: {patient_summary}\n\n"
        f"Could you share the current enrollment status, key eligibility "
        f"requirements, and the preferred process for referring a patient?\n\n"
        f"Thank you,\n"
        f"{signature_name}\n"
        f"{signature_facility}"
    )

    return {
        "nctId": normalized_id,
        "messageBody": message_body,
        "disclaimer": (
            "Review before sending. Do NOT include PHI (patient name, DOB, SSN, "
            "MRN, or other identifiable details) in this message. Share "
            "identifiable information only through approved secure channels."
        ),
        "_harness": {
            "phiValidation": "passed_deterministic_screen",
            "authority": referral_authority_envelope(),
        },
    }
