"""Calendars: per portfolio its trades, runs, planned orders and quarterly reports; for the market its holidays."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import AiTraderError
from .const import EVENT_CACHE_TTL
from .coordinator import AiTraderConfigEntry, AiTraderCoordinator
from .entity import MarketEntity, PortfolioEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# A trade is a moment; the calendar shows it as a short entry.
MOMENT = timedelta(minutes=15)
UPCOMING = timedelta(days=30)


async def async_setup_entry(hass: HomeAssistant, entry: AiTraderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[int] = set()

    @callback
    def add_new() -> None:
        new = [PortfolioCalendar(coordinator, p) for pid, p in coordinator.portfolios.items() if pid not in known]
        known.update(coordinator.portfolios)
        if new:
            async_add_entities(new)

    async_add_entities([MarketCalendar(coordinator)])
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def to_event(e: dict[str, Any]) -> CalendarEvent | None:
    start = dt_util.parse_datetime(e["start"])
    if start is None:
        return None
    end = dt_util.parse_datetime(e["end"]) if e.get("end") else None
    if e.get("all_day"):
        first = start.date()
        last = end.date() if end else first + timedelta(days=1)
        return CalendarEvent(
            start=first, end=max(last, first + timedelta(days=1)), summary=e["summary"], description=e.get("description"), uid=e["uid"]
        )
    start = dt_util.as_local(start)
    end = dt_util.as_local(end) if end else start + MOMENT
    return CalendarEvent(
        start=start, end=max(end, start + timedelta(minutes=1)), summary=e["summary"], description=e.get("description"), uid=e["uid"]
    )


class _CachedCalendar(CalendarEntity):
    """Keeps the next 30 days for the entity state, refreshed at most every few minutes."""

    _attr_has_entity_name = True
    coordinator: AiTraderCoordinator

    def __init__(self) -> None:
        self._upcoming: list[CalendarEvent] = []
        self._fetched: datetime | None = None
        self._fetching = False

    async def _fetch(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for e in self._upcoming:
            if e.end_datetime_local > now:
                return e
        return None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        try:
            raw = await self._fetch(start_date, end_date)
        except AiTraderError as err:
            _LOGGER.warning("Calendar %s not available: %s", self.entity_id, err)
            return []
        return [e for e in map(to_event, raw) if e is not None]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self._fetching and (self._fetched is None or dt_util.utcnow() - self._fetched > EVENT_CACHE_TTL):
            self.hass.async_create_task(self._refresh())
        super()._handle_coordinator_update()

    async def _refresh(self) -> None:
        self._fetching = True
        try:
            now = dt_util.utcnow()
            self._upcoming = await self.async_get_events(self.hass, now - timedelta(days=1), now + UPCOMING)
            self._fetched = now
        finally:
            self._fetching = False
        self.async_write_ha_state()


class PortfolioCalendar(PortfolioEntity, _CachedCalendar):
    _attr_translation_key = "portfolio_calendar"

    def __init__(self, coordinator: AiTraderCoordinator, portfolio: dict[str, Any]) -> None:
        PortfolioEntity.__init__(self, coordinator, portfolio, "calendar")
        _CachedCalendar.__init__(self)

    async def _fetch(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return await self.coordinator.client.events(self.portfolio_id, start, end)


class MarketCalendar(MarketEntity, _CachedCalendar):
    _attr_translation_key = "market_calendar"

    def __init__(self, coordinator: AiTraderCoordinator) -> None:
        MarketEntity.__init__(self, coordinator, "calendar")
        _CachedCalendar.__init__(self)

    async def _fetch(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return await self.coordinator.client.market_events(start, end)
