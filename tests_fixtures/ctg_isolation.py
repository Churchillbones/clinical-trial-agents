"""Per-test isolation for clinical_trials_tools.

Import ``ctg_isolation`` into a test module to activate it there (there is no
conftest.py in this repository):

    from tests_fixtures.ctg_isolation import ctg_isolation  # noqa: F401

It patches out real backoff sleeps (the requested delays are recorded on
``ctg_isolation.sleeps``), starts from an empty enums cache, fails any HTTP
request that no respx route in the test matches (so nothing reaches the real
network), and closes the shared client at teardown.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest_asyncio
import respx

import clinical_trials_tools as tools


@pytest_asyncio.fixture(autouse=True)
async def ctg_isolation(monkeypatch):
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(tools, "_sleep", record_sleep)
    tools.reset_enums_cache()
    # A route-less router: a request matched by no test route raises instead of
    # going out to the network.
    with respx.mock(assert_all_called=False, assert_all_mocked=True):
        yield SimpleNamespace(sleeps=sleeps)
        await tools.aclose_client()
    tools.reset_enums_cache()
