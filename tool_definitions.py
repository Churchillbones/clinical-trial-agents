"""FunctionTool definitions for the Clinical Trials Search agent.

Schemas mirror the tool definitions in spec.md. Each tool is declared with
``strict=True`` so the model emits arguments that match the JSON schema.
"""

from azure.ai.projects.models import FunctionTool

search_studies_tool = FunctionTool(
    name="search_studies",
    description=(
        "Search ClinicalTrials.gov for clinical studies. Returns a list of "
        "matching studies with key details. Use this whenever the user asks "
        "about clinical trials, studies, or research for a condition, "
        "intervention, or location."
    ),
    parameters={
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "General search term (searches title, condition, intervention, summary, etc.)",
            },
            "condition": {
                "type": "string",
                "description": "Patient's condition or diagnosis (e.g. 'PTSD', 'type 2 diabetes', 'non-small cell lung cancer')",
            },
            "intervention": {
                "type": "string",
                "description": "Treatment or intervention of interest (e.g. 'psilocybin', 'CBT', 'immunotherapy')",
            },
            "location": {
                "type": "string",
                "description": "City, state, or VA facility name near the patient (e.g. 'San Francisco VA', 'Texas')",
            },
            "sponsor": {
                "type": "string",
                "description": "Sponsor or collaborator name (e.g. 'Department of Veterans Affairs')",
            },
            "age": {
                "type": "string",
                "description": "Patient age or age group to filter eligible studies (e.g. '62', 'OLDER_ADULT')",
            },
            "sex": {
                "type": "string",
                "enum": ["MALE", "FEMALE", "ALL"],
                "description": "Patient sex for eligibility filtering",
            },
            "status": {
                "type": "string",
                "enum": [
                    "RECRUITING",
                    "NOT_YET_RECRUITING",
                    "ACTIVE_NOT_RECRUITING",
                    "COMPLETED",
                    "ENROLLING_BY_INVITATION",
                    "SUSPENDED",
                    "TERMINATED",
                    "WITHDRAWN",
                ],
                "description": "Filter by recruitment status. Defaults to RECRUITING if not specified.",
            },
            "phase": {
                "type": "string",
                "enum": ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"],
                "description": "Filter by study phase",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of results to return (1-20, default 10)",
            },
        },
        "required": [],
    },
    strict=True,
)

get_study_details_tool = FunctionTool(
    name="get_study_details",
    description=(
        "Retrieve the complete details of a clinical trial by NCT ID. Returns "
        "eligibility criteria, study locations with contact info, enrollment "
        "status, and study design. Use when a clinician wants to evaluate "
        "whether a specific trial is right for their patient."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nct_id": {
                "type": "string",
                "description": "The NCT identifier (e.g. 'NCT12345678')",
            }
        },
        "required": ["nct_id"],
    },
    strict=True,
)

check_eligibility_tool = FunctionTool(
    name="check_eligibility",
    description=(
        "Retrieve the eligibility criteria, age range, sex, and healthy "
        "volunteer status for a study. Use when a clinician wants to quickly "
        "check if their patient might qualify without pulling the full study "
        "record."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nct_id": {
                "type": "string",
                "description": "The NCT identifier (e.g. 'NCT12345678')",
            }
        },
        "required": ["nct_id"],
    },
    strict=True,
)

count_studies_tool = FunctionTool(
    name="count_studies",
    description=(
        "Count how many clinical studies match the given criteria. Use when "
        "the user asks 'how many' studies exist for a condition or filter."
    ),
    parameters={
        "type": "object",
        "properties": {
            "term": {"type": "string", "description": "General search term"},
            "condition": {"type": "string", "description": "Condition or disease"},
            "intervention": {
                "type": "string",
                "description": "Intervention or treatment",
            },
            "sponsor": {"type": "string", "description": "Sponsor name"},
            "status": {"type": "string", "description": "Recruitment status filter"},
        },
        "required": [],
    },
    strict=True,
)

list_enum_values_tool = FunctionTool(
    name="list_enum_values",
    description=(
        "List the allowed values for a ClinicalTrials.gov coded field. Use "
        "when the user asks what phases, statuses, or types are available."
    ),
    parameters={
        "type": "object",
        "properties": {
            "enum_name": {
                "type": "string",
                "description": "Name of the enum (e.g. 'Phase', 'OverallStatus', 'InterventionType', 'StudyType')",
            }
        },
        "required": ["enum_name"],
    },
    strict=True,
)

generate_trial_summary_tool = FunctionTool(
    name="generate_trial_summary",
    description=(
        "Generate a printable, patient-friendly summary of a clinical trial. "
        "Includes study purpose in plain language, what participation involves, "
        "eligibility highlights, site location, and contact info. Use when the "
        "clinician wants a handout to share with the patient."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nct_id": {
                "type": "string",
                "description": "The NCT identifier (e.g. 'NCT12345678')",
            },
            "patient_context": {
                "type": "string",
                "description": "Optional: brief patient description so the summary can highlight relevant eligibility points (e.g. '62-year-old male with type 2 diabetes')",
            },
        },
        "required": ["nct_id"],
    },
    strict=True,
)

draft_referral_message_tool = FunctionTool(
    name="draft_referral_message",
    description=(
        "Draft a referral message to a study coordinator on behalf of the "
        "clinician. Includes the study ID, patient's relevant clinical context, "
        "and a request for enrollment information. The clinician reviews and "
        "sends the message themselves."
    ),
    parameters={
        "type": "object",
        "properties": {
            "nct_id": {
                "type": "string",
                "description": "The NCT identifier for the target study",
            },
            "patient_summary": {
                "type": "string",
                "description": "Brief clinical summary of the patient (condition, age, relevant history). No PHI — use de-identified descriptions.",
            },
            "clinician_name": {
                "type": "string",
                "description": "Name and title of the referring clinician",
            },
            "clinician_facility": {
                "type": "string",
                "description": "The clinician's VA facility (e.g. 'Portland VAMC')",
            },
        },
        "required": ["nct_id", "patient_summary"],
    },
    strict=True,
)

all_tools = [
    search_studies_tool,
    get_study_details_tool,
    check_eligibility_tool,
    count_studies_tool,
    list_enum_values_tool,
    generate_trial_summary_tool,
    draft_referral_message_tool,
]
