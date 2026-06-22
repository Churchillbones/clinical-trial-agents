"""Register the Clinical Trials Search agent version in Azure AI Foundry.

This bundles the system prompt + the seven ClinicalTrials.gov ``FunctionTool``
definitions into a ``PromptAgentDefinition`` and registers it as a new agent
version so it appears in the Foundry portal under Hosted Agents.

Run this *after* the hosted-agent code is deployed via the Foundry Toolkit
(``Foundry Toolkit: Deploy Hosted Agent`` -> Code -> Remote), or any time the
prompt/tool schema changes and you want to publish a new version.

Auth: Entra ID via ``DefaultAzureCredential``. Sign in first with ``az login``
and ensure your account has the **Azure AI User** role on the Foundry resource.
No keys are read or written by this script.

Usage::

    az login
    .\\.venv\\Scripts\\python.exe deploy_agent.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from tool_definitions import all_tools

DEFAULT_MODEL = "gpt-5.4-nano"
DEFAULT_AGENT_NAME = "clinical-trials-search"


def _read_system_prompt() -> str:
    """Read the agent instructions from ``system_prompt.txt``."""
    return (Path(__file__).parent / "system_prompt.txt").read_text(encoding="utf-8")


def deploy() -> object:
    """Create/register an agent version in the configured Foundry project.

    Returns the created agent object so callers (or a REPL) can inspect it.
    """
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from azure.identity import DefaultAzureCredential

    load_dotenv()
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise SystemExit(
            "FOUNDRY_PROJECT_ENDPOINT is not set. Copy .env.example to .env "
            "and fill it in, or export the variable before running."
        )
    model = os.environ.get("FOUNDRY_MODEL_NAME", DEFAULT_MODEL)
    agent_name = os.environ.get("FOUNDRY_AGENT_NAME", DEFAULT_AGENT_NAME)
    system_prompt = _read_system_prompt()

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=model,
            instructions=system_prompt,
            tools=all_tools,
        ),
    )

    version = getattr(agent, "version", None)
    label = f"'{agent_name}'" + (f" version {version}" if version else "")
    print(f"Registered agent {label}")
    print(f"  model:    {model}")
    print(f"  tools:    {len(all_tools)}")
    print(f"  endpoint: {endpoint}")
    return agent


if __name__ == "__main__":
    deploy()
