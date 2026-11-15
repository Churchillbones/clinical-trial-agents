"""Unit tests for the Agent Framework tool wrappers and lifecycle in main.py.

``clinical_trials_tools`` is replaced with async fakes throughout, so these
tests cover main.py alone: the wrappers are coroutines that leave the event
loop free, results always have one JSON-object shape, failures become
structured errors that leak nothing, the framework-generated parameter
schemas are unchanged, and shutdown closes the shared HTTP client after the
Responses drain. No Azure credentials or network access are used.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import subprocess
import sys
import textwrap
import typing
from pathlib import Path
from typing import Any

import httpx
import pytest

import main

NCT = "NCT06123456"
NCT_FORMAT_MESSAGE = "nct_id must match the format NCT########"
PHI_MESSAGE = (
    "Potential PHI detected in patient_summary: date_of_birth. "
    "Remove direct identifiers and retry with de-identified context."
)

# Minimal valid model-side arguments for each tool, in AGENT_TOOLS order.
TOOL_ARGS: dict[str, dict[str, Any]] = {
    "search_studies": {"condition": "PTSD"},
    "get_study_details": {"nct_id": NCT},
    "check_eligibility": {"nct_id": NCT},
    "count_studies": {"condition": "PTSD"},
    "list_enum_values": {"enum_name": "Phase"},
    "generate_trial_summary": {"nct_id": NCT},
    "draft_referral_message": {"nct_id": NCT, "patient_summary": "58-year-old with PTSD"},
}


class FakeApiError(Exception):
    """Stand-in for ``ct.ClinicalTrialsApiError`` with only the agreed attributes."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class FakeCtFunction:
    """Async stand-in for one ``clinical_trials_tools`` function; records calls."""

    def __init__(self, result: Any = None, exc: BaseException | None = None):
        self.result = result
        self.exc = exc
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        await asyncio.sleep(0)  # yield to the loop like a real upstream call
        if self.exc is not None:
            raise self.exc
        return self.result


class StubAgent:
    """Just enough of an agent for ``ResponsesHostServer``; never run."""

    context_providers: list[Any] = []

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the agent is not run in these tests")


@pytest.fixture(autouse=True)
def _fake_api_error_class(monkeypatch):
    # main.py maps by isinstance(exc, ct.ClinicalTrialsApiError) and the agreed
    # status_code / retryable attributes, so a double keeps these tests
    # independent of the real class and its constructor.
    monkeypatch.setattr(main.ct, "ClinicalTrialsApiError", FakeApiError, raising=False)


@pytest.fixture
def local_host_env(monkeypatch):
    # Not "hosted": the Responses host uses its in-memory store, no Azure.
    monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)


def fake_ct(monkeypatch, name: str, *, result: Any = None, exc: BaseException | None = None) -> FakeCtFunction:
    fake = FakeCtFunction(result=result, exc=exc)
    monkeypatch.setattr(main.ct, name, fake)
    return fake


async def invoke(name: str, **arguments: Any) -> Any:
    """Run a tool via ``FunctionTool.invoke``, the call the agent's tool loop makes."""
    return await main.TOOL_DISPATCH[name].invoke(arguments=arguments, skip_parsing=True)


def build_local_server():
    return main.build_server(StubAgent(), configure_observability=None)


async def run_lifespan(app) -> list[str]:
    """Drive one ASGI lifespan startup/shutdown cycle, as Hypercorn does."""
    inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    inbox.put_nowait({"type": "lifespan.startup"})
    inbox.put_nowait({"type": "lifespan.shutdown"})
    sent: list[str] = []

    async def receive() -> dict[str, str]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message["type"])

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return sent


# --------------------------------------------------------------------------- #
# (a) Async wrappers: nothing blocks the event loop.
# --------------------------------------------------------------------------- #
def test_every_tool_wraps_a_coroutine_function():
    assert [agent_tool.name for agent_tool in main.AGENT_TOOLS] == list(TOOL_ARGS)
    for agent_tool in main.AGENT_TOOLS:
        assert inspect.iscoroutinefunction(agent_tool.func), agent_tool.name
        assert inspect.iscoroutinefunction(agent_tool.func.__wrapped__), agent_tool.name


async def test_pending_upstream_call_leaves_readiness_probe_responsive(monkeypatch, local_host_env):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_search(**kwargs):
        started.set()
        await release.wait()
        return {"studies": [], "totalCount": 0}

    monkeypatch.setattr(main.ct, "search_studies", slow_search)
    server = build_local_server()
    tool_call = asyncio.create_task(invoke("search_studies", condition="PTSD"))
    await asyncio.wait_for(started.wait(), timeout=5)

    transport = httpx.ASGITransport(app=server)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        probe = await asyncio.wait_for(client.get("/readiness"), timeout=5)

    assert probe.status_code == 200
    assert not tool_call.done()  # still waiting on "ClinicalTrials.gov"
    release.set()
    assert await asyncio.wait_for(tool_call, timeout=5) == {"studies": [], "totalCount": 0}


# --------------------------------------------------------------------------- #
# (c) Success results: payloads pass through; count/enums become objects.
# --------------------------------------------------------------------------- #
async def test_search_studies_forwards_arguments_and_returns_payload(monkeypatch):
    payload = {"studies": [], "totalCount": 0, "_harness": {"jobId": "find"}}
    fake = fake_ct(monkeypatch, "search_studies", result=payload)

    assert await invoke("search_studies", condition="PTSD", location="Portland VA") == payload
    assert fake.calls == [
        (
            (),
            {
                "term": None,
                "condition": "PTSD",
                "intervention": None,
                "location": "Portland VA",
                "sponsor": None,
                "age": None,
                "sex": None,
                "status": "RECRUITING",
                "phase": None,
                "page_size": 10,
            },
        )
    ]


@pytest.mark.parametrize(
    ("name", "arguments", "expected_call"),
    [
        ("get_study_details", {"nct_id": NCT}, ((NCT,), {})),
        ("check_eligibility", {"nct_id": NCT}, ((NCT,), {})),
        (
            "generate_trial_summary",
            {"nct_id": NCT, "patient_context": "58-year-old with PTSD"},
            ((NCT,), {"patient_context": "58-year-old with PTSD"}),
        ),
        (
            "draft_referral_message",
            {"nct_id": NCT, "patient_summary": "58-year-old with PTSD", "clinician_name": "Dr. Smith"},
            (
                (NCT, "58-year-old with PTSD"),
                {"clinician_name": "Dr. Smith", "clinician_facility": None},
            ),
        ),
    ],
)
async def test_study_tools_forward_arguments_and_return_payload(monkeypatch, name, arguments, expected_call):
    payload = {"nctId": NCT, "_harness": {}}
    fake = fake_ct(monkeypatch, name, result=payload)

    assert await invoke(name, **arguments) == payload
    assert fake.calls == [expected_call]


async def test_count_studies_returns_a_count_object(monkeypatch):
    fake = fake_ct(monkeypatch, "count_studies", result=42)

    assert await invoke("count_studies", condition="PTSD", status="RECRUITING") == {"count": 42}
    assert fake.calls == [
        (
            (),
            {"term": None, "condition": "PTSD", "intervention": None, "sponsor": None, "status": "RECRUITING"},
        )
    ]


async def test_list_enum_values_returns_a_values_object(monkeypatch):
    fake_ct(monkeypatch, "list_enum_values", result=["PHASE1", "PHASE2"])

    assert await invoke("list_enum_values", enum_name="Phase") == {
        "enumName": "Phase",
        "values": ["PHASE1", "PHASE2"],
    }


def test_every_tool_is_annotated_to_return_a_json_object():
    for agent_tool in main.AGENT_TOOLS:
        assert typing.get_type_hints(agent_tool.func)["return"] == dict[str, Any], agent_tool.name


# --------------------------------------------------------------------------- #
# (b) Failures become structured errors instead of "Error: Function failed."
# --------------------------------------------------------------------------- #
ERROR_CASES = [
    pytest.param(
        ValueError(NCT_FORMAT_MESSAGE),
        {"type": "invalid_input", "message": NCT_FORMAT_MESSAGE, "retryable": False},
        id="bad-nct-format",
    ),
    pytest.param(
        ValueError(PHI_MESSAGE),
        {"type": "phi_detected", "message": PHI_MESSAGE, "retryable": False},
        id="phi-screen",
    ),
    pytest.param(
        FakeApiError("Study NCT06123456 was not found.", status_code=404),
        {"type": "not_found", "message": "Study NCT06123456 was not found.", "retryable": False},
        id="404-not-found",
    ),
    pytest.param(
        FakeApiError("ClinicalTrials.gov is unavailable.", status_code=503, retryable=True),
        {"type": "upstream_unavailable", "message": "ClinicalTrials.gov is unavailable.", "retryable": True},
        id="503-retryable",
    ),
    pytest.param(
        FakeApiError("ClinicalTrials.gov rejected the request.", status_code=400),
        {"type": "upstream_unavailable", "message": "ClinicalTrials.gov rejected the request.", "retryable": False},
        id="400-not-retryable",
    ),
    pytest.param(
        FakeApiError("Could not reach ClinicalTrials.gov.", status_code=None, retryable=True),
        {"type": "upstream_unavailable", "message": "Could not reach ClinicalTrials.gov.", "retryable": True},
        id="transport-no-status",
    ),
]


@pytest.mark.parametrize("name", list(TOOL_ARGS))
@pytest.mark.parametrize(("exc", "expected_error"), ERROR_CASES)
async def test_tool_failures_become_structured_errors(monkeypatch, name, exc, expected_error):
    fake_ct(monkeypatch, name, exc=exc)

    assert await invoke(name, **TOOL_ARGS[name]) == {"error": expected_error}


@pytest.mark.parametrize("name", list(TOOL_ARGS))
async def test_unexpected_exception_is_internal_and_leaks_no_exception_text(monkeypatch, caplog, name):
    secret = "Jane Q. Veteran 123-45-6789"
    fake_ct(monkeypatch, name, exc=RuntimeError(f"KeyError while parsing {secret}"))

    with caplog.at_level(logging.INFO, logger=main.logger.name):
        result = await invoke(name, **TOOL_ARGS[name])

    assert result == {
        "error": {"type": "internal", "message": main.INTERNAL_ERROR_MESSAGE, "retryable": False}
    }
    assert secret not in json.dumps(result)
    assert "RuntimeError" not in json.dumps(result)
    # Logged server-side with the traceback; the message itself names only the
    # tool and the exception type.
    [record] = [r for r in caplog.records if r.name == main.logger.name and r.levelno == logging.ERROR]
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError
    assert f"tool={name}" in record.getMessage()
    assert secret not in record.getMessage()


async def test_model_receives_the_error_object_as_json_text(monkeypatch):
    fake_ct(monkeypatch, "draft_referral_message", exc=ValueError(PHI_MESSAGE))

    # Without skip_parsing: exactly what the agent loop wraps into the
    # function_result content sent back to the model.
    contents = await main.TOOL_DISPATCH["draft_referral_message"].invoke(
        arguments=TOOL_ARGS["draft_referral_message"]
    )

    assert [json.loads(content.text) for content in contents] == [
        {"error": {"type": "phi_detected", "message": PHI_MESSAGE, "retryable": False}}
    ]


async def test_cancellation_is_not_swallowed(monkeypatch):
    fake_ct(monkeypatch, "search_studies", exc=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await invoke("search_studies", condition="PTSD")


async def test_logs_record_outcome_but_never_arguments_or_results(monkeypatch, caplog):
    argument_marker = "arg-marker-7f3a"
    result_marker = "result-marker-91c2"
    fake_ct(monkeypatch, "draft_referral_message", result={"messageBody": result_marker})
    fake_ct(monkeypatch, "generate_trial_summary", exc=ValueError(PHI_MESSAGE))
    fake_ct(monkeypatch, "check_eligibility", exc=FakeApiError("upstream down", status_code=502, retryable=True))
    fake_ct(monkeypatch, "get_study_details", exc=RuntimeError("boom"))

    with caplog.at_level(logging.DEBUG, logger=main.logger.name):
        await invoke("draft_referral_message", nct_id=NCT, patient_summary=argument_marker)
        await invoke("generate_trial_summary", nct_id=NCT, patient_context=argument_marker)
        await invoke("check_eligibility", nct_id=NCT)
        await invoke("get_study_details", nct_id=NCT)

    logged = "\n".join(r.getMessage() for r in caplog.records if r.name == main.logger.name)
    assert "tool=draft_referral_message outcome=ok" in logged
    assert "tool=generate_trial_summary outcome=phi_detected" in logged
    assert "tool=check_eligibility outcome=upstream_unavailable status_code=502" in logged
    assert "tool=get_study_details outcome=internal exception_type=RuntimeError" in logged
    for forbidden in (argument_marker, result_marker, NCT, PHI_MESSAGE, "upstream down"):
        assert forbidden not in logged


# --------------------------------------------------------------------------- #
# (e) + schema: what the framework sends the model is unchanged.
# --------------------------------------------------------------------------- #
STATUS_ENUM = [
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "COMPLETED",
    "ENROLLING_BY_INVITATION",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
]
PHASE_ENUM = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
SEX_ENUM = ["MALE", "FEMALE", "ALL"]

# tool name -> (parameter names in order, required parameters)
EXPECTED_PARAMETERS = {
    "search_studies": (
        ["term", "condition", "intervention", "location", "sponsor", "age", "sex", "status", "phase", "page_size"],
        [],
    ),
    "get_study_details": (["nct_id"], ["nct_id"]),
    "check_eligibility": (["nct_id"], ["nct_id"]),
    "count_studies": (["term", "condition", "intervention", "sponsor", "status"], []),
    "list_enum_values": (["enum_name"], ["enum_name"]),
    "generate_trial_summary": (["nct_id", "patient_context"], ["nct_id"]),
    "draft_referral_message": (
        ["nct_id", "patient_summary", "clinician_name", "clinician_facility"],
        ["nct_id", "patient_summary"],
    ),
}


def enum_values(prop: dict[str, Any]) -> list[str] | None:
    """Enum of a plain or nullable (``anyOf``) string property."""
    for option in prop.get("anyOf", [prop]):
        if "enum" in option:
            return option["enum"]
    return None


@pytest.mark.parametrize("name", list(EXPECTED_PARAMETERS))
def test_generated_schema_has_expected_parameters_and_required(name):
    agent_tool = main.TOOL_DISPATCH[name]
    schema = agent_tool.parameters()
    expected_names, expected_required = EXPECTED_PARAMETERS[name]

    assert schema["type"] == "object"
    assert list(schema["properties"]) == expected_names
    assert sorted(schema.get("required", [])) == sorted(expected_required)
    assert all(prop.get("description") for prop in schema["properties"].values())
    assert agent_tool.description and agent_tool.description == agent_tool.func.__wrapped__.__doc__


def test_search_studies_schema_enums_defaults_and_page_size_bounds():
    props = main.TOOL_DISPATCH["search_studies"].parameters()["properties"]

    assert enum_values(props["status"]) == STATUS_ENUM
    assert props["status"]["default"] == "RECRUITING"
    assert enum_values(props["phase"]) == PHASE_ENUM
    assert enum_values(props["sex"]) == SEX_ENUM
    page_size = props["page_size"]
    assert (page_size["type"], page_size["minimum"], page_size["maximum"], page_size["default"]) == (
        "integer",
        1,
        20,
        10,
    )


def test_count_studies_schema_status_enum():
    props = main.TOOL_DISPATCH["count_studies"].parameters()["properties"]

    assert enum_values(props["status"]) == STATUS_ENUM
    assert props["status"]["default"] is None


# --------------------------------------------------------------------------- #
# dispatch_tool: one code path with the agent's tools.
# --------------------------------------------------------------------------- #
def test_dispatch_table_holds_the_agent_tool_objects():
    assert list(main.TOOL_DISPATCH) == list(TOOL_ARGS)
    assert all(main.TOOL_DISPATCH[agent_tool.name] is agent_tool for agent_tool in main.AGENT_TOOLS)


async def test_dispatch_unknown_tool_returns_error():
    assert await main.dispatch_tool("not_a_real_tool", {}) == {
        "error": {"type": "invalid_input", "message": "Unknown tool: not_a_real_tool", "retryable": False}
    }


async def test_dispatch_returns_tool_results_and_shared_error_mapping(monkeypatch):
    fake_ct(monkeypatch, "count_studies", result=7)
    fake_ct(monkeypatch, "get_study_details", exc=FakeApiError("Study not found.", status_code=404))

    assert await main.dispatch_tool("count_studies", {"condition": "PTSD"}) == {"count": 7}
    assert await main.dispatch_tool("get_study_details", {"nct_id": NCT}) == {
        "error": {"type": "not_found", "message": "Study not found.", "retryable": False}
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        pytest.param("get_study_details", {}, id="missing-required"),
        pytest.param("search_studies", {"status": "OPEN-ISH"}, id="not-in-enum"),
        pytest.param("search_studies", {"page_size": 50}, id="page-size-above-20"),
        pytest.param("search_studies", {"page_size": 0}, id="page-size-below-1"),
    ],
)
async def test_dispatch_rejects_schema_invalid_arguments_before_any_upstream_call(monkeypatch, name, arguments):
    fake = fake_ct(monkeypatch, name, result={})

    assert await main.dispatch_tool(name, arguments) == {
        "error": {
            "type": "invalid_input",
            "message": f"The arguments for {name} do not match its parameter schema.",
            "retryable": False,
        }
    }
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# (d) Lifecycle: the shared HTTP client is closed after the Responses drain.
# --------------------------------------------------------------------------- #
@pytest.fixture
def shutdown_order(monkeypatch):
    """Record the Responses layer's drain and ct.aclose_client, in call order.

    Spies on the SDK's private endpoint handler on purpose: if an SDK upgrade
    moves the drain out of the single shutdown_handler slot that main.py
    chains onto, these tests fail instead of the drain silently being lost.
    """
    from azure.ai.agentserver.responses.hosting._endpoint_handler import (
        _ResponseEndpointHandler,
    )

    order: list[str] = []
    real_drain = _ResponseEndpointHandler.handle_shutdown

    async def recording_drain(self):
        order.append("responses_drain")
        await real_drain(self)

    async def fake_aclose_client():
        order.append("aclose_client")

    monkeypatch.setattr(_ResponseEndpointHandler, "handle_shutdown", recording_drain)
    monkeypatch.setattr(main.ct, "aclose_client", fake_aclose_client, raising=False)
    return order


async def test_shutdown_runs_responses_drain_then_closes_http_client(shutdown_order, local_host_env):
    server = build_local_server()

    assert await run_lifespan(server) == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert shutdown_order == ["responses_drain", "aclose_client"]


async def test_http_client_is_closed_even_if_responses_drain_fails(monkeypatch, shutdown_order, local_host_env):
    from azure.ai.agentserver.responses.hosting._endpoint_handler import (
        _ResponseEndpointHandler,
    )

    async def failing_drain(self):
        shutdown_order.append("responses_drain")
        raise RuntimeError("drain failed")

    monkeypatch.setattr(_ResponseEndpointHandler, "handle_shutdown", failing_drain)
    server = build_local_server()

    assert await run_lifespan(server) == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert shutdown_order == ["responses_drain", "aclose_client"]


async def test_failure_to_close_http_client_does_not_break_shutdown(monkeypatch, caplog, local_host_env):
    async def failing_aclose_client():
        raise RuntimeError("close failed")

    monkeypatch.setattr(main.ct, "aclose_client", failing_aclose_client, raising=False)
    server = build_local_server()

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        assert await run_lifespan(server) == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert any("Failed to close" in r.getMessage() for r in caplog.records if r.name == main.logger.name)


# --------------------------------------------------------------------------- #
# Import safety: no Azure credentials or network needed to import main.
# --------------------------------------------------------------------------- #
def test_importing_main_needs_no_azure_credentials_or_network():
    code = textwrap.dedent(
        """
        import socket

        def _blocked(*args, **kwargs):
            raise OSError("network access attempted while importing main")

        socket.socket.connect = _blocked
        socket.socket.connect_ex = _blocked
        socket.create_connection = _blocked
        socket.getaddrinfo = _blocked

        import main

        assert len(main.AGENT_TOOLS) == 7
        """
    )
    credential_prefixes = ("AZURE_", "FOUNDRY_", "APIM_", "OPENAI_", "AGENT_CHAT_BACKEND")
    env = {key: value for key, value in os.environ.items() if not key.startswith(credential_prefixes)}

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(main.__file__).resolve().parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
