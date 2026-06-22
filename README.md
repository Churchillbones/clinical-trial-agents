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
| `deploy_agent.py` | Registers the agent version in Foundry (`agents.create_version`) |
| `apim-clinical-trials-policy.xml` | APIM operation policy routing to the hosted agent |
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

## Deploy to Foundry

1. **Register the agent version** (bundles prompt + 7 tools into a
   `PromptAgentDefinition`):

   ```powershell
   az login   # Azure AI User role on the Foundry resource
   .\.venv\Scripts\python.exe deploy_agent.py
   ```

2. **Deploy the hosted-agent code** via the Foundry Toolkit VS Code extension:
   `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote.
3. **Smoke test** in the Foundry Playground:
   "Find recruiting PTSD trials near Portland VA".
4. **Expose via APIM** using `apim-clinical-trials-policy.xml` (routes
   `POST /api/projects/806/responses` → `/openai/v1/responses` with the
   `clinical-trials-search` agent_reference; see the file header for the named
   value / product prerequisites).

See Phase 2 in [implementation-plan.md](implementation-plan.md).
