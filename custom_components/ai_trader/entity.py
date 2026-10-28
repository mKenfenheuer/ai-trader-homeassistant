"""Devices: one per portfolio, plus one for the market."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AiTraderCoordinator

BROKERS = {
    "Paper": "Papierdepot",
    "Trading212Demo": "Trading 212 Demo",
    "Trading212Live": "Trading 212",
}


def portfolio_device(coordinator: AiTraderCoordinator, portfolio: dict[str, Any]) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_portfolio_{portfolio['id']}")},
        name=portfolio["name"],
        manufacturer=MANUFACTURER,
        model=BROKERS.get(portfolio["broker"], portfolio["broker"]),
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"{coordinator.client.url}/Portfolios/Details/{portfolio['id']}",
        via_device=(DOMAIN, f"{entry.entry_id}_market"),
    )


def market_device(coordinator: AiTraderCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_market")},
        name="AI Trader",
        manufacturer=MANUFACTURER,
        model="Markt und Marktrisiko",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=coordinator.client.url,
    )


class PortfolioEntity(CoordinatorEntity[AiTraderCoordinator]):
    """An entity of one portfolio; unavailable once the portfolio is gone from the app."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AiTraderCoordinator, portfolio: dict[str, Any], key: str) -> None:
        super().__init__(coordinator)
        self.portfolio_id: int = portfolio["id"]
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{portfolio['id']}_{key}"
        self._attr_device_info = portfolio_device(coordinator, portfolio)

    @property
    def portfolio(self) -> dict[str, Any] | None:
        return self.coordinator.portfolio(self.portfolio_id)

    @property
    def available(self) -> bool:
        return super().available and self.portfolio is not None


class MarketEntity(CoordinatorEntity[AiTraderCoordinator]):
    """An entity of the market device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AiTraderCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_market_{key}"
        self._attr_device_info = market_device(coordinator)
