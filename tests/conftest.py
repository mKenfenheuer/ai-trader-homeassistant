"""Fixtures: a config entry against a fake app answering from tests/fixtures."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ai_trader.const import CONF_VERIFY_SSL, DOMAIN

URL = "https://trader.example.com"
API = f"{URL}/api/v1"
TOKEN = "test-token-0123456789"

SUMMARY: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures" / "summary.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock: Any, enable_custom_integrations: None) -> None:
    """Loads custom_components/ai_trader in every test, with a recorder: the integration depends on it.

    The recorder has to start before hass, so it comes first here.
    """


@pytest.fixture
def summary() -> dict[str, Any]:
    return copy.deepcopy(SUMMARY)


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="trader.example.com", unique_id=URL, data={CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    )


@pytest.fixture
def app(aioclient_mock: AiohttpClientMocker, summary: dict[str, Any]) -> AiohttpClientMocker:
    """The app's endpoints with the fixture data; tests replace single answers with mock_app."""
    mock_app(aioclient_mock, summary)
    return aioclient_mock


def mock_app(aioclient_mock: AiohttpClientMocker, summary: dict[str, Any], history: list[dict[str, Any]] | None = None) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock._mocks.clear()
    aioclient_mock.get(f"{API}/summary", json=summary)
    for p in summary["portfolios"]:
        aioclient_mock.get(f"{API}/portfolios/{p['id']}/history", json=history or [])
        aioclient_mock.get(f"{API}/portfolios/{p['id']}/events", json=EVENTS if p["id"] == 1 else [])
    aioclient_mock.get(f"{API}/market/events", json=MARKET_EVENTS)


EVENTS = [
    {
        "uid": "tx-5",
        "kind": "buy",
        "summary": "Kauf 10 × Apple",
        "description": "10 Stück zu 100,00 EUR",
        "start": "2026-10-18T10:00:00Z",
        "end": None,
        "all_day": False,
    },
    {
        "uid": "run-300",
        "kind": "run",
        "summary": "Agentenlauf",
        "description": "Ein Nachkauf vorgeschlagen.",
        "start": "2026-10-19T12:00:00Z",
        "end": "2026-10-19T12:03:00Z",
        "all_day": False,
    },
    {
        "uid": "earnings-7-20261022",
        "kind": "earnings",
        "summary": "Quartalszahlen Apple",
        "description": None,
        "start": "2026-10-22T00:00:00Z",
        "end": "2026-10-23T00:00:00Z",
        "all_day": True,
    },
]

MARKET_EVENTS = [
    {
        "uid": "closed-20261224",
        "kind": "market_closed",
        "summary": "Börse geschlossen",
        "description": None,
        "start": "2026-12-24T00:00:00Z",
        "end": "2026-12-25T00:00:00Z",
        "all_day": True,
    },
]


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
