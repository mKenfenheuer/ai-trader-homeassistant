"""Setup and reauthentication."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ai_trader.const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL, DOMAIN

from .conftest import API, TOKEN, URL, setup_entry


async def test_user_flow_creates_entry(hass: HomeAssistant, recorder_mock, app: AiohttpClientMocker) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "trader.example.com/", CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == URL  # https added, slash removed
    assert result["title"] == "trader.example.com"
    sent = next(r for r in app.mock_calls if str(r[1]).endswith("/summary"))
    assert sent[3]["Authorization"] == f"Bearer {TOKEN}"


async def test_user_flow_reports_a_wrong_token(hass: HomeAssistant, recorder_mock, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{API}/summary", status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: "wrong", CONF_VERIFY_SSL: True})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_reports_an_unreachable_app(hass: HomeAssistant, recorder_mock, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{API}/summary", status=502)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True})
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://x", CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/summary", status=401)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert [f["step_id"] for f in flows] == ["reauth_confirm"]


async def test_reauth_stores_the_new_token(hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker) -> None:
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "new-token"


async def test_options_set_the_interval(hass: HomeAssistant, recorder_mock, entry: MockConfigEntry, app: AiohttpClientMocker) -> None:
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 30})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 30
