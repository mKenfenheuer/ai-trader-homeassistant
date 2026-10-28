"""Devices, entities, events, services, calendars and the history import."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ai_trader.const import DOMAIN, EVENT_RUN_FINISHED, EVENT_SIGNAL_CLOSED, EVENT_SIGNAL_PENDING

from .conftest import API, mock_app, setup_entry

NOW = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(NOW)
    return freezer


def entity(hass: HomeAssistant, unique: str) -> str:
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN))
    entity_id = er.async_get(hass).async_get_entity_id(_platform(unique), DOMAIN, f"{entry.entry_id}_{unique}")
    assert entity_id, unique
    return entity_id


def _platform(unique: str) -> str:
    if unique.endswith("calendar"):
        return "calendar"
    if unique.split("_", 1)[1] in ("run_in_progress", "pending", "broker_problem", "open"):
        return "binary_sensor"
    if "_run_" in unique:
        return "button"
    return "sensor"


async def test_one_device_per_portfolio_with_its_values(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker
) -> None:
    await setup_entry(hass, entry)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert sorted(d.name for d in devices) == ["AI Trader", "Echtgeld", "Wachstum"]
    growth = next(d for d in devices if d.name == "Wachstum")
    assert growth.model == "Papierdepot"
    assert growth.configuration_url == "https://trader.example.com/Portfolios/Details/1"

    value = hass.states.get(entity(hass, "1_total_value"))
    assert (value.state, value.attributes["unit_of_measurement"], value.attributes["state_class"]) == ("10100.0", "EUR", "measurement")
    assert value.entity_id.startswith("sensor.wachstum")
    assert hass.states.get(entity(hass, "1_day_change_percent")).state == "1.0"
    assert hass.states.get(entity(hass, "1_pending_signals")).attributes["signals"][0]["id"] == 41
    assert hass.states.get(entity(hass, "1_last_run")).state == "2026-10-19T12:00:00+00:00"
    assert hass.states.get(entity(hass, "1_forecast_high_80")).state == "10220.0"

    apple = hass.states.get(entity(hass, "1_position_US0378331005"))
    assert apple.state == "1100.0"
    assert apple.attributes["quantity"] == 10
    assert apple.attributes["earnings_date"] == "2026-10-22"
    assert apple.attributes["model_expected_excess_20d"] == 1.2

    # Broker diagnostics only for the Trading 212 portfolio.
    assert hass.states.get(entity(hass, "2_broker_problem")).state == "on"
    assert er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_1_broker_problem") is None

    assert hass.states.get(entity(hass, "market_open")).state == "on"
    assert hass.states.get(entity(hass, "market_risk_level")).state == "normal"
    assert hass.states.get(entity(hass, "market_drawdown_probability")).state == "21.0"


async def test_sold_position_turns_unavailable_and_new_ones_appear(
    hass: HomeAssistant,
    recorder_mock,
    entry: MockConfigEntry,
    app: AiohttpClientMocker,
    summary: dict[str, Any],
    frozen: FrozenDateTimeFactory,
) -> None:
    await setup_entry(hass, entry)
    apple = entity(hass, "1_position_US0378331005")

    p = summary["portfolios"][0]
    msft = {**p["positions"][0], "isin": "US5949181045", "name": "Microsoft", "symbol": "MSFT", "forecast": None}
    p["positions"] = [msft]
    mock_app(app, summary)
    await _poll(hass, frozen)

    assert hass.states.get(apple).state == "unavailable"
    assert hass.states.get(entity(hass, "1_position_US5949181045")).state == "1100.0"


async def test_new_proposals_and_finished_runs_fire_events(
    hass: HomeAssistant,
    recorder_mock,
    entry: MockConfigEntry,
    app: AiohttpClientMocker,
    summary: dict[str, Any],
    frozen: FrozenDateTimeFactory,
) -> None:
    pending = async_capture_events(hass, EVENT_SIGNAL_PENDING)
    closed = async_capture_events(hass, EVENT_SIGNAL_CLOSED)
    runs = async_capture_events(hass, EVENT_RUN_FINISHED)
    await setup_entry(hass, entry)
    # The first poll only records what is open.
    assert (pending, runs) == ([], [])

    p = summary["portfolios"][0]
    p["pending_signals"].append({**p["pending_signals"][0], "id": 42, "side": "sell", "quantity": 1.5, "amount": 165.0})
    p["last_run"] = {**p["last_run"], "id": 301, "status": "failed", "error": "Timeout"}
    mock_app(app, summary)
    await _poll(hass, frozen)

    assert [e.data["signal_id"] for e in pending] == [42]
    assert pending[0].data["title"] == "Wachstum: Verkauf 1,5 × Apple"
    assert pending[0].data["message"].startswith("165,00 EUR zu 110,00.")
    assert [(e.data["run_id"], e.data["status"], e.data["error"]) for e in runs] == [(301, "failed", "Timeout")]

    p["pending_signals"] = []
    mock_app(app, summary)
    await _poll(hass, frozen)
    assert sorted(e.data["signal_id"] for e in closed) == [41, 42]
    assert len(runs) == 1


async def test_announced_proposals_survive_a_restart(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker
) -> None:
    pending = async_capture_events(hass, EVENT_SIGNAL_PENDING)
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert pending == []


async def test_approve_and_reject_call_the_app(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker
) -> None:
    await setup_entry(hass, entry)
    app.post(f"{API}/signals/41/approve", json={"id": 41, "status": "scheduled", "message": None})
    app.post(f"{API}/signals/43/reject", status=409, json={"error": "Der Vorschlag ist nicht mehr offen."})

    result = await hass.services.async_call(DOMAIN, "approve_signal", {"signal_id": 41}, blocking=True, return_response=True)
    assert result == {"id": 41, "status": "scheduled", "message": None}

    with pytest.raises(ServiceValidationError, match="nicht mehr offen"):
        await hass.services.async_call(DOMAIN, "reject_signal", {"signal_id": 43}, blocking=True)


async def test_run_button_and_service_start_runs(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker
) -> None:
    await setup_entry(hass, entry)
    app.post(f"{API}/portfolios/1/run", status=202, json={"status": "started", "engine": "rules"})

    await hass.services.async_call("button", "press", {"entity_id": entity(hass, "1_run_rules")}, blocking=True)
    await hass.services.async_call(DOMAIN, "run", {"portfolio_id": 1, "engine": "agent", "instruction": "Nur prüfen"}, blocking=True)

    bodies = [call[2] for call in app.mock_calls if str(call[1]).endswith("/run")]
    assert bodies == [{"engine": "rules", "instruction": None}, {"engine": "agent", "instruction": "Nur prüfen"}]


async def test_calendars_show_portfolio_and_market_events(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    await setup_entry(hass, entry)
    cal = entity(hass, "1_calendar")

    # The state shows the next entry that has not ended: the quarterly report on Thursday.
    state = hass.states.get(cal)
    assert (state.state, state.attributes["message"], state.attributes["all_day"]) == ("off", "Quartalszahlen Apple", True)

    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": cal, "start_date_time": "2026-10-12T00:00:00Z", "end_date_time": "2026-10-26T00:00:00Z"},
        blocking=True,
        return_response=True,
    )
    events = result[cal]["events"]
    assert [e["summary"] for e in events] == ["Kauf 10 × Apple", "Agentenlauf", "Quartalszahlen Apple"]
    assert events[0]["end"] == "2026-10-18T12:15:00+02:00"  # a trade shows as a 15-minute entry
    assert (events[2]["start"], events[2]["end"]) == ("2026-10-22", "2026-10-23")

    market = entity(hass, "market_calendar")
    assert hass.states.get(market).attributes["message"] == "Börse geschlossen"


async def test_history_fills_the_statistics_once(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker, summary: dict[str, Any]
) -> None:
    history = [
        {
            "time": (NOW - timedelta(days=d)).replace(hour=20).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_value": 10_000 + d,
            "cash": 9000,
            "invested": 1000 + d,
            "net_deposits": 10_000,
        }
        for d in (3, 2, 1)
    ]
    mock_app(app, summary, history)
    await hass.config.async_set_time_zone("Europe/Berlin")
    await setup_entry(hass, entry)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    value = entity(hass, "1_total_value")
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period, hass, NOW - timedelta(days=10), None, {value}, "hour", None, {"mean"}
    )
    assert [s["mean"] for s in stats[value]] == [10_003, 10_002, 10_001]

    profit = entity(hass, "1_total_profit")
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period, hass, NOW - timedelta(days=10), None, {profit}, "hour", None, {"mean"}
    )
    assert [s["mean"] for s in stats[profit]] == [3, 2, 1]

    # A restart imports nothing again; the service does on request.
    requests = sum(1 for c in app.mock_calls if "/history" in str(c[1]))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert sum(1 for c in app.mock_calls if "/history" in str(c[1])) == requests

    result = await hass.services.async_call(DOMAIN, "import_history", {"portfolio_id": 1}, blocking=True, return_response=True)
    assert result["imported_days"][value] == 3


async def test_unreachable_app_marks_entities_unavailable(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker, frozen: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass, entry)
    app.clear_requests()
    app._mocks.clear()
    app.get(f"{API}/summary", status=503)
    await _poll(hass, frozen)
    assert hass.states.get(entity(hass, "1_total_value")).state == "unavailable"


async def _poll(hass: HomeAssistant, frozen: FrozenDateTimeFactory) -> None:
    frozen.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
