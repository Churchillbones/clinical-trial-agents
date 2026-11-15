"""Contract fixtures: ClinicalTrials.gov API v2 response bodies.

HAND-BUILT, NOT RECORDED. These payloads were written by hand from the
documented v2 response shapes (https://clinicaltrials.gov/data-api/api, the
endpoint reference and the "Data Structure" study model) because the sandbox
they were written in cannot reach clinicaltrials.gov. Module and field names,
enum values and nesting follow the documentation; titles, text, people and
contact details are invented. Before relying on an exact field name, compare
these against a live response from:

    GET /api/v2/studies?countTotal=true -> {"totalCount", "studies": [Study], "nextPageToken"}
    GET /api/v2/studies/{nctId}         -> Study
    GET /api/v2/studies/enums           -> [{"type", "pieces", "values": [EnumItem]}]

Each builder returns a fresh object so tests can mutate it freely.
"""

from __future__ import annotations

from typing import Any

VA_SITES = [
    ("VA Portland Health Care System", "Portland", "Oregon", "97239"),
    ("Minneapolis VA Medical Center", "Minneapolis", "Minnesota", "55417"),
    ("VA Palo Alto Health Care System", "Palo Alto", "California", "94304"),
]


def location(
    facility: str,
    city: str,
    state: str,
    zip_code: str,
    *,
    status: str = "RECRUITING",
) -> dict[str, Any]:
    """One ``contactsLocationsModule.locations[]`` entry (site contact included)."""
    return {
        "facility": facility,
        "status": status,
        "city": city,
        "state": state,
        "zip": zip_code,
        "country": "United States",
        "contacts": [
            {
                "name": "Site Research Coordinator",
                "role": "CONTACT",
                "phone": "555-555-0100",
                "phoneExt": "1234",
                "email": "site.coordinator@example.org",
            },
            {"name": "Site Investigator, MD", "role": "PRINCIPAL_INVESTIGATOR"},
        ],
        "geoPoint": {"lat": 45.49713, "lon": -122.68382},
    }


def site_locations(count: int, *, va_positions: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    """``count`` locations: non-VA university sites, with VA sites at ``va_positions``."""
    sites = []
    for index in range(count):
        if index in va_positions:
            facility, city, state, zip_code = VA_SITES[va_positions.index(index) % len(VA_SITES)]
        else:
            facility, city, state, zip_code = (
                f"University Research Hospital {index + 1}",
                "Springfield",
                "Illinois",
                "62701",
            )
        sites.append(location(facility, city, state, zip_code))
    return sites


def study_record(
    nct_id: str = "NCT05123456",
    *,
    locations: list[dict[str, Any]] | None = None,
    brief_summary: str | None = None,
    has_results: bool = True,
) -> dict[str, Any]:
    """A complete v2 Study as returned by GET /studies/{nctId} (and in /studies)."""
    criteria = (
        "Inclusion Criteria:\n\n"
        "* Veterans aged 18 to 70 years\n"
        "* DSM-5 diagnosis of posttraumatic stress disorder (CAPS-5 total >= 25)\n"
        "* Inadequate response to at least one first-line treatment\n"
        "* Stable psychotropic medication for at least 4 weeks\n\n"
        "Exclusion Criteria:\n\n"
        "* Lifetime psychotic or bipolar I disorder\n"
        "* Moderate or severe traumatic brain injury\n"
        "* Current suicidal ideation with intent or plan\n"
        "* Pregnancy or breastfeeding\n"
    ) * 6  # real criteria blocks are often several thousand characters
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "orgStudyIdInfo": {"id": "MHBA-010-24S"},
                "secondaryIdInfos": [{"id": "I01CX002412", "type": "OTHER_GRANT"}],
                "organization": {
                    "fullName": "VA Office of Research and Development",
                    "class": "FED",
                },
                "briefTitle": "Stellate Ganglion Block for Treatment-Resistant PTSD",
                "officialTitle": (
                    "A Randomized, Sham-Controlled Trial of Stellate Ganglion Block "
                    "for Veterans With Treatment-Resistant Posttraumatic Stress Disorder"
                ),
                "acronym": "SGB-PTSD",
            },
            "statusModule": {
                "statusVerifiedDate": "2026-09",
                "overallStatus": "RECRUITING",
                "expandedAccessInfo": {"hasExpandedAccess": False},
                "startDateStruct": {"date": "2025-03-01", "type": "ACTUAL"},
                "primaryCompletionDateStruct": {"date": "2028-09-30", "type": "ESTIMATED"},
                "completionDateStruct": {"date": "2029-03-31", "type": "ESTIMATED"},
                "studyFirstSubmitDate": "2024-11-04",
                "studyFirstSubmitQcDate": "2024-11-04",
                "studyFirstPostDateStruct": {"date": "2024-11-08", "type": "ACTUAL"},
                "lastUpdateSubmitDate": "2026-09-15",
                "lastUpdatePostDateStruct": {"date": "2026-09-18", "type": "ACTUAL"},
            },
            "sponsorCollaboratorsModule": {
                "responsibleParty": {"type": "SPONSOR"},
                "leadSponsor": {"name": "VA Office of Research and Development", "class": "FED"},
                "collaborators": [{"name": "National Center for PTSD", "class": "FED"}],
            },
            "oversightModule": {
                "oversightHasDmc": True,
                "isFdaRegulatedDrug": True,
                "isFdaRegulatedDevice": False,
            },
            "descriptionModule": {
                "briefSummary": brief_summary
                or (
                    "This study tests whether an injection of a local anesthetic near "
                    "a nerve bundle in the neck (stellate ganglion block) reduces PTSD "
                    "symptoms in Veterans whose symptoms did not improve enough with "
                    "standard treatment."
                ),
                "detailedDescription": "Participants are randomized to active or sham injection. "
                * 120,
            },
            "conditionsModule": {
                "conditions": ["Post-Traumatic Stress Disorder"],
                "keywords": ["PTSD", "Veterans", "stellate ganglion block"],
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2"],
                "designInfo": {
                    "allocation": "RANDOMIZED",
                    "interventionModel": "PARALLEL",
                    "primaryPurpose": "TREATMENT",
                    "maskingInfo": {
                        "masking": "QUADRUPLE",
                        "whoMasked": [
                            "PARTICIPANT",
                            "CARE_PROVIDER",
                            "INVESTIGATOR",
                            "OUTCOMES_ASSESSOR",
                        ],
                    },
                },
                "enrollmentInfo": {"count": 240, "type": "ESTIMATED"},
            },
            "armsInterventionsModule": {
                "armGroups": [
                    {
                        "label": "Stellate ganglion block",
                        "type": "EXPERIMENTAL",
                        "description": "Ultrasound-guided injection of ropivacaine.",
                        "interventionNames": ["Drug: Ropivacaine"],
                    },
                    {
                        "label": "Sham procedure",
                        "type": "SHAM_COMPARATOR",
                        "description": "Subcutaneous saline injection.",
                        "interventionNames": ["Other: Saline"],
                    },
                ],
                "interventions": [
                    {
                        "type": "DRUG",
                        "name": "Ropivacaine",
                        "description": "7 mL of 0.5% ropivacaine at the C6 level.",
                        "armGroupLabels": ["Stellate ganglion block"],
                        "otherNames": ["Naropin"],
                    },
                    {
                        "type": "OTHER",
                        "name": "Saline",
                        "description": "Subcutaneous saline sham injection.",
                        "armGroupLabels": ["Sham procedure"],
                    },
                ],
            },
            "outcomesModule": {
                "primaryOutcomes": [
                    {
                        "measure": "Change in CAPS-5 total severity score",
                        "description": "Clinician-Administered PTSD Scale for DSM-5.",
                        "timeFrame": "Baseline to 8 weeks",
                    }
                ],
                "secondaryOutcomes": [
                    {
                        "measure": f"Secondary outcome {number}",
                        "description": "Self-reported symptom inventory.",
                        "timeFrame": "Baseline to 12 weeks",
                    }
                    for number in range(1, 9)
                ],
            },
            "eligibilityModule": {
                "eligibilityCriteria": criteria,
                "healthyVolunteers": False,
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "70 Years",
                "stdAges": ["ADULT", "OLDER_ADULT"],
            },
            "contactsLocationsModule": {
                "centralContacts": [
                    {
                        "name": "Central Study Coordinator",
                        "role": "CONTACT",
                        "phone": "555-555-0199",
                        "email": "study.coordinator@example.org",
                    }
                ],
                "overallOfficials": [
                    {
                        "name": "Study Chair, MD PhD",
                        "affiliation": "VA Office of Research and Development",
                        "role": "STUDY_CHAIR",
                    }
                ],
                "locations": (
                    locations if locations is not None else site_locations(3, va_positions=(2,))
                ),
            },
            "referencesModule": {
                "references": [
                    {
                        "pmid": "00000000",
                        "type": "BACKGROUND",
                        "citation": "Example A, Example B. Background citation. J Example. 2020.",
                    }
                ]
            },
            "ipdSharingStatementModule": {"ipdSharing": "NO"},
        },
        "derivedSection": {
            "miscInfoModule": {"versionHolder": "2026-11-13"},
            "conditionBrowseModule": {
                "meshes": [{"id": "D013313", "term": "Stress Disorders, Post-Traumatic"}],
                "ancestors": [{"id": "D040921", "term": "Stress Disorders, Traumatic"}],
                "browseLeaves": [
                    {"id": f"M{number}", "name": f"Browse leaf {number}", "relevance": "LOW"}
                    for number in range(25)
                ],
                "browseBranches": [{"abbrev": "BXM", "name": "Behaviors and Mental Disorders"}],
            },
            "interventionBrowseModule": {
                "meshes": [{"id": "D000077212", "term": "Ropivacaine"}],
                "browseLeaves": [{"id": "M1", "name": "Anesthetics", "relevance": "LOW"}],
            },
        },
        "resultsSection": {
            "participantFlowModule": {
                "groups": [
                    {"id": "FG000", "title": "Active", "description": "Active arm."},
                    {"id": "FG001", "title": "Sham", "description": "Sham arm."},
                ],
                "periods": [
                    {
                        "title": "Overall Study",
                        "milestones": [
                            {
                                "type": "STARTED",
                                "achievements": [
                                    {"groupId": "FG000", "numSubjects": "60"},
                                    {"groupId": "FG001", "numSubjects": "60"},
                                ],
                            }
                        ],
                    }
                ],
            },
            "outcomeMeasuresModule": {
                "outcomeMeasures": [
                    {
                        "type": "PRIMARY",
                        "title": "Change in CAPS-5 total severity score",
                        "description": "Outcome measure description. " * 40,
                        "timeFrame": "Baseline to 8 weeks",
                    }
                ]
            },
            "adverseEventsModule": {
                "frequencyThreshold": "5",
                "timeFrame": "12 weeks",
                "seriousEvents": [
                    {"term": f"Serious event {number}", "organSystem": "General disorders"}
                    for number in range(30)
                ],
            },
            "moreInfoModule": {"pointOfContact": {"title": "Results Contact"}},
        },
        "documentSection": {
            "largeDocumentModule": {
                "largeDocs": [
                    {
                        "typeAbbrev": "Prot_SAP",
                        "hasProtocol": True,
                        "hasSap": True,
                        "hasIcf": False,
                        "label": "Study Protocol and Statistical Analysis Plan",
                        "date": "2024-10-01",
                        "uploadDate": "2024-11-04T10:00",
                        "filename": "Prot_SAP_000.pdf",
                        "size": 812345,
                    }
                ]
            }
        },
        "annotationSection": {
            "annotationModule": {
                "unpostedAnnotation": {
                    "unpostedResponsibleParty": "VA Office of Research and Development",
                    "unpostedEvents": [{"type": "RESET", "date": "2026-01-15"}],
                }
            }
        },
        "hasResults": has_results,
    }


def search_page(
    studies: list[Any],
    *,
    total_count: int | None = None,
    next_page_token: str | None = "NF0g5JiEk_Ew",
) -> dict[str, Any]:
    """A GET /studies page; ``totalCount`` is only present when countTotal=true."""
    page: dict[str, Any] = {"studies": studies}
    if total_count is not None:
        page["totalCount"] = total_count
    if next_page_token is not None:
        page["nextPageToken"] = next_page_token
    return page


def enums_payload() -> list[dict[str, Any]]:
    """GET /studies/enums: a JSON array of enum descriptors (subset of the real list)."""

    def items(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
        return [{"value": value, "legacyValue": legacy} for value, legacy in pairs]

    return [
        {
            "type": "Status",
            "pieces": ["OverallStatus", "LastKnownStatus"],
            "values": items(
                ("ACTIVE_NOT_RECRUITING", "Active, not recruiting"),
                ("COMPLETED", "Completed"),
                ("ENROLLING_BY_INVITATION", "Enrolling by invitation"),
                ("NOT_YET_RECRUITING", "Not yet recruiting"),
                ("RECRUITING", "Recruiting"),
                ("SUSPENDED", "Suspended"),
                ("TERMINATED", "Terminated"),
                ("WITHDRAWN", "Withdrawn"),
                ("AVAILABLE", "Available"),
                ("NO_LONGER_AVAILABLE", "No longer available"),
                ("TEMPORARILY_NOT_AVAILABLE", "Temporarily not available"),
                ("APPROVED_FOR_MARKETING", "Approved for marketing"),
                ("WITHHELD", "Withheld"),
                ("UNKNOWN", "Unknown status"),
            ),
        },
        {
            "type": "Phase",
            "pieces": ["Phase"],
            "values": [
                {"value": "NA", "legacyValue": "N/A", "exceptions": {"Phase": "Not Applicable"}},
                {
                    "value": "EARLY_PHASE1",
                    "legacyValue": "Early Phase 1",
                    "exceptions": {"Phase": "Early Phase 1"},
                },
                {"value": "PHASE1", "legacyValue": "Phase 1"},
                {"value": "PHASE2", "legacyValue": "Phase 2"},
                {"value": "PHASE3", "legacyValue": "Phase 3"},
                {"value": "PHASE4", "legacyValue": "Phase 4"},
            ],
        },
        {
            "type": "StudyType",
            "pieces": ["StudyType"],
            "values": items(
                ("EXPANDED_ACCESS", "Expanded Access"),
                ("INTERVENTIONAL", "Interventional"),
                ("OBSERVATIONAL", "Observational"),
            ),
        },
        {
            "type": "InterventionType",
            "pieces": ["InterventionType"],
            "values": items(
                ("BEHAVIORAL", "Behavioral"),
                ("BIOLOGICAL", "Biological"),
                ("COMBINATION_PRODUCT", "Combination Product"),
                ("DEVICE", "Device"),
                ("DIAGNOSTIC_TEST", "Diagnostic Test"),
                ("DIETARY_SUPPLEMENT", "Dietary Supplement"),
                ("DRUG", "Drug"),
                ("GENETIC", "Genetic"),
                ("PROCEDURE", "Procedure"),
                ("RADIATION", "Radiation"),
                ("OTHER", "Other"),
            ),
        },
        {
            "type": "Sex",
            "pieces": ["Sex"],
            "values": items(("FEMALE", "Female"), ("MALE", "Male"), ("ALL", "All")),
        },
        {
            "type": "StandardAge",
            "pieces": ["StdAge"],
            "values": items(
                ("CHILD", "Child"), ("ADULT", "Adult"), ("OLDER_ADULT", "Older Adult")
            ),
        },
        {
            "type": "RecruitmentStatus",
            "pieces": ["LocationStatus"],
            "values": items(
                ("ACTIVE_NOT_RECRUITING", "Active, not recruiting"),
                ("COMPLETED", "Completed"),
                ("ENROLLING_BY_INVITATION", "Enrolling by invitation"),
                ("NOT_YET_RECRUITING", "Not yet recruiting"),
                ("RECRUITING", "Recruiting"),
                ("SUSPENDED", "Suspended"),
                ("TERMINATED", "Terminated"),
                ("WITHDRAWN", "Withdrawn"),
                ("AVAILABLE", "Available"),
            ),
        },
    ]
