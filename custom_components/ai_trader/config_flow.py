"""Setup: address of the app and the API token from its settings page."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from yarl import URL

from .api import AiTraderAuthError, AiTraderClient, AiTraderError
from .const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL, DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class AiTraderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize(user_input[CONF_URL])
            if url is None:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                errors = await self._test(url, user_input[CONF_TOKEN], user_input[CONF_VERIFY_SSL])
                if not errors:
                    return self.async_create_entry(title=URL(url).host or "AI Trader", data={**user_input, CONF_URL: url})

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "")): str,
                vol.Required(CONF_TOKEN): PASSWORD,
                vol.Required(CONF_VERIFY_SSL, default=(user_input or {}).get(CONF_VERIFY_SSL, True)): bool,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors, description_placeholders={"example": "https://trader.example.com"}
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._test(entry.data[CONF_URL], user_input[CONF_TOKEN], entry.data.get(CONF_VERIFY_SSL, True))
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): PASSWORD}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def _test(self, url: str, token: str, verify_ssl: bool) -> dict[str, str]:
        client = AiTraderClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token, verify_ssl)
        try:
            await client.summary()
        except AiTraderAuthError:
            return {"base": "invalid_auth"}
        except AiTraderError as err:
            _LOGGER.debug("AI Trader at %s not reachable: %s", url, err)
            return {"base": "cannot_connect"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AiTraderOptionsFlow()


class AiTraderOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
                }
            ),
        )


def _normalize(value: str) -> str | None:
    """Base address without trailing slash; http(s) only."""
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    url = URL(value)
    if url.scheme not in ("http", "https") or not url.host:
        return None
    return str(url).rstrip("/")
