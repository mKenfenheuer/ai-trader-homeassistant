"""Buttons: start a run of the agent or the rule engine for a portfolio."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AiTraderError
from .coordinator import AiTraderConfigEntry, AiTraderCoordinator
from .entity import PortfolioEntity

PARALLEL_UPDATES = 1

ENGINES = ("agent", "rules")


async def async_setup_entry(hass: HomeAssistant, entry: AiTraderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new() -> None:
        new = [RunButton(coordinator, p, engine) for pid, p in coordinator.portfolios.items() if pid not in known for engine in ENGINES]
        known.update(coordinator.portfolios)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class RunButton(PortfolioEntity, ButtonEntity):
    def __init__(self, coordinator: AiTraderCoordinator, portfolio: dict[str, Any], engine: str) -> None:
        super().__init__(coordinator, portfolio, f"run_{engine}")
        self.engine = engine
        self._attr_translation_key = f"run_{engine}"
        self._attr_icon = "mdi:robot" if engine == "agent" else "mdi:cog-play"

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.run(self.portfolio_id, self.engine)
        except AiTraderError as err:
            raise HomeAssistantError(f"Lauf nicht gestartet: {err}") from err
        await self.coordinator.async_request_refresh()
