# Phase 2 Prompt — Clinical Trials Search Agent (Foundry + APIM Deploy)

> Paste this into a fresh context window (or tell the agent to read this file) to continue the build at Phase 2.

You are continuing a multi-phase build of the **Clinical Trials Search Agent** — an Azure AI Foundry hosted agent that helps VA clinicians find recruiting ClinicalTrials.gov studies. **Phase 1 is complete; your job is Phase 2 (deploy to Foundry + APIM).**

## First, orient yourself

Read these files before doing anything (they are the source of truth):

- `clinical-trials-agent/spec.md` — design, SDK patterns, system prompt, decisions
- `clinical-trials-agent/implementation-plan.md` — see **Phase 2, Tasks 11–12**
- `clinical-trials-agent/todo.md` — Phase 2 checklist + gate
- `clinical-trials-agent/README.md` — setup & deploy notes
- Repository memory at `/memories/repo/env-notes.md` — environment + APIM gotchas

Project folder (absolute):
`c:\Users\VHASTCLambA\OneDrive - Department of Veterans Affairs\Simlab\806\clinical-trials-agent`

## Current state (Phase 1 done — do NOT rebuild)

- 7 tools implemented + TDD-tested in `clinical_trials_tools.py`: `search_studies`, `get_study_details`, `check_eligibility`, `count_studies`, `list_enum_values`, `generate_trial_summary`, `draft_referral_message`.
- `tool_definitions.py` exposes `all_tools` (7 `FunctionTool` objects, `strict=True`).
- `system_prompt.txt` holds the agent instructions.
- `main.py` has the function-call loop, `dispatch_tool`, and a `POST /responses` server on `:8088`.
- **17 tests pass** (16 unit + 1 integration), all CTG calls mocked with `respx`.
- Secrets audit passed; git repo is scoped to the `clinical-trials-agent/` folder (nested), 14 commits.

## Environment specifics (important — learned the hard way)

- Use the **`py`** launcher (Python 3.13.7). `python`/`python3` are non-functional Windows Store stubs.
- Virtual env: `clinical-trials-agent\.venv`. Run tests with `.\.venv\Scripts\python.exe -m pytest`.
- `pytest.ini` disables the cache (`-p no:cacheprovider`) because OneDrive blocks `.pytest_cache` (WinError 5). Keep it.
- `azure-ai-projects` 2.2.0 and `openai` 2.43.0 are installed and working. `azure.ai.projects.models.FunctionTool.as_dict()` emits OpenAI Responses-format tool dicts (keys: `type/name/description/parameters/strict`).
- Git identity is already set (Allan Lamb / allan.lamb@va.gov). Commit after each task as the plan specifies.
- Env vars (already in `.env.example`):
  - `FOUNDRY_PROJECT_ENDPOINT=https://spd2494-dev-eus2-806-fdy.services.ai.azure.com/api/projects/806`
  - `FOUNDRY_MODEL_NAME=gpt-5.4-nano`
  - `FOUNDRY_MODEL_NAME_REASONING=gpt-5.5`
  - `FOUNDRY_AGENT_NAME=clinical-trials-search`
  - Copy `.env.example` → `.env` (gitignored) and fill in if needed.

## Auth & network gotchas (verify early)

- Auth is **Entra ID** via `DefaultAzureCredential` → requires `az login` and the **Azure AI User** role on the Foundry resource. The `az` CLI may NOT be installed/on PATH — check with `az version`; if missing, install or have the user `az login` first. **Do not put raw keys in code.**
- On VA VPN, direct `*.services.ai.azure.com` calls can return an **HTTP 503 VA TIC block page** (Restricted App-ID `azure-ai-services`). The APIM hostname is reachable. If you hit 503s, note it and prefer routing through APIM.
- APIM caveats from prior project work: subscription-key header is **`api-key`** (not `Ocp-Apim-Subscription-Key`) on the existing APIs; date-string `api-version` values returned `400 "API version not supported"` while `v1` returned `404` — watch for version-set config conflicts. APIM policy samples live in `../apim-config/`.

## Optional pre-work (recommended, ~10 min — from the Phase 1 test review)

Do these first to keep the gate green, using TDD:

1. Add two tests for `main.dispatch_tool`: unknown-tool → `{"error": "Unknown tool: ..."}`, and tool-exception wrapping (mock a CTG 500/timeout) → `{"error": "<ExcType>: ..."}`.
2. Tighten the two weak URL assertions in `test_clinical_trials_tools.py` to check parsed query params (`request.url.params["filter.overallStatus"] == "RECRUITING"`, `params["query.locn"] == "Portland VA"`).
3. Refactor `main.py` so import-time side effects (`load_dotenv()`, `system_prompt.txt` read) move into `main()`/`run_agent()`; keep `dispatch_tool` import-safe. Re-run the full suite (expect green) and commit.

## Phase 2 tasks

### Task 11 — Deploy hosted agent + register version

1. Create `deploy_agent.py` that builds the `AIProjectClient` (endpoint from env, `DefaultAzureCredential`), reads `system_prompt.txt`, imports `all_tools` from `tool_definitions`, and registers the agent:

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

2. Deploy via the **Foundry Toolkit** VS Code extension: Command Palette → `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote. (Install the extension if absent.)
3. Verify the agent appears in the Foundry portal under Hosted Agents.
4. Playground smoke test: **"Find recruiting PTSD trials near Portland VA"** — confirm a tool call fires and real CTG data returns.
5. Commit (e.g., `feat: add deploy_agent.py and register agent version`).

### Task 12 — APIM gateway

1. Create an APIM operation routing to the hosted agent endpoint (reuse the existing APIM gateway; see `../apim-config/` for patterns).
2. Test via the APIM URL (mind the `api-key` header and `api-version` caveats above).
3. Save the policy XML to `clinical-trials-agent/` or `../apim-config/` and commit.

## Definition of done (Phase 2 gate)

- Agent responds correctly in the Foundry Playground.
- Agent is callable through APIM.
- All existing tests still pass.
- Apply the **verification-before-completion** skill: run the commands and paste real output before claiming success — no unverified "done."

## Constraints

- Follow **TDD** for any new tool/logic code (RED → GREEN → commit). Deploy scripts may not be unit-testable — verify by running them.
- Never commit secrets; `.env` stays gitignored. The Foundry endpoint hostname is a non-sensitive resource identifier, not a secret.
- Use the `wwvd` agent for a secrets/pre-commit check before commits, and `test-reviewer` after adding tests. **Note:** in the last session the `wwvd` subagent had no terminal/file tools and couldn't scan — if that recurs, run the audit yourself (`git ls-files`, `git check-ignore`, content grep for keys/tokens).
- If blocked by auth/VPN/TIC, stop and report the exact error rather than guessing.

Start by reading the four project docs + repo memory, confirm the test suite is green, then proceed with the optional pre-work and Task 11.
