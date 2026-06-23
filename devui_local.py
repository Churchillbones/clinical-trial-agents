"""Local-only DevUI launcher — a browser chat UI for the Clinical Trials agent.

This is a LOCAL development tool and is **not** part of the Foundry hosted
deployment. The hosted agent is served by ``main.py`` (``ResponsesHostServer``);
this file, ``requirements-dev.txt``, and the ``.venv-devui`` environment are
excluded from the deploy package (``.dockerignore`` / ``.azdignore``) and are
never imported by ``main.py``.

It reuses the exact same agent as the deployment (``build_agent`` from
``main.py``) — same seven tools, same system prompt, same model backend chosen
by ``AGENT_CHAT_BACKEND`` (``apim`` for local VA-network testing).

DevUI pulls ``agent-framework-core>=1.9``, which conflicts with the pinned
``agent-dev-cli`` (<1.3.0) in ``requirements.txt``; keep it in its own venv::

    py -m venv .venv-devui
    .\\.venv-devui\\Scripts\\python.exe -m pip install -r requirements-dev.txt
    .\\.venv-devui\\Scripts\\python.exe devui_local.py

The browser opens automatically at http://localhost:8090.
"""

from __future__ import annotations

import os

from agent_framework.devui import serve

from main import build_agent


def main() -> None:
    # Local UI defaults to the VA-network-reachable APIM Responses backend.
    os.environ.setdefault("AGENT_CHAT_BACKEND", "apim")

    agent = build_agent()
    # DevUI lists entities by name; the deployed agent is built without one.
    try:
        agent.name = os.environ.get("FOUNDRY_AGENT_NAME", "clinical-trials-search")
    except Exception:
        pass

    port = int(os.environ.get("DEVUI_PORT", "8090"))
    serve(
        entities=[agent],
        host="127.0.0.1",
        port=port,
        auto_open=True,
        ui_enabled=True,
        auth_enabled=False,  # localhost dev only — no token prompt
    )


if __name__ == "__main__":
    main()
