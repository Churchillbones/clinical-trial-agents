"""Clinical Trials Search agent — Microsoft Agent Framework entry point.

Hosts the seven ClinicalTrials.gov tools as an Agent Framework ``Agent`` served
over the OpenAI Responses protocol via ``ResponsesHostServer`` on :8088. This is
the entry point the Foundry Toolkit runs locally (F5 / Agent Inspector) and
deploys as a Foundry hosted agent (the Dockerfile runs ``python main.py``).

All ClinicalTrials.gov logic lives in ``clinical_trials_tools`` (covered by the
pytest suite); the functions below are thin, async Agent Framework ``@tool``
wrappers around it. They never raise: the model always gets a JSON object,
either the tool's payload or ``{"error": {"type", "message", "retryable"}}``
(see ``tool_error``), so validation, PHI-screen and upstream failures reach it
in a form it can act on instead of the framework's bare "Function failed."
"""

from __future__ import annotations

import functools
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from agent_framework import Agent, FunctionTool, tool
from dotenv import load_dotenv
from pydantic import Field

import clinical_trials_tools as ct

if TYPE_CHECKING:
    from agent_framework_foundry_hosting import ResponsesHostServer

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
# Tool results and errors.
# --------------------------------------------------------------------------- #
ToolErrorType = Literal[
    "invalid_input",  # bad arguments, e.g. an NCT ID in the wrong format
    "phi_detected",  # the deterministic PHI screen rejected free text
    "not_found",  # ClinicalTrials.gov answered 404 (e.g. unknown NCT ID)
    "upstream_unavailable",  # any other ClinicalTrials.gov / transport failure
    "internal",  # a bug; details are logged server-side only
]

# clinical_trial_harness.require_deidentified_text() raises a ValueError whose
# message starts with this. It is the only thing that tells a PHI rejection
# apart from other input-validation ValueErrors without changing that module.
PHI_ERROR_PREFIX = "Potential PHI detected"

INTERNAL_ERROR_MESSAGE = (
    "The tool failed because of an internal error; details were logged for the "
    "service team. Tell the clinician this step could not be completed."
)


def tool_error(
    error_type: ToolErrorType, message: str, *, retryable: bool = False
) -> dict[str, Any]:
    """Build the structured error result a tool returns instead of raising."""
    return {"error": {"type": error_type, "message": message, "retryable": retryable}}


def _error_from_exception(exc: Exception) -> dict[str, Any]:
    """Map a tool exception to a model-safe structured error.

    Only messages that ``clinical_trials_tools`` guarantees are safe to show
    (``ValueError`` from validation / the PHI screen, ``ClinicalTrialsApiError``)
    are passed through. Anything else is a bug: the model gets a generic
    message, never the exception text.
    """
    if isinstance(exc, ct.ClinicalTrialsApiError):
        status_code = getattr(exc, "status_code", None)
        return tool_error(
            "not_found" if status_code == 404 else "upstream_unavailable",
            str(exc) or "The ClinicalTrials.gov request failed.",
            retryable=bool(getattr(exc, "retryable", False)),
        )
    if isinstance(exc, ValueError):
        message = str(exc) or "The tool arguments were rejected."
        error_type: ToolErrorType = (
            "phi_detected" if message.startswith(PHI_ERROR_PREFIX) else "invalid_input"
        )
        return tool_error(error_type, message)
    return tool_error("internal", INTERNAL_ERROR_MESSAGE)


def _guarded(
    func: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Make an async tool return a structured error instead of raising.

    ``functools.wraps`` carries over the name, docstring, annotations and (via
    ``__wrapped__``) the signature — everything ``@tool`` reads to build the
    tool description and parameter JSON schema. Logs record only the tool
    name, outcome and duration, never arguments or results (possible PHI).
    ``asyncio.CancelledError`` is not an ``Exception`` and still propagates.
    """
    name = func.__name__

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            result = _error_from_exception(exc)
            error = result["error"]
            elapsed_ms = (time.perf_counter() - started) * 1000
            if error["type"] == "internal":
                logger.exception(
                    "tool=%s outcome=internal exception_type=%s duration_ms=%.0f",
                    name,
                    type(exc).__name__,
                    elapsed_ms,
                )
            elif error["type"] == "upstream_unavailable":
                logger.warning(
                    "tool=%s outcome=upstream_unavailable status_code=%s "
                    "retryable=%s duration_ms=%.0f",
                    name,
                    getattr(exc, "status_code", None),
                    error["retryable"],
                    elapsed_ms,
                )
            else:
                logger.info(
                    "tool=%s outcome=%s duration_ms=%.0f", name, error["type"], elapsed_ms
                )
            return result
        logger.info(
            "tool=%s outcome=ok duration_ms=%.0f",
            name,
            (time.perf_counter() - started) * 1000,
        )
        return result

    return wrapper


# --------------------------------------------------------------------------- #
# Agent Framework tool wrappers — thin async shims over clinical_trials_tools.
# The model sees these; the underlying functions hold the tested CTG logic.
# ``@_guarded`` sits under ``@tool`` so the FunctionTool wraps the guarded
# coroutine function.
# --------------------------------------------------------------------------- #
@tool(approval_mode="never_require")
@_guarded
async def search_studies(
    term: Annotated[str | None, Field(description="General search term across title, condition, intervention, and summary.")] = None,
    condition: Annotated[str | None, Field(description="Patient's condition or diagnosis, e.g. 'PTSD', 'type 2 diabetes'.")] = None,
    intervention: Annotated[str | None, Field(description="Treatment or intervention of interest, e.g. 'psilocybin', 'CBT'.")] = None,
    location: Annotated[str | None, Field(description="City, state, or VA facility near the patient, e.g. 'Portland VA'.")] = None,
    sponsor: Annotated[str | None, Field(description="Sponsor or collaborator name, e.g. 'Department of Veterans Affairs'.")] = None,
    age: Annotated[str | None, Field(description="Patient age or age group, e.g. '62' or 'OLDER_ADULT'.")] = None,
    sex: Annotated[SexType | None, Field(description="Patient sex for eligibility filtering.")] = None,
    status: Annotated[StatusType | None, Field(description="Recruitment status. Defaults to RECRUITING.")] = "RECRUITING",
    phase: Annotated[PhaseType | None, Field(description="Study phase filter.")] = None,
    page_size: Annotated[int, Field(description="Number of results to return (1-20, default 10).", ge=1, le=20)] = 10,
) -> dict[str, Any]:
    """Search ClinicalTrials.gov for studies. Use whenever the user asks about
    clinical trials, studies, or research for a condition, intervention, or
    location."""
    return await ct.search_studies(
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
@_guarded
async def get_study_details(
    nct_id: Annotated[str, Field(description="The NCT identifier, e.g. 'NCT12345678'.")],
) -> dict[str, Any]:
    """Retrieve the complete details of a clinical trial by NCT ID — eligibility,
    locations with contacts, status, and design."""
    return await ct.get_study_details(nct_id)


@tool(approval_mode="never_require")
@_guarded
async def check_eligibility(
    nct_id: Annotated[str, Field(description="The NCT identifier, e.g. 'NCT12345678'.")],
) -> dict[str, Any]:
    """Retrieve eligibility criteria, age range, sex, and healthy-volunteer status
    for a study without pulling the full record."""
    return await ct.check_eligibility(nct_id)


@tool(approval_mode="never_require")
@_guarded
async def count_studies(
    term: Annotated[str | None, Field(description="General search term.")] = None,
    condition: Annotated[str | None, Field(description="Condition or diagnosis.")] = None,
    intervention: Annotated[str | None, Field(description="Treatment or intervention.")] = None,
    sponsor: Annotated[str | None, Field(description="Sponsor or collaborator name.")] = None,
    status: Annotated[StatusType | None, Field(description="Recruitment status filter.")] = None,
) -> dict[str, Any]:
    """Count studies matching the criteria without returning full records."""
    count = await ct.count_studies(
        term=term,
        condition=condition,
        intervention=intervention,
        sponsor=sponsor,
        status=status,
    )
    return {"count": count}


@tool(approval_mode="never_require")
@_guarded
async def list_enum_values(
    enum_name: Annotated[str, Field(description="Coded field name, e.g. 'Phase', 'StudyType', 'OverallStatus'.")],
) -> dict[str, Any]:
    """List valid values for a ClinicalTrials.gov coded field."""
    values = await ct.list_enum_values(enum_name)
    return {"enumName": enum_name, "values": values}


@tool(approval_mode="never_require")
@_guarded
async def generate_trial_summary(
    nct_id: Annotated[str, Field(description="The NCT identifier.")],
    patient_context: Annotated[str | None, Field(description="Optional short, de-identified patient context (NO PHI).")] = None,
) -> dict[str, Any]:
    """Build a structured, patient-friendly summary payload for a study, ready to
    render as a plain-language handout."""
    return await ct.generate_trial_summary(nct_id, patient_context=patient_context)


@tool(approval_mode="never_require")
@_guarded
async def draft_referral_message(
    nct_id: Annotated[str, Field(description="The NCT identifier.")],
    patient_summary: Annotated[str, Field(description="De-identified clinical context for the candidate (NO PHI).")],
    clinician_name: Annotated[str | None, Field(description="Referring clinician's name for the signature.")] = None,
    clinician_facility: Annotated[str | None, Field(description="Referring clinician's VA facility.")] = None,
) -> dict[str, Any]:
    """Draft a de-identified referral message to a study coordinator for the
    clinician to review. Never transmits anything."""
    return await ct.draft_referral_message(
        nct_id,
        patient_summary,
        clinician_name=clinician_name,
        clinician_facility=clinician_facility,
    )


AGENT_TOOLS: list[FunctionTool] = [
    search_studies,
    get_study_details,
    check_eligibility,
    count_studies,
    list_enum_values,
    generate_trial_summary,
    draft_referral_message,
]


# --------------------------------------------------------------------------- #
# Name-based dispatch over the same FunctionTool objects the agent uses, so the
# integration test runs exactly the model's path: the framework's argument
# validation, then the guarded wrapper and its error mapping.
# --------------------------------------------------------------------------- #
TOOL_DISPATCH: dict[str, FunctionTool] = {
    agent_tool.name: agent_tool for agent_tool in AGENT_TOOLS
}


async def dispatch_tool(
    name: str, arguments: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Execute a tool by name the way the agent's tool loop does.

    Returns the tool's JSON result or a structured ``{"error": ...}`` (see
    ``tool_error``); it never raises for an unknown tool, arguments the tool's
    schema rejects, or a tool failure.
    """
    agent_tool = TOOL_DISPATCH.get(name)
    if agent_tool is None:
        return tool_error("invalid_input", f"Unknown tool: {name}")
    try:
        return await agent_tool.invoke(arguments=dict(arguments or {}), skip_parsing=True)
    except TypeError:
        # FunctionTool.invoke raises TypeError when the arguments fail the
        # tool's schema (the agent loop reports "Argument parsing failed").
        # Its text can echo argument values, so it is not passed on.
        logger.info("tool=%s outcome=invalid_input reason=argument_schema", name)
        return tool_error(
            "invalid_input",
            f"The arguments for {name} do not match its parameter schema.",
        )


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


async def _close_http_client() -> None:
    """Close the shared ClinicalTrials.gov HTTP client; never fails shutdown."""
    try:
        await ct.aclose_client()
    except Exception:
        logger.exception("Failed to close the ClinicalTrials.gov HTTP client")
    else:
        logger.info("Closed the ClinicalTrials.gov HTTP client")


def _register_shutdown_cleanup(server: ResponsesHostServer) -> None:
    """Close the shared HTTP client during the host's graceful shutdown.

    ``AgentServerHost.shutdown_handler()`` (azure-ai-agentserver-core) keeps a
    single handler, and ``ResponsesAgentServerHost.__init__`` has already put
    its own there: it cancels in-flight responses and waits for background
    runs. Registering ours on its own would silently replace that drain, so we
    chain onto it: the existing handler runs first, then the client is closed
    once nothing is using it. The SDK has no public getter for the registered
    handler, hence the read of ``_shutdown_fn``.
    """
    if not hasattr(server, "_shutdown_fn"):
        logger.warning(
            "Hosting SDK shutdown hook not found; the ClinicalTrials.gov HTTP "
            "client will not be closed explicitly on shutdown"
        )
        return
    responses_shutdown = server._shutdown_fn

    async def _shutdown() -> None:
        try:
            if responses_shutdown is not None:
                await responses_shutdown()
        finally:
            await _close_http_client()

    server.shutdown_handler(_shutdown)


def build_server(agent: Agent, **server_kwargs: Any) -> ResponsesHostServer:
    """Serve ``agent`` over the Responses protocol, with HTTP-client cleanup."""
    from agent_framework_foundry_hosting import ResponsesHostServer

    server = ResponsesHostServer(agent, **server_kwargs)
    _register_shutdown_cleanup(server)
    return server


def main() -> None:
    agent = build_agent()
    server = build_server(agent)
    logger.info("Starting Clinical Trials hosted agent (Responses protocol) on :8088")
    server.run()


if __name__ == "__main__":
    main()
