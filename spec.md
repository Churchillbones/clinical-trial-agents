# Clinical Trials Search Agent — Spec

## Overview

A chat-based AI agent deployed to **Azure AI Foundry** that helps VA doctors and clinical staff find active clinical trials they can refer their patients to. A clinician describes a patient's condition, age, or treatment situation in plain language and the agent searches [ClinicalTrials.gov](https://clinicaltrials.gov) for recruiting studies that may be a fit — returning eligibility criteria, site locations, and contact information so the provider can discuss enrollment with the patient.

## Problem Statement

Clinicians often know a patient could benefit from a clinical trial but have no quick way to find one. Searching ClinicalTrials.gov directly requires knowledge of the query syntax, and results are dense and hard to scan during a clinic visit. This agent removes that friction: the doctor asks a natural question and gets a curated, actionable shortlist.

## Goals

| # | Goal |
|---|------|
| 1 | Deploy as an **Azure AI Foundry Agent** (Agent Service) — accessible from the Foundry portal, Agent SDK, and embeddable in VA clinical apps |
| 2 | Wrap the public **ClinicalTrials.gov API v2** (`https://clinicaltrials.gov/api/v2/`) — no API key required |
| 3 | Let clinicians search by **patient profile** — condition, age, sex, treatment history — and return trials the patient may qualify for |
| 4 | Highlight **eligibility criteria, recruiting locations near VA sites, and study contacts** so the clinician can take immediate action |
| 5 | Default to **recruiting / enrolling** studies — clinicians need trials their patients can join *now* |

## Target Users

- **Primary care providers** — looking for trials when standard treatments have plateaued
- **Specialists** (psychiatry, oncology, endocrinology, etc.) — matching patients to condition-specific studies
- **Nurse care coordinators / social workers** — helping patients explore research participation
- **Research-interested clinicians** — checking what's available at their own facility or nearby VA sites

---

## Architecture

This is a **Hosted Agent** deployed to the Foundry Agent Service. It runs as a containerized Python service that Foundry manages — no separate Azure Functions or App Service needed.

```
┌──────────────────────────────────────────────────────┐
│          Foundry Agent Service (Hosted)               │
│  ┌────────────────────────────────────────────────┐   │
│  │  main.py  (Agent Framework / Responses API)    │   │
│  │                                                │   │
│  │  System prompt  +  FunctionTool definitions     │   │
│  │    ├─ search_studies                           │   │
│  │    ├─ get_study_details                        │   │
│  │    ├─ check_eligibility                        │   │
│  │    ├─ count_studies                            │   │
│  │    ├─ list_enum_values                         │   │
│  │    ├─ generate_trial_summary                   │   │
│  │    └─ draft_referral_message                   │   │
│  │                                                │   │
│  │  Tool call loop:                               │   │
│  │    model returns function_call →               │   │
│  │    execute tool → return FunctionCallOutput →  │   │
│  │    model returns final answer                  │   │
│  └──────────────┬─────────────────────────────────┘   │
│                 │                                      │
│  ┌──────────────▼─────────────────────────────────┐   │
│  │  clinical_trials_tools.py                      │   │
│  │    → httpx calls to CTG API v2                 │   │
│  └────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
         ▲                              │
         │  POST /responses             │  HTTPS (no key)
         │  (local: :8088)              │
         ▼                              ▼
   End-user                      clinicaltrials.gov
   (Foundry UI / Teams /              /api/v2/*
    any app)
```

### Models

| Model | Role | Rationale |
|-------|------|-----------|
| **gpt-5.4-nano** | Default for search / count / enum queries | Fast, cheap, sufficient for structured API calls |
| **gpt-5.5** | Eligibility analysis, complex multi-step searches | Better reasoning for parsing dense eligibility text and patient matching |

Both models are accessed through the **Responses API** (not Chat Completions). The Responses API provides built-in tool orchestration — the model emits `function_call` items, our code executes them, and we return `FunctionCallOutput` items to continue the conversation.

### Hosted Agent Approach

This uses the **Code Agent** path in Foundry:
- Build the agent using **Agent Framework** + **Responses API** protocol
- Test locally on `http://localhost:8088/responses` (F5 in VS Code)
- Deploy to Foundry Agent Service via the **Foundry Toolkit** VS Code extension
- Once deployed, the agent is accessible from the Foundry Playground, can be published to **Teams and Microsoft 365**, and exposes an endpoint callable from any app

### Foundry SDK (`azure-ai-projects` 2.2.0)

The [azure-ai-projects](https://pypi.org/project/azure-ai-projects/) SDK (v2.2.0) provides:

```python
import json
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, Tool, FunctionTool
from azure.identity import DefaultAzureCredential
from openai.types.responses.response_input_param import FunctionCallOutput

project = AIProjectClient(
    endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)
openai = project.get_openai_client()

# Define function tools
search_tool = FunctionTool(
    name="search_studies",
    description="Search ClinicalTrials.gov for recruiting studies...",
    parameters={...},  # JSON schema
    strict=True,
)

# Register the agent in Foundry
agent = project.agents.create_version(
    agent_name="clinical-trials-search",
    definition=PromptAgentDefinition(
        model="gpt-5.4-nano",
        instructions=system_prompt,
        tools=[search_tool, ...],
    ),
)

# Conversations for multi-turn
conversation = openai.conversations.create()

# Call with tool handling loop
response = openai.responses.create(
    input="Find PTSD trials near Portland VA",
    conversation=conversation.id,
    extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
)

# Process function_call items → execute tool → return FunctionCallOutput
for item in response.output:
    if item.type == "function_call":
        result = execute_tool(item.name, json.loads(item.arguments))
        # return result via next responses.create() call
```

Key patterns:
- **`FunctionTool`** — defines each tool with name, parameters JSON schema, and `strict=True`
- **`PromptAgentDefinition`** — bundles model + instructions + tools into an agent version
- **`.agents.create_version()`** — registers the agent so it appears in the Foundry portal
- **`conversations`** — server-side multi-turn (no client-side message history)
- **`agent_reference`** — routes `responses.create()` through the registered agent
- **`FunctionCallOutput`** — returns tool results back to the model

---

## ClinicalTrials.gov API v2 — Endpoints Used

Base URL: `https://clinicaltrials.gov/api/v2`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/studies` | GET | Search / list studies with query filters |
| `/studies/{nctId}` | GET | Retrieve full record for one study |
| `/studies/metadata` | GET | Field metadata (names, types) |
| `/studies/search-areas` | GET | Available search areas |
| `/studies/enums` | GET | Enum values for coded fields |
| `/stats/size` | GET | Total study count (with optional filters) |
| `/stats/field/values` | GET | Distinct values for a field |
| `/stats/field/sizes` | GET | Field size distribution |
| `/version` | GET | API data timestamp |

### Key Query Parameters (`GET /studies`)

These map to the search areas the agent uses to translate a clinician's patient description into API filters.

| Parameter | Maps to Search Area | Example |
| `query.term` | BasicSearch (57 fields) | `"PTSD treatment"` |
| `query.cond` | ConditionSearch | `"diabetes"` |
| `query.intr` | InterventionSearch | `"psilocybin"` |
| `query.outc` | OutcomeSearch | `"pain reduction"` |
| `query.titles` | TitleSearch | `"veterans"` |
| `query.locn` | LocationSearch | `"San Francisco"` |
| `query.spons` | SponsorSearch | `"Department of Veterans Affairs"` |
| `query.id` | IdSearch | `"NCT12345678"` |
| `query.patient` | PatientSearch | `"knee replacement"` |
| `filter.overallStatus` | — | `"RECRUITING"` |
| `filter.geo` | — | `distance(lat,lon,dist)` |
| `filter.ids` | — | Comma-separated NCT IDs |
| `filter.advanced` | — | Complex filter expression |
| `pageSize` | — | 1–1000 (default 10) |
| `pageToken` | — | Pagination cursor |
| `sort` | — | e.g. `"LastUpdatePostDate:desc"` |
| `fields` | — | Comma-separated field names to return |
| `format` | — | `json` (default) or `csv` |

---

## Agent Tools (Function Definitions)

### 1. `search_studies`

Search ClinicalTrials.gov for studies matching user criteria.

```json
{
  "name": "search_studies",
  "description": "Search ClinicalTrials.gov for clinical studies. Returns a list of matching studies with key details. Use this whenever the user asks about clinical trials, studies, or research for a condition, intervention, or location.",
  "parameters": {
    "type": "object",
    "properties": {
      "term": {
        "type": "string",
        "description": "General search term (searches title, condition, intervention, summary, etc.)"
      },
      "condition": {
        "type": "string",
        "description": "Patient's condition or diagnosis (e.g. 'PTSD', 'type 2 diabetes', 'non-small cell lung cancer')"
      },
      "intervention": {
        "type": "string",
        "description": "Treatment or intervention of interest (e.g. 'psilocybin', 'CBT', 'immunotherapy')"
      },
      "location": {
        "type": "string",
        "description": "City, state, or VA facility name near the patient (e.g. 'San Francisco VA', 'Texas')"
      },
      "sponsor": {
        "type": "string",
        "description": "Sponsor or collaborator name (e.g. 'Department of Veterans Affairs')"
      },
      "age": {
        "type": "string",
        "description": "Patient age or age group to filter eligible studies (e.g. '62', 'OLDER_ADULT')"
      },
      "sex": {
        "type": "string",
        "enum": ["MALE", "FEMALE", "ALL"],
        "description": "Patient sex for eligibility filtering"
      },
      "status": {
        "type": "string",
        "enum": ["RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED", "ENROLLING_BY_INVITATION", "SUSPENDED", "TERMINATED", "WITHDRAWN"],
        "description": "Filter by recruitment status. Defaults to RECRUITING if not specified."
      },
      "phase": {
        "type": "string",
        "enum": ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"],
        "description": "Filter by study phase"
      },
      "page_size": {
        "type": "integer",
        "description": "Number of results to return (1-20, default 10)"
      }
    },
    "required": []
  }
}
```

### 2. `get_study_details`

Get the full record for a single study — emphasizing eligibility criteria, locations, and contacts.

```json
{
  "name": "get_study_details",
  "description": "Retrieve the complete details of a clinical trial by NCT ID. Returns eligibility criteria, study locations with contact info, enrollment status, and study design. Use when a clinician wants to evaluate whether a specific trial is right for their patient.",
  "parameters": {
    "type": "object",
    "properties": {
      "nct_id": {
        "type": "string",
        "description": "The NCT identifier (e.g. 'NCT12345678')"
      }
    },
    "required": ["nct_id"]
  }
}
```

### 3. `check_eligibility`

Retrieve just the eligibility section for a study — optimized for the "would my patient qualify?" question.

```json
{
  "name": "check_eligibility",
  "description": "Retrieve the eligibility criteria, age range, sex, and healthy volunteer status for a study. Use when a clinician wants to quickly check if their patient might qualify without pulling the full study record.",
  "parameters": {
    "type": "object",
    "properties": {
      "nct_id": {
        "type": "string",
        "description": "The NCT identifier (e.g. 'NCT12345678')"
      }
    },
    "required": ["nct_id"]
  }
}
```

### 4. `count_studies`

Get the count of studies matching criteria (useful for "how many?" questions).

```json
{
  "name": "count_studies",
  "description": "Count how many clinical studies match the given criteria. Use when the user asks 'how many' studies exist for a condition or filter.",
  "parameters": {
    "type": "object",
    "properties": {
      "term": { "type": "string", "description": "General search term" },
      "condition": { "type": "string", "description": "Condition or disease" },
      "intervention": { "type": "string", "description": "Intervention or treatment" },
      "sponsor": { "type": "string", "description": "Sponsor name" },
      "status": { "type": "string", "description": "Recruitment status filter" }
    },
    "required": []
  }
}
```

### 5. `list_enum_values`

List valid values for coded fields (phases, statuses, intervention types, etc.).

```json
{
  "name": "list_enum_values",
  "description": "List the allowed values for a ClinicalTrials.gov coded field. Use when the user asks what phases, statuses, or types are available.",
  "parameters": {
    "type": "object",
    "properties": {
      "enum_name": {
        "type": "string",
        "description": "Name of the enum (e.g. 'Phase', 'OverallStatus', 'InterventionType', 'StudyType')"
      }
    },
    "required": ["enum_name"]
  }
}
```

### 6. `generate_trial_summary`

Generate a patient-friendly handout summarizing a study the clinician can print and give to the patient.

```json
{
  "name": "generate_trial_summary",
  "description": "Generate a printable, patient-friendly summary of a clinical trial. Includes study purpose in plain language, what participation involves, eligibility highlights, site location, and contact info. Use when the clinician wants a handout to share with the patient.",
  "parameters": {
    "type": "object",
    "properties": {
      "nct_id": {
        "type": "string",
        "description": "The NCT identifier (e.g. 'NCT12345678')"
      },
      "patient_context": {
        "type": "string",
        "description": "Optional: brief patient description so the summary can highlight relevant eligibility points (e.g. '62-year-old male with type 2 diabetes')"
      }
    },
    "required": ["nct_id"]
  }
}
```

### 7. `draft_referral_message`

Draft a message the clinician can send to the study coordinator to refer a patient.

```json
{
  "name": "draft_referral_message",
  "description": "Draft a referral message to a study coordinator on behalf of the clinician. Includes the study ID, patient's relevant clinical context, and a request for enrollment information. The clinician reviews and sends the message themselves.",
  "parameters": {
    "type": "object",
    "properties": {
      "nct_id": {
        "type": "string",
        "description": "The NCT identifier for the target study"
      },
      "patient_summary": {
        "type": "string",
        "description": "Brief clinical summary of the patient (condition, age, relevant history). No PHI — use de-identified descriptions."
      },
      "clinician_name": {
        "type": "string",
        "description": "Name and title of the referring clinician"
      },
      "clinician_facility": {
        "type": "string",
        "description": "The clinician's VA facility (e.g. 'Portland VAMC')"
      }
    },
    "required": ["nct_id", "patient_summary"]
  }
}
```

---

## System Prompt (Agent Instructions)

```text
You are a Clinical Trials Referral Assistant for VA clinicians. You help
doctors, nurses, and care coordinators find recruiting clinical trials that
their patients may be eligible for.

WORKFLOW:
1. The clinician describes a patient situation — condition, age, sex, current
   treatments, location, or what they're looking for.
2. You translate that into a ClinicalTrials.gov search using the search_studies
   tool. Always default to status=RECRUITING so results are actionable.
3. Present results as a scannable table optimized for a busy clinic visit:
   NCT ID (linked), Title, Phase, Key Eligibility, Nearest Site, Contact.
4. Offer to pull full eligibility criteria or site details for any study the
   clinician is interested in (use get_study_details).
5. When the clinician is interested in a study for their patient, offer to:
   a. Generate a printable patient summary (use generate_trial_summary)
   b. Draft a referral message to the study coordinator (use draft_referral_message)

RESULT FORMATTING:
- Always link NCT IDs: https://clinicaltrials.gov/study/{nctId}
- Highlight inclusion/exclusion criteria that are most relevant to the
  patient description the clinician gave.
- When showing locations, prioritize VA facilities. Include site contact
  name and phone when available.
- Include target enrollment and current enrollment count when available.

SMART DEFAULTS:
- Default to RECRUITING status unless the clinician asks otherwise.
- If the clinician mentions a VA facility, search that location automatically.
- If no results are found, suggest broadening: remove location filter, try
  related conditions, or expand to NOT_YET_RECRUITING.
- For common VA conditions (PTSD, TBI, MST, chronic pain, substance use,
  hepatitis C, diabetes), you can suggest related search terms.

WHAT YOU DO NOT DO:
- You do not provide medical advice or recommend specific trials.
- You do not determine if a patient is eligible — you surface the criteria
  so the clinician can make that judgment.
- You cannot enroll patients or contact study sites on behalf of the user.
- ClinicalTrials.gov data refreshes daily (Mon–Fri by 9 AM ET).

PATIENT SUMMARIES:
- When generating a trial summary, write in plain language at a 6th-grade
  reading level. Avoid medical jargon where possible.
- Include: study purpose, what participation involves, key eligibility,
  site address and contact info, and the ClinicalTrials.gov link.
- Format for printing: clear sections, no markdown links.

REFERRAL MESSAGES:
- Draft professional, concise messages addressed to the study coordinator.
- Include the NCT ID, referring clinician's info, and a de-identified
  summary of the patient’s relevant clinical context.
- NEVER include PHI (name, DOB, SSN, etc.) — remind the clinician to
  share identifiable details through secure channels only.
- The clinician must review and send the message themselves.
```

---

## File Structure

Scaffolded by the **Foundry Toolkit** VS Code extension (`Foundry Toolkit: Create new Hosted Agent` → Python → Agent Framework → Responses API).

```
clinical-trials-agent/
├── spec.md                        # This file
├── main.py                        # Agent entry point (HTTP server on :8088)
├── clinical_trials_tools.py       # Tool implementations (CTG API v2 wrapper)
├── tool_definitions.py            # FunctionTool objects for each tool
├── system_prompt.txt              # System prompt (loaded by main.py)
├── requirements.txt               # Python dependencies
├── .env                           # Local env vars (gitignored)
├── .env.example                   # Template for required env vars
├── test_clinical_trials_tools.py  # Unit tests for tool functions
└── README.md                      # Setup and deployment instructions
```

---

## Implementation Plan

### Phase 1 — Tools + Local Testing
1. Install **Foundry Toolkit** VS Code extension
2. Scaffold hosted agent project: `Foundry Toolkit: Create new Hosted Agent` → Python → Agent Framework → Responses API → Basic
3. Implement `clinical_trials_tools.py` — the seven tool functions calling CTG API v2
4. Create `tool_definitions.py` — `FunctionTool` objects with `strict=True`
5. Wire tools into `main.py` — function-call loop (model emits `function_call` → execute tool → return `FunctionCallOutput`)
6. Write `test_clinical_trials_tools.py` — verify against live CTG API
7. Test locally: **F5** → agent runs on `http://localhost:8088/responses` → test with curl or Agent Inspector

### Phase 2 — Deploy to Foundry
8. `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote
9. Register agent version with `agents.create_version()` using `PromptAgentDefinition` + `FunctionTool` list
10. Test in Foundry portal Playground tab
11. Expose hosted agent endpoint through existing **APIM gateway**
12. Publish to **Teams / Microsoft 365** for clinician access

### Phase 2.5 — Clinician Action Features
13. Implement `generate_trial_summary` — plain-language patient handout from study record
14. Implement `draft_referral_message` — de-identified referral draft to study coordinator
15. Add print-friendly HTML/PDF formatting for trial summaries
16. Test end-to-end: search → select study → generate summary → draft referral

### Phase 3 — VA Enhancements
17. Add VA-specific pre-built queries (PTSD, TBI, MST, chronic pain)
18. Add geo-filtering for VA medical centers
19. Add result export (CSV download via CTG API)
20. Evaluate Foundry Memory Stores for conversation history across sessions
21. Evaluate MCP Server packaging for tool reuse by other agents

### Phase 4 — Future Integrations
22. Bing grounding tool for PubMed / literature search alongside CTG
23. VistA RPC or Cerner FHIR integration — auto-populate patient demographics from EHR
24. Response caching layer if CTG API rate limits become an issue

---

## Dependencies

```
azure-ai-projects>=2.2.0   # Foundry SDK — AIProjectClient, agent ops, get_openai_client()
azure-identity>=1.15.0     # DefaultAzureCredential (Entra ID auth)
httpx>=0.27.0              # Async HTTP client for CTG API calls
python-dotenv>=1.0.0       # Load .env for local development
```

Note: `azure-ai-projects` pulls in `openai` as a dependency — no separate install needed.

### Foundry Endpoint Configuration

The agent connects to the 806 Foundry project. Required env vars:

```bash
# Foundry project endpoint (from Foundry portal → Project → Overview)
FOUNDRY_PROJECT_ENDPOINT=https://spd2494-dev-eus2-806-fdy.services.ai.azure.com/api/projects/806

# Model deployment names (as shown in Foundry "Deployed models" table)
FOUNDRY_MODEL_NAME=gpt-5.4-nano              # default model
FOUNDRY_MODEL_NAME_REASONING=gpt-5.5         # for eligibility analysis

# Agent name (after deploy_agent.py registers it)
FOUNDRY_AGENT_NAME=clinical-trials-search
```

Authentication uses **Entra ID** via `DefaultAzureCredential` — requires `az login` and an appropriate role assignment (Azure AI User) on the Foundry resource. No raw API keys in code.

---

## Example Conversations

### Finding a trial for a specific patient

**Clinician:** I have a 58-year-old male veteran with treatment-resistant PTSD. He's tried prolonged exposure and sertraline. Anything recruiting near the Portland VA?

**Agent:** *(calls search_studies with condition="treatment-resistant PTSD", location="Portland VA", status="RECRUITING", age="58", sex="MALE")*

> I found 3 recruiting studies that may be relevant for your patient:
>
> | NCT ID | Title | Phase | Key Eligibility | Site | Contact |
> |--------|-------|-------|----------------|------|---------|
> | [NCT06123456](https://clinicaltrials.gov/study/NCT06123456) | Stellate Ganglion Block for Treatment-Resistant PTSD | Phase 2 | Ages 18-65, failed ≥1 first-line tx, PCL-5 ≥ 38 | Portland VAMC | Dr. Smith, 503-555-0100 |
> | [NCT06234567](https://clinicaltrials.gov/study/NCT06234567) | Psilocybin-Assisted Therapy for Veterans with PTSD | Phase 2 | Ages 21-70, CAPS-5 ≥ 25, no psychotic disorders | Portland VAMC | Research Coord., 503-555-0200 |
> | [NCT06345678](https://clinicaltrials.gov/study/NCT06345678) | Transcranial Magnetic Stimulation for PTSD | N/A | Ages 18-75, failed ≥2 treatments | OHSU (affiliated) | 503-555-0300 |
>
> Your patient's age (58) and treatment history (failed PE + sertraline) look consistent with the first two studies. Would you like me to pull the full eligibility criteria for any of these?

---

### Quick scan before a clinic visit

**Clinician:** What diabetes trials are recruiting at VA facilities in Texas?

**Agent:** *(calls search_studies with condition="diabetes", location="Texas", sponsor="Department of Veterans Affairs", status="RECRUITING")*

> I found 5 recruiting diabetes studies at Texas VA sites. Here are the top results:
>
> | NCT ID | Title | Phase | Population | Site |
> |--------|-------|-------|-----------|------|
> | [NCT06456789](https://clinicaltrials.gov/study/NCT06456789) | GLP-1 Agonist Combination for T2DM in Veterans | Phase 3 | HbA1c 7.5-12%, ages 30-75 | Dallas VAMC |
> | [NCT06567890](https://clinicaltrials.gov/study/NCT06567890) | Continuous Glucose Monitoring Outcomes | N/A | T1DM or T2DM on insulin | San Antonio VAMC |
>
> Want me to check if a specific patient would meet the criteria for any of these?

---

### Checking eligibility details

**Clinician:** Pull the full eligibility for NCT06123456. My patient has a history of mild TBI — would that exclude him?

**Agent:** *(calls get_study_details with nct_id="NCT06123456")*

> **NCT06123456 — Stellate Ganglion Block for Treatment-Resistant PTSD**
>
> **Inclusion Criteria:**
> - Age 18–65
> - PTSD diagnosis (DSM-5), PCL-5 score ≥ 38
> - Failed ≥ 1 first-line treatment (psychotherapy or SSRI/SNRI)
> - Stable medications for ≥ 4 weeks
>
> **Exclusion Criteria:**
> - Severe traumatic brain injury (moderate/mild TBI is NOT excluded)
> - Active psychosis or suicidal ideation with plan
> - Bleeding disorder or anticoagulant use
> - Prior stellate ganglion block
>
> **Mild TBI does not appear to be an exclusion.** Your patient may qualify based on what you've described. The site contact is Dr. Smith at 503-555-0100.
>
> Would you like me to find more studies, or do you need anything else on this one?

---

## Decisions

| Question | Decision | Notes |
|----------|----------|-------|
| Foundry project | **806 project** | Deploy into existing `spd2494-dev-eus2-806-fdy` project |
| APIM gateway | **Yes** | Expose via existing APIM so other VA apps can call it |
| Bing / PubMed grounding | **Not yet** | Future feature — add `BingGroundingTool` later for literature search |
| CTG API caching | **Not yet** | Revisit if we hit courtesy rate limits |
| EHR integration | **Manual for now** | Clinician pastes/types patient info; future: VistA or Cerner API pre-fill |
| Printable trial summary | **Yes — Phase 2** | Agent generates a patient-friendly handout for the clinician to print |
| Referral message draft | **Yes — Phase 2** | Agent drafts a message to the study coordinator the clinician can send |

## Future Roadmap

- **Bing grounding** — add `BingGroundingTool` to search PubMed/literature alongside CTG
- **EHR pre-fill** — VistA RPC or Cerner FHIR integration to auto-populate patient demographics
- **Response caching** — cache CTG API results if rate limits become an issue
- **MCP Server** — package tools as an MCP server for reuse by other Foundry agents
