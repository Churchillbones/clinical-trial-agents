# Clinical Trials Agent — Development Checklist

> **Status (2026-06-22):** 19 tests passing. The agent is now a **Microsoft Agent
> Framework** hosted agent — `main.py` uses `FoundryChatClient` + `Agent` +
> `ResponsesHostServer` (verified starting on :8088) with the 7 CTG tools as
> `@tool` wrappers; toolkit scaffolding added (`agent.yaml`, `agent.manifest.yaml`,
> `Dockerfile`, `.vscode/`). The Foundry Toolkit extension is installed.
> **Remaining to test in Foundry:** sign in to the toolkit + select the 806
> project (Foundry Project Manager role), then F5 / Deploy Hosted Agent; mind VA
> TIC/VPN. `deploy_agent.py` + the APIM policy remain as an alternative
> prompt-agent path.

> **Source of truth:** `spec.md` (tool schemas, system prompt, API mappings)
> **Execution guide:** `implementation-plan.md` (TDD steps, code, commands)
> **Skills:** TDD, test-reviewer, wwvd, systematic-debugging, verification-before-completion

---

## Phase 1 — Core Tools (TDD)

- [x] **Task 1: Project Setup**
  - [ ] Create `requirements.txt` (azure-ai-projects, httpx, pytest, respx)
  - [ ] Create `.env.example` with Foundry endpoint vars
  - [ ] Create `.gitignore`
  - [ ] Create empty `clinical_trials_tools.py` and `test_clinical_trials_tools.py`
  - [ ] `pip install -r requirements.txt`
  - [ ] Verify `pytest` runs (0 collected)
  - [ ] Commit: `chore: scaffold clinical-trials-agent project`

- [x] **Task 2: `search_studies`** *(RED → GREEN → REFACTOR)*
  - [ ] Write 3 failing tests (by condition, defaults to recruiting, with location)
  - [ ] Verify tests fail (ImportError)
  - [ ] Implement `search_studies()` — maps params to CTG `/studies` endpoint
  - [ ] Verify 3 tests pass
  - [ ] Commit: `feat: add search_studies tool with TDD tests`

- [x] **Task 3: `get_study_details`** *(RED → GREEN)*
  - [ ] Write 2 failing tests (returns full record, invalid NCT raises)
  - [ ] Verify tests fail
  - [ ] Implement `get_study_details()` — calls `/studies/{nctId}`
  - [ ] Verify tests pass
  - [ ] Commit: `feat: add get_study_details tool`

- [x] **Task 4: `check_eligibility`** *(RED → GREEN)*
  - [ ] Write failing test (returns eligibility fields)
  - [ ] Verify fails
  - [ ] Implement `check_eligibility()` — extracts eligibilityModule
  - [ ] Verify passes
  - [ ] Commit: `feat: add check_eligibility tool`

- [x] **Task 5: `count_studies`** *(RED → GREEN)*
  - [ ] Write 2 failing tests (returns count, no filters)
  - [ ] Verify fail
  - [ ] Implement `count_studies()` — calls `/stats/size`
  - [ ] Verify pass
  - [ ] Commit: `feat: add count_studies tool`

- [x] **Task 6: `list_enum_values`** *(RED → GREEN)*
  - [ ] Write 2 failing tests (returns values, unknown enum)
  - [ ] Verify fail
  - [ ] Implement `list_enum_values()` — calls `/studies/enums`
  - [ ] Verify pass
  - [ ] Commit: `feat: add list_enum_values tool`

- [x] **Task 7: Error handling + edge cases**
  - [ ] Write tests: API timeout, empty results
  - [ ] Verify all tests pass (should already)
  - [ ] Commit: `test: add error handling and edge case tests`
  - [ ] **Run `test-reviewer` agent** on test file → fix any issues

### Phase 1 Gate
- [x] `pytest test_clinical_trials_tools.py -v` — ALL PASS
- [ ] `test-reviewer` agent approves test quality
- [ ] `wwvd` agent pre-commit check (secrets audit)

---

## Phase 1B — Tool Definitions + System Prompt

- [x] **Task 8: `tool_definitions.py`**
  - [ ] Create `FunctionTool` objects for all 7 tools (schemas from spec.md)
  - [ ] Each tool has `strict=True`
  - [ ] Verify: `python -c "from tool_definitions import search_studies_tool"`
  - [ ] Commit: `feat: add FunctionTool definitions for all 7 tools`

- [x] **Task 9: `system_prompt.txt`**
  - [ ] Copy system prompt from spec.md
  - [ ] Commit: `feat: add system prompt`

---

## Phase 1C — Agent Wiring

- [x] **Task 10: `main.py`**
  - [ ] Create agent HTTP server entry point (`:8088`)
  - [ ] Load system prompt + tool definitions
  - [ ] Wire function-call loop (function_call → dispatch → FunctionCallOutput)
  - [ ] Test locally: F5 or `python main.py`
  - [ ] curl test: `POST /responses` with "How many PTSD studies are recruiting?"
  - [ ] Verify tool is called and answer is returned
  - [ ] Commit: `feat: add main.py agent entry point with function-call loop`

### Phase 1C Gate
- [ ] Agent responds to curl with real CTG data
- [ ] Function-call loop handles multiple tools in one conversation
- [ ] **Run `requesting-code-review`** skill — self-review before deploy

---

## Phase 2 — Deploy to Foundry

- [ ] **Task 11: Deploy via Foundry Toolkit**
  - [x] Register agent version via `agents.create_version()` — implemented in `deploy_agent.py`
  - [ ] `Foundry Toolkit: Deploy Hosted Agent` → Code → Remote — **BLOCKED**: Foundry Toolkit extension not installed
  - [ ] Verify agent appears in Foundry portal — blocked on deploy
  - [ ] Test in Playground tab: "Find recruiting PTSD trials near Portland VA" — blocked on deploy
  - Note: `deploy_agent.py` runs correctly up to auth; `DefaultAzureCredential`
    finds no token source (no Azure CLI / Az PowerShell / VS Code sign-in). Run
    `az login` (Azure AI User role) then `.\.venv\Scripts\python.exe deploy_agent.py`.

- [ ] **Task 12: APIM Gateway**
  - [x] APIM operation policy authored → `apim-clinical-trials-policy.xml` (XML validated well-formed)
  - [ ] Create the APIM operation + named value `foundry-backend-api-key` — **BLOCKED**: needs APIM admin
  - [ ] Test via APIM URL — blocked on deploy + APIM config

### Phase 2 Gate
- [x] Phase 2 code artifacts committed (`deploy_agent.py`, APIM policy XML)
- [ ] Agent works in Foundry Playground — blocked (auth/extension)
- [ ] Agent callable via APIM — blocked (deploy + APIM admin)
- [x] **`verification-before-completion`** — 19 tests green; deploy run surfaced the exact auth error (reported, not guessed)

---

## Phase 2.5 — Clinician Action Features (TDD)

- [x] **Task 13: `generate_trial_summary`** *(RED → GREEN)*
  - [ ] Write 2 failing tests (plain language output, with patient context)
  - [ ] Implement — fetches study, structures for patient handout
  - [ ] Verify pass
  - [ ] Commit: `feat: add generate_trial_summary tool`

- [x] **Task 14: `draft_referral_message`** *(RED → GREEN)*
  - [ ] Write 2 failing tests (message structure, PHI disclaimer)
  - [ ] Implement — generates referral message dict with no-PHI guardrails
  - [ ] Verify pass
  - [ ] Commit: `feat: add draft_referral_message tool`
  - [ ] **Run `test-reviewer` agent** on updated test file

- [x] **Task 15: Integration test — full flow**
  - [ ] Write test: search → select → summary → referral (mocked API)
  - [ ] Verify ALL tests pass
  - [ ] Commit: `test: add integration flow test`
  - [ ] **Run `wwvd` agent** — pre-commit readiness check

### Phase 2.5 Gate
- [ ] All 7 tools have passing tests
- [ ] Integration test covers the full clinician workflow
- [ ] Updated tools deployed to Foundry
- [ ] Test in Playground: search → eligibility → summary → referral

---

## Phase 3 — VA Enhancements

- [ ] **Task 16: VA condition shortcuts**
  - [ ] Map common VA conditions → optimized CTG search terms
  - [ ] Test with TDD
  - [ ] Commit

- [ ] **Task 17: Geo-filter for VA facilities**
  - [ ] VA facility coordinate lookup → `filter.geo` param
  - [ ] Test with TDD
  - [ ] Commit

- [ ] **Task 18: CSV export**
  - [ ] Return CTG CSV download URL for current search
  - [ ] Test with TDD
  - [ ] Commit

---

## Phase 4 — Future (not started)

- [ ] Bing grounding tool for PubMed / literature search
- [ ] VistA/Cerner EHR integration for patient pre-fill
- [ ] Response caching layer
- [ ] MCP Server packaging for tool reuse
- [ ] Foundry Memory Stores for conversation history
- [ ] Teams / M365 publish

---

## Quick Reference — Skills & Agents

| Trigger | Invoke |
|---------|--------|
| Writing any new code | Skill: `test-driven-development` — write test first |
| After each 3-task batch | Agent: `test-reviewer` — audit test quality |
| Before any commit | Agent: `wwvd` — secrets audit + pre-commit check |
| Test fails unexpectedly | Skill: `systematic-debugging` — diagnose root cause |
| Before claiming "done" | Skill: `verification-before-completion` — run and confirm |
| Phase complete | Skill: `requesting-code-review` — self-review |
| Implementing plan tasks | Skill: `executing-plans` or `subagent-driven-development` |

## Context Files

| File | Purpose |
|------|---------|
| `spec.md` | Source of truth — tool schemas, API mappings, system prompt, decisions |
| `implementation-plan.md` | TDD steps with code, commands, expected outputs |
| `todo.md` | This file — checklist for tracking progress |
