"""Clinical Trials Search agent — entry point.

Runs the Agent Framework / Responses API function-call loop over the seven
ClinicalTrials.gov tools. Exposes ``POST /responses`` on :8088 for local
testing (F5) and serves as the hosted-agent entry point for Foundry.
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

import clinical_trials_tools as ct
from tool_definitions import all_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clinical-trials-agent")

# Map tool name -> implementation. Mirrors tool_definitions.all_tools.
TOOL_DISPATCH = {
    "search_studies": ct.search_studies,
    "get_study_details": ct.get_study_details,
    "check_eligibility": ct.check_eligibility,
    "count_studies": ct.count_studies,
    "list_enum_values": ct.list_enum_values,
    "generate_trial_summary": ct.generate_trial_summary,
    "draft_referral_message": ct.draft_referral_message,
}

MAX_TOOL_TURNS = 8
DEFAULT_MODEL = "gpt-5.4-nano"

_openai_client = None


def _load_system_prompt() -> str:
    """Read the agent system prompt from disk (lazy — not at import time)."""
    return (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8")


def dispatch_tool(name: str, arguments: dict) -> dict:
    """Execute a tool by name with keyword arguments.

    Returns a JSON-serialisable dict. Unknown tools and tool errors are
    returned as ``{"error": ...}`` so the model can recover gracefully.
    """
    func = TOOL_DISPATCH.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = func(**arguments)
    except Exception as exc:  # surface tool failures to the model, don't crash
        logger.exception("Tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict):
        return result
    return {"result": result}


def _get_openai_client():
    """Lazily create the Foundry-backed OpenAI client (needs Entra auth)."""
    global _openai_client
    if _openai_client is None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not set")
        project = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        _openai_client = project.get_openai_client()
    return _openai_client


def run_agent(user_input: str, conversation_id: str | None = None) -> str:
    """Run the model with the tool loop and return the final text answer."""
    from openai.types.responses.response_input_param import FunctionCallOutput

    load_dotenv()
    model_name = os.environ.get("FOUNDRY_MODEL_NAME", DEFAULT_MODEL)
    system_prompt = _load_system_prompt()
    client = _get_openai_client()
    tools = [t.as_dict() for t in all_tools]
    input_list: list = [{"role": "user", "content": user_input}]

    for _ in range(MAX_TOOL_TURNS):
        kwargs: dict = {
            "model": model_name,
            "instructions": system_prompt,
            "input": input_list,
            "tools": tools,
        }
        if conversation_id:
            kwargs["conversation"] = conversation_id
        response = client.responses.create(**kwargs)

        function_calls = [
            item for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]
        if not function_calls:
            return response.output_text

        input_list.extend(response.output)
        for fc in function_calls:
            args = json.loads(fc.arguments) if fc.arguments else {}
            result = dispatch_tool(fc.name, args)
            input_list.append(
                FunctionCallOutput(
                    type="function_call_output",
                    call_id=fc.call_id,
                    output=json.dumps(result, default=str),
                )
            )

    return "I was unable to complete the request within the tool-call limit."


class ResponsesHandler(BaseHTTPRequestHandler):
    """Minimal HTTP surface for local testing: POST /responses."""

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path != "/responses":
            self._send_json(404, {"error": "Not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        user_input = payload.get("input")
        if not user_input:
            self._send_json(400, {"error": "Missing 'input'"})
            return

        try:
            answer = run_agent(user_input, payload.get("conversation"))
        except Exception as exc:  # pragma: no cover - network/auth failures
            logger.exception("Agent run failed")
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        self._send_json(200, {"output_text": answer})

    def log_message(self, fmt: str, *args) -> None:  # quieter logging
        logger.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    load_dotenv()
    host = os.environ.get("AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_PORT", "8088"))
    server = ThreadingHTTPServer((host, port), ResponsesHandler)
    logger.info("Clinical Trials agent listening on http://%s:%d/responses", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
