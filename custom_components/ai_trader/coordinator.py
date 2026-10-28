"""Polls the AI Trader summary and turns changes into Home Assistant events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AiTraderAuthError, AiTraderClient, AiTraderError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_RUN_FINISHED,
    EVENT_SIGNAL_CLOSED,
    EVENT_SIGNAL_PENDING,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type AiTraderConfigEntry = ConfigEntry[AiTraderCoordinator]


class AiTraderCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """One summary request per interval for all portfolios of the app."""

    config_entry: AiTraderConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AiTraderConfigEntry, client: AiTraderClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.client = client
        # Which proposals and runs have been announced survives restarts: no second notification for the same
        # proposal, and none lost while Home Assistant was down.
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._state: dict[str, Any] | None = None
        # Several value sensors import from the same history at startup; one request per portfolio serves them all.
        self._history: dict[int, tuple[datetime, list[dict[str, Any]]]] = {}
        self._history_lock = asyncio.Lock()
        # Value sensors whose statistics can be filled from the history, by (portfolio id, key).
        self.history_sensors: dict[tuple[int, str], Any] = {}

    @property
    def portfolios(self) -> dict[int, dict[str, Any]]:
        return {p["id"]: p for p in (self.data or {}).get("portfolios", [])}

    def portfolio(self, portfolio_id: int) -> dict[str, Any] | None:
        return self.portfolios.get(portfolio_id)

    async def async_history(self, portfolio_id: int) -> list[dict[str, Any]]:
        async with self._history_lock:
            cached = self._history.get(portfolio_id)
            if cached and dt_util.utcnow() - cached[0] < timedelta(minutes=10):
                return cached[1]
            points = await self.client.history(portfolio_id)
            self._history[portfolio_id] = (dt_util.utcnow(), points)
            return points

    async def async_history_cutoff(self, key: str) -> datetime | None:
        """Where the first import of this sensor's history stopped, or None before it ran."""
        value = (await self._load()).get("history_imported", {}).get(key)
        return dt_util.parse_datetime(value) if value else None

    async def async_set_history_cutoff(self, key: str, cutoff: datetime) -> None:
        state = await self._load()
        state.setdefault("history_imported", {})[key] = cutoff.isoformat()
        await self._store.async_save(state)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.summary()
        except AiTraderAuthError as err:
            raise ConfigEntryAuthFailed("API token rejected") from err
        except AiTraderError as err:
            raise UpdateFailed(f"AI Trader not reachable: {err}") from err
        await self._announce(data)
        return data

    async def _load(self) -> dict[str, Any]:
        if self._state is None:
            self._state = await self._store.async_load() or {}
        return self._state

    async def _announce(self, data: dict[str, Any]) -> None:
        state = await self._load()
        first = "pending" not in state
        known_pending: set[int] = set(state.get("pending", []))
        runs: dict[str, dict[str, Any]] = dict(state.get("runs", {}))

        pending_now: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        for p in data.get("portfolios", []):
            for s in p.get("pending_signals", []):
                pending_now[s["id"]] = (p, s)

            run = p.get("last_run")
            if run:
                seen = runs.get(str(p["id"]))
                finished = run["status"] != "running"
                # A run counts as news once it is finished and differs from the last one announced.
                if finished and not first and seen and (seen.get("id") != run["id"] or seen.get("status") == "running"):
                    self.hass.bus.async_fire(EVENT_RUN_FINISHED, _run_event(p, run))
                runs[str(p["id"])] = {"id": run["id"], "status": run["status"]}

        # On the very first poll the open proposals are only recorded; announcing all of them at once after
        # installing the integration would be noise.
        if not first:
            for signal_id in pending_now.keys() - known_pending:
                p, s = pending_now[signal_id]
                self.hass.bus.async_fire(EVENT_SIGNAL_PENDING, _signal_event(self.client.url, p, s))
            for signal_id in known_pending - pending_now.keys():
                self.hass.bus.async_fire(EVENT_SIGNAL_CLOSED, {"signal_id": signal_id})

        new_state = {**state, "pending": sorted(pending_now), "runs": runs}
        if new_state != state or first:
            self._state = new_state
            await self._store.async_save(new_state)


def _signal_event(url: str, portfolio: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    side = "Kauf" if s["side"] == "buy" else "Verkauf"
    return {
        "signal_id": s["id"],
        "portfolio_id": portfolio["id"],
        "portfolio": portfolio["name"],
        "side": s["side"],
        "name": s["name"],
        "isin": s["isin"],
        "quantity": s["quantity"],
        "price": s["price"],
        "amount": s["amount"],
        "currency": portfolio["currency"],
        "reason": s["reason"],
        "expires_at": s.get("expires_at"),
        "title": f"{portfolio['name']}: {side} {_qty(s['quantity'])} × {s['name']}",
        "message": f"{_money(s['amount'])} {portfolio['currency']} zu {_money(s['price'])}. {s['reason']}".strip(),
        "url": f"{url}/Portfolios/Details/{portfolio['id']}",
    }


def _run_event(portfolio: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    return {
        "portfolio_id": portfolio["id"],
        "portfolio": portfolio["name"],
        "run_id": run["id"],
        "engine": run["engine"].lower(),
        "status": run["status"],
        "summary": run.get("summary"),
        "error": run.get("error"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


def _qty(q: float) -> str:
    return f"{q:.4f}".rstrip("0").rstrip(".").replace(".", ",")


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
