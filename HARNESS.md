# Clinical Trials Agent Harness

This repository keeps clinical safety and evidence contracts outside the model
prompt so the agent cannot silently relax them.

## Job boundary

The agent finds potentially relevant studies and prepares evidence for clinician
review. It does not determine eligibility, recommend a study, enroll a patient,
contact a site, transmit a referral, or write to a clinical record.

## Deterministic controls

`clinical_trial_harness.py` owns:

- ClinicalTrials.gov NCT identifier validation
- conservative screening for common direct identifiers, applied to patient
  context, referral text, and every free-text search or count input before it
  is sent to ClinicalTrials.gov
- source-freshness and search proof receipts
- eligibility evidence anchors and allowed classifications
- VA-first location ordering
- draft-only referral authority envelopes

These controls supplement clinical and privacy review. The PHI screen is not a
complete de-identification service and must not be treated as one.

## Tool receipts

Tool responses preserve their existing top-level fields and add a `_harness`
object. Callers should treat this object as part of the clinical contract.

### Search receipt

`search_studies` returns:

- search job and harness versions
- exact non-empty query fields
- retrieval timestamp and source system
- result counts
- an explicit non-eligibility boundary
- warnings when supplied context was not applied as an API filter
- recovery options for empty results
- `projection`: marks the result as a compact view (`isFullRecord: false`) and
  records the summary length limit, the per-study location cap, and how many
  malformed upstream entries were skipped

Each study in the result also carries its own `_harness` with
`locationsTotal`, `locationsReturned`, `locationsTruncated`, and
`briefSummaryTruncated`.

### Study details and summary receipts

`get_study_details` returns only `protocolSection` and `hasResults`. Its receipt
adds `projection` (with `omittedSections` and `maxLocations`) plus
`locationsTotal`, `locationsReturned`, and `locationsTruncated`. Locations are
ordered VA-first before the cap is applied, so a truncated list can omit
non-VA sites; check `locationsTruncated` before treating the list as complete.
`generate_trial_summary` carries the same location counts and the source
timestamp.

Eligibility fields that are absent from the source record are returned as
`null`. The tools never substitute defaults such as `ALL`, `N/A`, or `No`.

### Eligibility receipt

`check_eligibility` returns stable ClinicalTrials.gov evidence anchors and limits
patient-specific interpretation to:

- `potentially_satisfied`
- `potential_conflict`
- `not_assessable`
- `not_applicable`

The conclusions `eligible`, `ineligible`, and `recommended_trial` are prohibited.

### Referral authority

`draft_referral_message` screens the patient summary for common direct
identifiers and returns a proposed, non-transmitted draft. Permitted actions are
copy, edit, and dismiss. Sending, submitting, contacting the site, and enrolling
the patient remain unavailable.

## Tool errors

The agent-facing tools never raise. A failed call returns:

```json
{"error": {"type": "...", "message": "...", "retryable": false}}
```

`type` is one of `invalid_input`, `phi_detected`, `not_found`,
`upstream_unavailable`, or `internal`. Messages never contain the rejected
text or raw exception details. `count_studies` returns `{"count": n}` and
`list_enum_values` returns `{"enumName": ..., "values": [...]}`.

## Evidence anchor format

ClinicalTrials.gov fields use:

```text
ctg:{NCT_ID}:{response_field_path}
```

Example:

```text
ctg:NCT01234567:protocolSection.eligibilityModule.eligibilityCriteria
```

The path identifies the source field used for a finding. It does not by itself
establish patient eligibility.

## Validation

Run:

```bash
pytest -q
```

The harness tests cover NCT validation, PHI pattern screening, evidence receipts,
VA location priority, referral authority, and compatibility with existing tool
response shapes.
