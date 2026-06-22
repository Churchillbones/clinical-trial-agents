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
| `main.py` | Agent Framework entry point — `Agent` + `ResponsesHostServer` on :8088, wires the 7 `@tool` wrappers |
| `system_prompt.txt` | Agent instructions, loaded by `main.py` |
| `agent.yaml` / `agent.manifest.yaml` | Hosted-agent deploy config read by the Foundry Toolkit / azd |
| `Dockerfile` | Container image for the hosted agent (`python main.py`) |
| `.vscode/` | Debug configs for F5 + the Foundry Toolkit Agent Inspector |
| `tool_definitions.py` | `FunctionTool` schemas for the alternative `deploy_agent.py` prompt-agent path |
| `deploy_agent.py` | Alternative: registers a `PromptAgentDefinition` version (`agents.create_version`) |
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

`main.py` is a Microsoft Agent Framework agent served over the Responses protocol
on `http://localhost:8088`. It uses **Entra ID** auth (`DefaultAzureCredential`) —
sign in to Azure in the Foundry Toolkit (and select the 806 project) first.

- **F5** runs the server under `agentdev` and opens the Foundry Toolkit Agent
  Inspector (see `.vscode/`), or run it directly:

  ```powershell
  .\.venv\Scripts\python.exe main.py
  ```

- Test from the Agent Inspector, or with curl once signed in:

  ```powershell
  curl -sS -H "Content-Type: application/json" -X POST `
    http://localhost:8088/responses `
    -d '{"input": "How many PTSD studies are recruiting?", "stream": false}'
  ```

## Environment variables

| Variable | Description |
|----------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | 806 Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model deployment used by `main.py` (Agent Framework) |
| `FOUNDRY_MODEL_NAME` | Model deployment for the `deploy_agent.py` path (e.g. `gpt-5.4-nano`) |
| `FOUNDRY_MODEL_NAME_REASONING` | Reasoning model for eligibility (e.g. `gpt-5.5`) |
| `FOUNDRY_AGENT_NAME` | Registered agent name |

## Deploy to Foundry

Primary path — Foundry Toolkit hosted agent (Agent Framework):

1. Sign in to Azure in the Foundry Toolkit and select the 806 project. Deploying
   needs the **Foundry Project Manager** role on the project (or **Owner** on the
   resource group).
2. Command Palette → **Foundry Toolkit: Deploy Hosted Agent** → Code → Remote.
   The wizard reads `agent.yaml` to auto-populate settings.
3. Smoke test in the Agent Playground:
   "Find recruiting PTSD trials near Portland VA"; stream logs from the Logs tab.

Alternative — SDK prompt-agent + APIM:

- `deploy_agent.py` registers a `PromptAgentDefinition` version via
  `agents.create_version()` (needs `az login`).
- Expose it through APIM with `apim-clinical-trials-policy.xml` (routes
  `POST /api/projects/806/responses` → `/openai/v1/responses`).

See Phase 2 in [implementation-plan.md](implementation-plan.md).
