# Clinical Trials Search Agent — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> Follow TDD (superpowers:test-driven-development) — no production code without a failing test first.
> Use the `test-reviewer` agent after each task batch to audit test quality.
> Use the `wwvd` agent for pre-commit readiness checks before each commit.

**Goal:** Build a Hosted Agent on Azure AI Foundry that lets VA clinicians search ClinicalTrials.gov for recruiting studies by patient profile, check eligibility, generate patient handouts, and draft referral messages.

**Architecture:** Foundry Hosted Agent (Code Agent path) using Agent Framework + Responses API. Python HTTP server on `:8088` locally, deployed via Foundry Toolkit. Tool functions call ClinicalTrials.gov API v2 (public, no key). Models: gpt-5.4-nano (default), gpt-5.5 (eligibility reasoning).

**Tech Stack:** Python 3.13+, azure-ai-projects 2.2.0, httpx, pytest, Foundry Toolkit VS Code extension

**Spec:** `spec.md` in this folder — the source of truth for all tool schemas, system prompt, and API mappings.

---

## Phase 1 — Core Tools (TDD)

The five CTG API wrapper functions. Each task follows Red-Green-Refactor.

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `clinical_trials_tools.py` (empty module)
- Create: `test_clinical_trials_tools.py` (empty test module)

**Step 1: Create `requirements.txt`**

```
azure-ai-projects>=2.2.0
azure-identity>=1.15.0
httpx>=0.27.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
respx>=0.21.0
```

**Step 2: Create `.env.example`**

```bash
FOUNDRY_PROJECT_ENDPOINT=https://spd2494-dev-eus2-806-fdy.services.ai.azure.com/api/projects/806
FOUNDRY_MODEL_NAME=gpt-5.4-nano
FOUNDRY_MODEL_NAME_REASONING=gpt-5.5
FOUNDRY_AGENT_NAME=clinical-trials-search
```

**Step 3: Create `.gitignore`**

```
.env
__pycache__/
.venv/
*.pyc
.pytest_cache/
```

**Step 4: Create empty modules**

```python
# clinical_trials_tools.py
"""ClinicalTrials.gov API v2 tool implementations."""

CTG_BASE_URL = "https://clinicaltrials.gov/api/v2"
```

```python
# test_clinical_trials_tools.py
"""Tests for ClinicalTrials.gov API tool functions."""
```

**Step 5: Install dependencies**

Run: `pip install -r requirements.txt`

**Step 6: Verify pytest runs**

Run: `pytest test_clinical_trials_tools.py -v`
Expected: "no tests ran" (0 collected)

**Step 7: Commit**

```bash
git add -A
git commit -m "chore: scaffold clinical-trials-agent project"
```

---

### Task 2: `search_studies` — RED then GREEN

**Files:**
- Test: `test_clinical_trials_tools.py`
- Create/Modify: `clinical_trials_tools.py`

**Step 1: Write failing test — basic search returns studies**

```python
import pytest
import httpx
import respx
import json
from clinical_trials_tools import search_studies

MOCK_SEARCH_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Test Study"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "designModule": {"phases": ["PHASE2"]},
                "conditionsModule": {"conditions": ["PTSD"]},
                "contactsLocationsModule": {
                    "locations": [
                        {
                            "facility": "Portland VAMC",
                            "city": "Portland",
                            "state": "Oregon",
                            "country": "United States",
                        }
                    ]
                },
            }
        }
    ],
    "totalCount": 1,
}


@respx.mock
def test_search_studies_by_condition():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    result = search_studies(condition="PTSD", status="RECRUITING")
    assert result["totalCount"] == 1
    assert result["studies"][0]["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"


@respx.mock
def test_search_studies_defaults_to_recruiting():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    search_studies(condition="diabetes")
    assert "RECRUITING" in str(route.calls[0].request.url)


@respx.mock
def test_search_studies_with_location():
    route = respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    search_studies(condition="PTSD", location="Portland VA")
    assert "Portland" in str(route.calls[0].request.url)
```

**Step 2: Run tests to verify they fail**

Run: `pytest test_clinical_trials_tools.py -v -k "search_studies"`
Expected: FAIL — `ImportError: cannot import name 'search_studies'`

**Step 3: Write minimal implementation**

```python
import httpx

CTG_BASE_URL = "https://clinicaltrials.gov/api/v2"

def search_studies(
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
    """Search ClinicalTrials.gov for studies matching criteria."""
    params: dict[str, str | int] = {"pageSize": min(page_size, 20)}
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

    resp = httpx.get(f"{CTG_BASE_URL}/studies", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json()
```

**Step 4: Run tests to verify they pass**

Run: `pytest test_clinical_trials_tools.py -v -k "search_studies"`
Expected: 3 passed

**Step 5: Commit**

```bash
git add clinical_trials_tools.py test_clinical_trials_tools.py
git commit -m "feat: add search_studies tool with TDD tests"
```

---

### Task 3: `get_study_details` — RED then GREEN

**Files:**
- Test: `test_clinical_trials_tools.py`
- Modify: `clinical_trials_tools.py`

**Step 1: Write failing test**

```python
from clinical_trials_tools import get_study_details

MOCK_STUDY_DETAIL = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Test Study"},
        "eligibilityModule": {
            "eligibilityCriteria": "Inclusion: Age 18-65\nExclusion: Pregnant",
            "sex": "ALL",
            "minimumAge": "18 Years",
            "maximumAge": "65 Years",
        },
    }
}


@respx.mock
def test_get_study_details_returns_full_record():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = get_study_details("NCT00000001")
    assert result["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"
    assert "eligibilityModule" in result["protocolSection"]


@respx.mock
def test_get_study_details_invalid_nct_raises():
    respx.get("https://clinicaltrials.gov/api/v2/studies/INVALID").mock(
        return_value=httpx.Response(404, json={"error": "Not found"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        get_study_details("INVALID")
```

**Step 2: Run — expect FAIL** (`cannot import name 'get_study_details'`)

**Step 3: Implement**

```python
def get_study_details(nct_id: str) -> dict:
    """Retrieve the complete record for a single study by NCT ID."""
    resp = httpx.get(f"{CTG_BASE_URL}/studies/{nct_id}", timeout=15.0)
    resp.raise_for_status()
    return resp.json()
```

**Step 4: Run — expect PASS** (2 passed)

**Step 5: Commit**

```bash
git commit -am "feat: add get_study_details tool"
```

---

### Task 4: `check_eligibility` — RED then GREEN

**Step 1: Write failing test**

```python
from clinical_trials_tools import check_eligibility


@respx.mock
def test_check_eligibility_returns_eligibility_fields():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = check_eligibility("NCT00000001")
    assert "eligibilityCriteria" in result
    assert result["minimumAge"] == "18 Years"
    assert result["maximumAge"] == "65 Years"
    assert result["sex"] == "ALL"
```

**Step 2: Run — expect FAIL**

**Step 3: Implement**

```python
def check_eligibility(nct_id: str) -> dict:
    """Retrieve just the eligibility section for a study."""
    resp = httpx.get(
        f"{CTG_BASE_URL}/studies/{nct_id}",
        params={"fields": "EligibilityCriteria,EligibilityModule"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    module = data.get("protocolSection", {}).get("eligibilityModule", {})
    return {
        "nctId": nct_id,
        "eligibilityCriteria": module.get("eligibilityCriteria", ""),
        "sex": module.get("sex", "ALL"),
        "minimumAge": module.get("minimumAge", "N/A"),
        "maximumAge": module.get("maximumAge", "N/A"),
        "healthyVolunteers": module.get("healthyVolunteers", "No"),
        "stdAges": module.get("stdAges", []),
    }
```

**Step 4: Run — expect PASS**

**Step 5: Commit**

```bash
git commit -am "feat: add check_eligibility tool"
```

---

### Task 5: `count_studies` — RED then GREEN

**Step 1: Write failing test**

```python
from clinical_trials_tools import count_studies


@respx.mock
def test_count_studies_returns_total():
    respx.get("https://clinicaltrials.gov/api/v2/stats/size").mock(
        return_value=httpx.Response(200, json={"totalCount": 42})
    )
    result = count_studies(condition="PTSD", status="RECRUITING")
    assert result == 42


@respx.mock
def test_count_studies_no_filters():
    respx.get("https://clinicaltrials.gov/api/v2/stats/size").mock(
        return_value=httpx.Response(200, json={"totalCount": 500000})
    )
    result = count_studies()
    assert result == 500000
```

**Step 2: Run — expect FAIL**

**Step 3: Implement**

```python
def count_studies(
    term: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    status: str | None = None,
) -> int:
    """Count studies matching the given criteria."""
    params: dict[str, str] = {}
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

    resp = httpx.get(f"{CTG_BASE_URL}/stats/size", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json().get("totalCount", 0)
```

**Step 4: Run — expect PASS**

**Step 5: Commit**

```bash
git commit -am "feat: add count_studies tool"
```

---

### Task 6: `list_enum_values` — RED then GREEN

**Step 1: Write failing test**

```python
from clinical_trials_tools import list_enum_values


@respx.mock
def test_list_enum_values_returns_values():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(
            200,
            json={
                "Phase": ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"],
                "OverallStatus": ["RECRUITING", "COMPLETED"],
            },
        )
    )
    result = list_enum_values("Phase")
    assert "PHASE2" in result


@respx.mock
def test_list_enum_values_unknown_enum():
    respx.get("https://clinicaltrials.gov/api/v2/studies/enums").mock(
        return_value=httpx.Response(200, json={"Phase": ["PHASE1"]})
    )
    result = list_enum_values("Nonexistent")
    assert result == []
```

**Step 2: Run — expect FAIL**

**Step 3: Implement**

```python
def list_enum_values(enum_name: str) -> list[str]:
    """List valid values for a ClinicalTrials.gov coded field."""
    resp = httpx.get(f"{CTG_BASE_URL}/studies/enums", timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    return data.get(enum_name, [])
```

**Step 4: Run — expect PASS**

**Step 5: Commit**

```bash
git commit -am "feat: add list_enum_values tool"
```

---

### Task 7: Error handling + edge cases

**Step 1: Write failing tests for error paths**

```python
@respx.mock
def test_search_studies_api_timeout():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        side_effect=httpx.ConnectTimeout("Timeout")
    )
    with pytest.raises(httpx.ConnectTimeout):
        search_studies(condition="PTSD")


@respx.mock
def test_search_studies_empty_results():
    respx.get("https://clinicaltrials.gov/api/v2/studies").mock(
        return_value=httpx.Response(200, json={"studies": [], "totalCount": 0})
    )
    result = search_studies(condition="xyznonexistent")
    assert result["totalCount"] == 0
    assert result["studies"] == []
```

**Step 2: Run — should already pass (httpx raises by default). If any fail, fix.**

**Step 3: Commit**

```bash
git commit -am "test: add error handling and edge case tests"
```

**Step 4: Run test-reviewer agent**

Dispatch `test-reviewer` agent against `test_clinical_trials_tools.py` to audit test quality before proceeding.

---

## Phase 1B — Tool Definitions + System Prompt

### Task 8: `tool_definitions.py`

**Files:**
- Create: `tool_definitions.py`

**Step 1: Create `FunctionTool` objects for all 7 tools**

Use the JSON schemas from `spec.md` → Agent Tools section. Each tool uses `strict=True`.

```python
from azure.ai.projects.models import FunctionTool

search_studies_tool = FunctionTool(
    name="search_studies",
    description="Search ClinicalTrials.gov for clinical studies...",
    parameters={...},  # copy from spec.md
    strict=True,
)
# ... repeat for all 7 tools
```

**Step 2: Verify import works**

Run: `python -c "from tool_definitions import search_studies_tool; print(search_studies_tool.name)"`
Expected: `search_studies`

**Step 3: Commit**

```bash
git commit -am "feat: add FunctionTool definitions for all 7 tools"
```

---

### Task 9: `system_prompt.txt`

**Files:**
- Create: `system_prompt.txt`

**Step 1: Copy system prompt from spec.md** (the full text between the triple-backtick block under "System Prompt")

**Step 2: Commit**

```bash
git commit -am "feat: add system prompt"
```

---

## Phase 1C — Agent Wiring (`main.py`)

### Task 10: `main.py` — agent entry point with function-call loop

**Files:**
- Create: `main.py`

**Step 1: Write the agent server**

This is the Agent Framework entry point. It:
1. Loads system prompt and tool definitions
2. Creates `AIProjectClient` + OpenAI client
3. Handles `POST /responses` — calls `responses.create()` with tools
4. Loops on `function_call` output items — dispatches to tool functions, returns `FunctionCallOutput`
5. Returns final text response

Key pattern from Foundry docs:
```python
for item in response.output:
    if item.type == "function_call":
        result = dispatch_tool(item.name, json.loads(item.arguments))
        input_list.append(FunctionCallOutput(
            type="function_call_output",
            call_id=item.call_id,
            output=json.dumps(result),
        ))
```

**Step 2: Test locally**

Run: `python main.py`
Then: `curl -sS -H "Content-Type: application/json" -X POST http://localhost:8088/responses -d '{"input": "How many PTSD studies are recruiting?", "stream": false}'`

**Step 3: Commit**

```bash
git commit -am "feat: add main.py agent entry point with function-call loop"
```

---

## Phase 2 — Deploy to Foundry

### Task 11: Deploy via Foundry Toolkit

**Step 1:** Open Command Palette → `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote

**Step 2:** Verify agent appears in Foundry portal under Hosted Agents (Preview)

**Step 3:** Test in Playground tab with: "Find recruiting PTSD trials near Portland VA"

**Step 4:** Register agent version via SDK:

```python
agent = project.agents.create_version(
    agent_name="clinical-trials-search",
    definition=PromptAgentDefinition(
        model="gpt-5.4-nano",
        instructions=system_prompt,
        tools=all_tools,
    ),
)
```

---

### Task 12: APIM Gateway Integration

**Step 1:** Create APIM operation pointing to the hosted agent endpoint

**Step 2:** Test via APIM URL

**Step 3:** Commit APIM policy XML to `apim-config/`

---

## Phase 2.5 — Clinician Action Features (TDD)

### Task 13: `generate_trial_summary` — RED then GREEN

**Step 1: Write failing test**

```python
from clinical_trials_tools import generate_trial_summary


@respx.mock
def test_generate_trial_summary_returns_plain_language():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = generate_trial_summary("NCT00000001")
    assert "nctId" in result
    assert "plainLanguageSummary" in result
    assert "contactInfo" in result


@respx.mock
def test_generate_trial_summary_with_patient_context():
    respx.get("https://clinicaltrials.gov/api/v2/studies/NCT00000001").mock(
        return_value=httpx.Response(200, json=MOCK_STUDY_DETAIL)
    )
    result = generate_trial_summary("NCT00000001", patient_context="58-year-old male with PTSD")
    assert result["patientContext"] == "58-year-old male with PTSD"
```

**Step 2: Run — expect FAIL**

**Step 3: Implement** — fetches full study record, extracts key sections, structures for the LLM to render as plain-language handout

**Step 4: Run — expect PASS**

**Step 5: Commit**

---

### Task 14: `draft_referral_message` — RED then GREEN

**Step 1: Write failing test**

```python
from clinical_trials_tools import draft_referral_message


def test_draft_referral_message_structure():
    result = draft_referral_message(
        nct_id="NCT00000001",
        patient_summary="58-year-old male veteran, treatment-resistant PTSD, failed PE + sertraline",
        clinician_name="Dr. Jane Smith",
        clinician_facility="Portland VAMC",
    )
    assert result["nctId"] == "NCT00000001"
    assert "NCT00000001" in result["messageBody"]
    assert "Dr. Jane Smith" in result["messageBody"]
    assert "Portland VAMC" in result["messageBody"]


def test_draft_referral_message_no_phi_warning():
    result = draft_referral_message(
        nct_id="NCT00000001",
        patient_summary="62-year-old female with diabetes",
    )
    assert "disclaimer" in result
    assert "PHI" in result["disclaimer"] or "identifiable" in result["disclaimer"].lower()
```

**Step 2: Run — expect FAIL**

**Step 3: Implement** — generates a structured message dict (not sent anywhere, returned to the LLM to display)

**Step 4: Run — expect PASS**

**Step 5: Commit + run test-reviewer agent**

---

### Task 15: Integration test — full flow

**Step 1: Write integration test**

A test that simulates: search → pick a study → generate summary → draft referral, using mocked CTG API responses throughout.

**Step 2: Verify all 7 tools pass as a suite**

Run: `pytest test_clinical_trials_tools.py -v`
Expected: All tests pass

**Step 3: Commit**

```bash
git commit -am "test: add integration flow test for search-to-referral pipeline"
```

**Step 4: Run wwvd agent for pre-commit readiness check** (secrets audit)

---

## Phase 3 — VA Enhancements

### Task 16: VA pre-built condition shortcuts

Add a helper that maps common VA condition shortcodes to optimized CTG search terms.

### Task 17: Geo-filter for VA medical centers

Add a lookup table of VA facility coordinates → `filter.geo` parameter.

### Task 18: CSV export

Add a tool that returns the CTG API CSV download URL for the current search.

---

## Execution Notes

### Skills to invoke during execution

| When | Skill |
|------|-------|
| Before writing any code | `test-driven-development` — RED first |
| After each 3-task batch | `test-reviewer` agent — audit test quality |
| Before each commit | `wwvd` agent — secrets audit |
| After all Phase 1 tasks | `requesting-code-review` — self-review |
| After Phase 2 deploy | `verification-before-completion` — verify claims |
| If any test fails unexpectedly | `systematic-debugging` — diagnose, don't guess |

### Agent dispatch pattern

Use `subagent-driven-development` for Phase 1 tasks (they're independent). Each subagent gets:
1. The task from this plan
2. The spec.md for context
3. Instructions to follow TDD
