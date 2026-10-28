"""Binary sensors: market open, run in progress, broker sync problem."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AiTraderConfigEntry, AiTraderCoordinator
from .entity import MarketEntity, PortfolioEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PortfolioBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[dict[str, Any]], bool]
    attributes: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    broker_only: bool = False


PORTFOLIO_BINARY: tuple[PortfolioBinaryDescription, ...] = (
    PortfolioBinaryDescription(
        key="run_in_progress",
        translation_key="run_in_progress",
        device_class=BinarySensorDeviceClass.RUNNING,
        value=lambda p: p["run_in_progress"],
    ),
    PortfolioBinaryDescription(key="pending", translation_key="pending", icon="mdi:bell-alert", value=lambda p: bool(p["pending_signals"])),
    PortfolioBinaryDescription(
        key="broker_problem",
        translation_key="broker_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        broker_only=True,
        value=lambda p: bool(p.get("broker_sync_error")),
        attributes=lambda p: {"error": p.get("broker_sync_error")},
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: AiTraderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new() -> None:
        new: list[BinarySensorEntity] = []
        for pid, p in coordinator.portfolios.items():
            if pid in known:
                continue
            known.add(pid)
            new.extend(PortfolioBinarySensor(coordinator, p, d) for d in PORTFOLIO_BINARY if not d.broker_only or p["broker"] != "Paper")
        if new:
            async_add_entities(new)

    async_add_entities([MarketOpenSensor(coordinator)])
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PortfolioBinarySensor(PortfolioEntity, BinarySensorEntity):
    entity_description: PortfolioBinaryDescription

    def __init__(self, coordinator: AiTraderCoordinator, portfolio: dict[str, Any], description: PortfolioBinaryDescription) -> None:
        super().__init__(coordinator, portfolio, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        p = self.portfolio
        return None if p is None else self.entity_description.value(p)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        p = self.portfolio
        if p is None or self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(p)


class MarketOpenSensor(MarketEntity, BinarySensorEntity):
    _attr_translation_key = "market_open"
    _attr_icon = "mdi:bank"

    def __init__(self, coordinator: AiTraderCoordinator) -> None:
        super().__init__(coordinator, "open")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data["market"]["is_open"])
