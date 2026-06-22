# Clinical Trials Search Agent

An Azure AI Foundry **hosted agent** that helps VA clinicians find recruiting
clinical trials on [ClinicalTrials.gov](https://clinicaltrials.gov) by patient
profile, check eligibility, generate patient handouts, and draft referral
messages.

See [spec.md](spec.md) for the full design (tool schemas, system prompt, API
mappings) and [implementation-plan.md](implementation-plan.md) for the
task-by-task TDD build. Progress is tracked in [todo.md](todo.md).

## Project layout

| File | Purpose |
|------|---------|
| `clinical_trials_tools.py` | The 7 tool functions wrapping CTG API v2 |
| `tool_definitions.py` | `FunctionTool` schemas (`strict=True`) for all 7 tools |
| `system_prompt.txt` | Agent instructions, loaded by `main.py` |
| `main.py` | Agent entry point — function-call loop, `POST /responses` on :8088 |
| `test_clinical_trials_tools.py` | Unit tests for the tool functions |
| `test_integration.py` | Full search → summary → referral flow (mocked CTG API) |

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in values as needed
```

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

All tests mock the ClinicalTrials.gov API (via `respx`) — no network or Azure
access is required.

## Run locally

The agent uses **Entra ID** auth (`DefaultAzureCredential`), so sign in first:

```powershell
az login
.\.venv\Scripts\python.exe main.py
```

Then call it:

```powershell
curl -sS -H "Content-Type: application/json" -X POST `
  http://localhost:8088/responses `
  -d '{"input": "How many PTSD studies are recruiting?"}'
```

Health check: `GET http://localhost:8088/health`.

## Environment variables

| Variable | Description |
|----------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | 806 Foundry project endpoint |
| `FOUNDRY_MODEL_NAME` | Default model deployment (e.g. `gpt-5.4-nano`) |
| `FOUNDRY_MODEL_NAME_REASONING` | Reasoning model for eligibility (e.g. `gpt-5.5`) |
| `FOUNDRY_AGENT_NAME` | Registered agent name |
| `AGENT_HOST` / `AGENT_PORT` | Local server bind (defaults `0.0.0.0:8088`) |

## Deploy to Foundry (next steps)

1. `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote.
2. Register the agent version with `agents.create_version()` using
   `PromptAgentDefinition(model=..., instructions=SYSTEM_PROMPT, tools=all_tools)`.
3. Test in the Foundry Playground, then expose through the existing APIM gateway.

See Phase 2 in [implementation-plan.md](implementation-plan.md).
