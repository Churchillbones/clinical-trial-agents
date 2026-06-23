"""Clinical Trials Search agent — Microsoft Agent Framework entry point.

Hosts the seven ClinicalTrials.gov tools as an Agent Framework ``Agent`` served
over the OpenAI Responses protocol via ``ResponsesHostServer`` on :8088. This is
the entry point the Foundry Toolkit runs locally (F5 / Agent Inspector) and
deploys as a Foundry hosted agent (the Dockerfile runs ``python main.py``).

All ClinicalTrials.gov logic lives in ``clinical_trials_tools`` (covered by the
pytest suite); the functions below are thin Agent Framework ``@tool`` wrappers
around it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Literal

from agent_framework import Agent, tool
from dotenv import load_dotenv
from pydantic import Field

import clinical_trials_tools as ct

# VA networks perform TLS inspection with a corporate root CA. Make Python trust
# the OS certificate store (where that CA is installed) so outbound HTTPS to the
# Foundry model endpoint and ClinicalTrials.gov verifies. No-op where unneeded.
import truststore

truststore.inject_into_ssl()

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clinical-trials-agent")

DEFAULT_MODEL = "gpt-5.4-nano"
# Prod APIM Responses gateway defaults (local/VA-network backend). The gateway
# fronts the Foundry Responses API and is reachable where the direct
# *.services.ai.azure.com data-plane is blocked by the VA TIC firewall.
DEFAULT_APIM_MODEL = "gpt-5.4"
DEFAULT_APIM_RESPONSES_API_VERSION = "2025-04-01-preview"

StatusType = Literal[
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "ENROLLING_BY_INVITATION",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
]
PhaseType = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
SexType = Literal["MALE", "FEMALE", "ALL"]


def _load_system_prompt() -> str:
    """Read the agent instructions from ``system_prompt.txt`` (lazy)."""
    return (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Agent Framework tool wrappers — thin shims over clinical_trials_tools.
# The model sees these; the underlying functions hold the tested CTG logic.
# --------------------------------------------------------------------------- #
@tool(approval_mode="never_require")
def search_studies(
    term: Annotated[str | None, Field(description="General search term across title, condition, intervention, and summary.")] = None,
    condition: Annotated[str | None, Field(description="Patient's condition or diagnosis, e.g. 'PTSD', 'type 2 diabetes'.")] = None,
    intervention: Annotated[str | None, Field(description="Treatment or intervention of interest, e.g. 'psilocybin', 'CBT'.")] = None,
    location: Annotated[str | None, Field(description="City, state, or VA facility near the patient, e.g. 'Portland VA'.")] = None,
    sponsor: Annotated[str | None, Field(description="Sponsor or collaborator name, e.g. 'Department of Veterans Affairs'.")] = None,
    age: Annotated[str | None, Field(description="Patient age or age group, e.g. '62' or 'OLDER_ADULT'.")] = None,
    sex: Annotated[SexType | None, Field(description="Patient sex for eligibility filtering.")] = None,
    status: Annotated[StatusType | None, Field(description="Recruitment status. Defaults to RECRUITING.")] = "RECRUITING",
    phase: Annotated[PhaseType | None, Field(description="Study phase filter.")] = None,
    page_size: Annotated[int, Field(description="Number of results to return (1-20, default 10).")] = 10,
) -> dict:
    """Search ClinicalTrials.gov for studies. Use whenever the user asks about
    clinical trials, studies, or research for a condition, intervention, or
    location."""
    return ct.search_studies(
        term=term,
        condition=condition,
        intervention=intervention,
        location=location,
        sponsor=sponsor,
        age=age,
        sex=sex,
        status=status,
        phase=phase,
        page_size=page_size,
    )


@tool(approval_mode="never_require")
def get_study_details(
    nct_id: Annotated[str, Field(description="The NCT identifier, e.g. 'NCT12345678'.")],
) -> dict:
    """Retrieve the complete details of a clinical trial by NCT ID — eligibility,
    locations with contacts, status, and design."""
    return ct.get_study_details(nct_id)


@tool(approval_mode="never_require")
def check_eligibility(
    nct_id: Annotated[str, Field(description="The NCT identifier, e.g. 'NCT12345678'.")],
) -> dict:
    """Retrieve eligibility criteria, age range, sex, and healthy-volunteer status
    for a study without pulling the full record."""
    return ct.check_eligibility(nct_id)


@tool(approval_mode="never_require")
def count_studies(
    term: Annotated[str | None, Field(description="General search term.")] = None,
    condition: Annotated[str | None, Field(description="Condition or diagnosis.")] = None,
    intervention: Annotated[str | None, Field(description="Treatment or intervention.")] = None,
    sponsor: Annotated[str | None, Field(description="Sponsor or collaborator name.")] = None,
    status: Annotated[StatusType | None, Field(description="Recruitment status filter.")] = None,
) -> int:
    """Count studies matching the criteria without returning full records."""
    return ct.count_studies(
        term=term,
        condition=condition,
        intervention=intervention,
        sponsor=sponsor,
        status=status,
    )


@tool(approval_mode="never_require")
def list_enum_values(
    enum_name: Annotated[str, Field(description="Coded field name, e.g. 'Phase', 'StudyType', 'OverallStatus'.")],
) -> list[str]:
    """List valid values for a ClinicalTrials.gov coded field."""
    return ct.list_enum_values(enum_name)


@tool(approval_mode="never_require")
def generate_trial_summary(
    nct_id: Annotated[str, Field(description="The NCT identifier.")],
    patient_context: Annotated[str | None, Field(description="Optional short, de-identified patient context (NO PHI).")] = None,
) -> dict:
    """Build a structured, patient-friendly summary payload for a study, ready to
    render as a plain-language handout."""
    return ct.generate_trial_summary(nct_id, patient_context=patient_context)


@tool(approval_mode="never_require")
def draft_referral_message(
    nct_id: Annotated[str, Field(description="The NCT identifier.")],
    patient_summary: Annotated[str, Field(description="De-identified clinical context for the candidate (NO PHI).")],
    clinician_name: Annotated[str | None, Field(description="Referring clinician's name for the signature.")] = None,
    clinician_facility: Annotated[str | None, Field(description="Referring clinician's VA facility.")] = None,
) -> dict:
    """Draft a de-identified referral message to a study coordinator for the
    clinician to review. Never transmits anything."""
    return ct.draft_referral_message(
        nct_id,
        patient_summary,
        clinician_name=clinician_name,
        clinician_facility=clinician_facility,
    )


AGENT_TOOLS = [
    search_studies,
    get_study_details,
    check_eligibility,
    count_studies,
    list_enum_values,
    generate_trial_summary,
    draft_referral_message,
]


# --------------------------------------------------------------------------- #
# Back-compat dispatch used by the integration test (exercises raw functions).
# --------------------------------------------------------------------------- #
TOOL_DISPATCH = {
    "search_studies": ct.search_studies,
    "get_study_details": ct.get_study_details,
    "check_eligibility": ct.check_eligibility,
    "count_studies": ct.count_studies,
    "list_enum_values": ct.list_enum_values,
    "generate_trial_summary": ct.generate_trial_summary,
    "draft_referral_message": ct.draft_referral_message,
}


def dispatch_tool(name: str, arguments: dict) -> dict:
    """Execute a tool by name with keyword arguments.

    Returns a JSON-serialisable dict. Unknown tools and tool errors are
    returned as ``{"error": ...}``.
    """
    func = TOOL_DISPATCH.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = func(**arguments)
    except Exception as exc:  # surface tool failures, don't crash
        logger.exception("Tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict):
        return result
    return {"result": result}


def _build_foundry_chat_client():
    """Default backend — the Foundry project data-plane via Entra ID.

    Used by the hosted-agent deployment (managed identity, in-Azure, no TIC).
    """
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not set")
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.environ.get(
        "FOUNDRY_MODEL_NAME", DEFAULT_MODEL
    )
    return FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=DefaultAzureCredential(),
    )


def _build_apim_chat_client():
    """Local/VA-network backend — the 806 prod APIM gateway.

    The gateway fronts the Azure Foundry **Responses API** at
    ``/foundry/openai/responses`` with ``api-key`` auth and is reachable on the
    VA network, where the direct ``*.services.ai.azure.com`` model data-plane is
    blocked by the VA TIC firewall. The Azure OpenAI SDK appends
    ``/openai/responses``, so the endpoint must point at the ``/foundry`` base.

    Mirrors the proven call in ``secure-messaging-ai/secure_message_analyzer.py``.
    Credentials default to the same env vars that example uses; the prod values
    live in the workspace-root ``.env`` (loaded here for local runs).
    """
    from agent_framework.openai import OpenAIChatClient
    from openai import AsyncAzureOpenAI

    # Prod APIM creds live in the 806 workspace-root .env; load it (without
    # overriding anything already set) so local runs from this subfolder work.
    root_env = Path(__file__).resolve().parent.parent / ".env"
    if root_env.is_file():
        load_dotenv(root_env, override=False)

    base = (
        os.environ.get("APIM_RESPONSES_ENDPOINT")
        or os.environ.get("AZURE_VISION_ENDPOINT", "")
    ).rstrip("/")
    if not base:
        raise RuntimeError(
            "AGENT_CHAT_BACKEND=apim but neither APIM_RESPONSES_ENDPOINT nor "
            "AZURE_VISION_ENDPOINT is set."
        )
    # The gateway nests the Foundry Responses surface under /foundry.
    if not base.endswith("/foundry"):
        base = f"{base}/foundry"

    api_key = (
        os.environ.get("APIM_SUBSCRIPTION_KEY")
        or os.environ.get("AZURE_VISION_API_KEY")
        or os.environ.get("AZURE_VOICELIVE_APIM_SUBSCRIPTION_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY", "")
    )
    if not api_key:
        raise RuntimeError(
            "AGENT_CHAT_BACKEND=apim but no subscription key is set "
            "(APIM_SUBSCRIPTION_KEY / AZURE_VISION_API_KEY)."
        )

    model = os.environ.get("APIM_MODEL", DEFAULT_APIM_MODEL)
    api_version = os.environ.get(
        "AZURE_RESPONSES_API_VERSION", DEFAULT_APIM_RESPONSES_API_VERSION
    )
    logger.info("Using prod APIM Responses backend %s (model=%s)", base, model)
    return OpenAIChatClient(
        model=model,
        async_client=AsyncAzureOpenAI(
            azure_endpoint=base,
            api_key=api_key,
            api_version=api_version,
        ),
    )


def _build_chat_client():
    """Select the model backend from ``AGENT_CHAT_BACKEND`` (foundry|apim).

    ``foundry`` (default) is used by the hosted deployment; ``apim`` targets the
    prod APIM Responses gateway for local testing on the VA network.
    """
    backend = os.environ.get("AGENT_CHAT_BACKEND", "foundry").strip().lower()
    if backend == "apim":
        return _build_apim_chat_client()
    return _build_foundry_chat_client()


def build_agent() -> Agent:
    """Construct the Agent with the seven ClinicalTrials.gov tools.

    The model backend is chosen by ``AGENT_CHAT_BACKEND``: ``foundry`` (default,
    used by the hosted deployment) or ``apim`` (the prod APIM Responses gateway,
    for local testing on the VA network).
    """
    return Agent(
        client=_build_chat_client(),
        instructions=_load_system_prompt(),
        tools=AGENT_TOOLS,
        # History is managed by the hosting infrastructure (no service-side store).
        default_options={"store": False},
    )


def main() -> None:
    from agent_framework_foundry_hosting import ResponsesHostServer

    agent = build_agent()
    server = ResponsesHostServer(agent)
    logger.info("Starting Clinical Trials hosted agent (Responses protocol) on :8088")
    server.run()


if __name__ == "__main__":
    main()
