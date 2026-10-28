"""AI Trader: portfolios, orders and the market of the AI Trader app in Home Assistant."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AiTraderClient, AiTraderError, AiTraderRequestError
from .const import (
    ATTR_CONFIG_ENTRY,
    ATTR_ENGINE,
    ATTR_INSTRUCTION,
    ATTR_PORTFOLIO_ID,
    ATTR_SIGNAL_ID,
    CONF_VERIFY_SSL,
    DOMAIN,
    SERVICE_APPROVE,
    SERVICE_IMPORT_HISTORY,
    SERVICE_REJECT,
    SERVICE_RUN,
)
from .coordinator import AiTraderConfigEntry, AiTraderCoordinator
from .history import async_import_history

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ENTRY = vol.Optional(ATTR_CONFIG_ENTRY)
SIGNAL_SCHEMA = vol.Schema({vol.Required(ATTR_SIGNAL_ID): vol.Coerce(int), ENTRY: cv.string})
RUN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PORTFOLIO_ID): vol.Coerce(int),
        vol.Optional(ATTR_ENGINE): vol.In(["agent", "rules"]),
        vol.Optional(ATTR_INSTRUCTION): cv.string,
        ENTRY: cv.string,
    }
)
IMPORT_SCHEMA = vol.Schema({vol.Required(ATTR_PORTFOLIO_ID): vol.Coerce(int), ENTRY: cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services live on the domain, so automations keep working while an entry reloads."""

    async def decide(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        signal_id = call.data[ATTR_SIGNAL_ID]
        try:
            if call.service == SERVICE_APPROVE:
                result = await coordinator.client.approve(signal_id)
            else:
                result = await coordinator.client.reject(signal_id)
        except AiTraderRequestError as err:
            raise ServiceValidationError(f"Vorschlag {signal_id}: {err}") from err
        except AiTraderError as err:
            raise HomeAssistantError(f"AI Trader nicht erreichbar: {err}") from err
        await coordinator.async_request_refresh()
        return result

    async def run(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        try:
            result = await coordinator.client.run(call.data[ATTR_PORTFOLIO_ID], call.data.get(ATTR_ENGINE), call.data.get(ATTR_INSTRUCTION))
        except AiTraderRequestError as err:
            raise ServiceValidationError(f"Lauf nicht gestartet: {err}") from err
        except AiTraderError as err:
            raise HomeAssistantError(f"AI Trader nicht erreichbar: {err}") from err
        await coordinator.async_request_refresh()
        return result

    async def import_history(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        portfolio_id = call.data[ATTR_PORTFOLIO_ID]
        sensors = [s for (pid, _), s in coordinator.history_sensors.items() if pid == portfolio_id]
        if not sensors:
            raise ServiceValidationError(f"Kein Depot mit der Nummer {portfolio_id}.")
        imported = {s.entity_id: await async_import_history(hass, coordinator, portfolio_id, s, force=True) for s in sensors}
        return {"imported_days": imported}

    for service in (SERVICE_APPROVE, SERVICE_REJECT):
        hass.services.async_register(DOMAIN, service, decide, schema=SIGNAL_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_RUN, run, schema=RUN_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_HISTORY, import_history, schema=IMPORT_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AiTraderConfigEntry) -> bool:
    client = AiTraderClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    coordinator = AiTraderCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AiTraderConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: AiTraderConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> AiTraderCoordinator:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY):
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError("Keine geladene AI-Trader-Integration gefunden.")
    if len(entries) > 1:
        raise ServiceValidationError("Mehrere AI-Trader-Instanzen: bitte config_entry_id angeben.")
    return entries[0].runtime_data
