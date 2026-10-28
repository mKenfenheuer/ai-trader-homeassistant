"""Sensors: values and orders of each portfolio, its positions, the 7-day corridor and the market."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import AiTraderConfigEntry, AiTraderCoordinator
from .entity import MarketEntity, PortfolioEntity
from .history import async_import_history

PARALLEL_UPDATES = 0

Portfolio = dict[str, Any]


def _ts(value: str | None):
    return dt_util.parse_datetime(value) if value else None


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 2)


@dataclass(frozen=True, kw_only=True)
class PortfolioSensorDescription(SensorEntityDescription):
    value: Callable[[Portfolio], Any]
    attributes: Callable[[Portfolio], dict[str, Any]] | None = None
    # Key of the app's daily value history this sensor's long-term statistics are filled from.
    history: Callable[[dict[str, Any]], float] | None = None
    currency: bool = False


def _signals(p: Portfolio, key: str) -> dict[str, Any]:
    return {
        "signals": [
            {
                "id": s["id"],
                "side": s["side"],
                "status": s["status"],
                "name": s["name"],
                "isin": s["isin"],
                "quantity": s["quantity"],
                "price": s["price"],
                "amount": s["amount"],
                "reason": s["reason"],
                "created_at": s["created_at"],
                "expires_at": s.get("expires_at"),
                "scheduled_for": s.get("scheduled_for"),
            }
            for s in p[key]
        ]
    }


def _positions(p: Portfolio) -> dict[str, Any]:
    return {
        "positions": [
            {
                "name": x["name"],
                "isin": x["isin"],
                "symbol": x.get("symbol"),
                "value": x["market_value"],
                "weight": x["weight_percent"],
                "profit_percent": x.get("unrealized_percent"),
                "day_change_percent": x.get("day_change_percent"),
            }
            for x in p["positions"]
        ]
    }


def _run(p: Portfolio) -> dict[str, Any]:
    run = p.get("last_run") or {}
    return {
        "run_id": run.get("id"),
        "engine": (run.get("engine") or "").lower() or None,
        "trigger": run.get("trigger"),
        "status": run.get("status"),
        "finished_at": run.get("finished_at"),
        "summary": run.get("summary"),
        "error": run.get("error"),
        "running": p["run_in_progress"],
        "schedule_enabled": p["automation"]["schedule_enabled"],
        "buy_mode": p["automation"]["buy_mode"],
        "sell_mode": p["automation"]["sell_mode"],
    }


def _forecast(p: Portfolio) -> dict[str, Any]:
    f = p.get("forecast")
    if not f:
        return {}
    return {
        "as_of": f["as_of"][:10],
        "target_date": f["target_date"][:10],
        "low_80": f["low_80"],
        "high_80": f["high_80"],
        "source": f["source"],
        "positions_covered": f["covered"],
        "positions": f["positions"],
    }


def _forecast_value(name: str) -> Callable[[Portfolio], Any]:
    return lambda p: (p.get("forecast") or {}).get(name)


MONEY = {"state_class": SensorStateClass.MEASUREMENT, "suggested_display_precision": 2, "currency": True}

PORTFOLIO_SENSORS: tuple[PortfolioSensorDescription, ...] = (
    PortfolioSensorDescription(
        key="total_value",
        translation_key="total_value",
        icon="mdi:briefcase-variant",
        value=lambda p: p["total_value"],
        history=lambda h: h["total_value"],
        **MONEY,
    ),
    PortfolioSensorDescription(
        key="cash", translation_key="cash", icon="mdi:cash", value=lambda p: p["cash"], history=lambda h: h["cash"], **MONEY
    ),
    PortfolioSensorDescription(
        key="invested",
        translation_key="invested",
        icon="mdi:chart-pie",
        value=lambda p: p["invested"],
        history=lambda h: h["invested"],
        **MONEY,
    ),
    PortfolioSensorDescription(
        key="net_deposits",
        translation_key="net_deposits",
        icon="mdi:bank-transfer-in",
        value=lambda p: p["net_deposits"],
        history=lambda h: h["net_deposits"],
        **MONEY,
    ),
    PortfolioSensorDescription(
        key="total_profit",
        translation_key="total_profit",
        icon="mdi:finance",
        value=lambda p: p["total_profit"],
        history=lambda h: round(h["total_value"] - h["net_deposits"], 2),
        **MONEY,
    ),
    PortfolioSensorDescription(
        key="total_return",
        translation_key="total_return",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda p: p.get("total_return_percent"),
        history=lambda h: round((h["total_value"] - h["net_deposits"]) / h["net_deposits"] * 100, 2) if h["net_deposits"] > 0 else None,
    ),
    PortfolioSensorDescription(
        key="day_change", translation_key="day_change", icon="mdi:swap-vertical", value=lambda p: p["day_change"], **MONEY
    ),
    PortfolioSensorDescription(
        key="day_change_percent",
        translation_key="day_change_percent",
        icon="mdi:swap-vertical",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value=lambda p: p.get("day_change_percent"),
    ),
    PortfolioSensorDescription(key="cost_basis", translation_key="cost_basis", icon="mdi:tag", value=lambda p: p["cost_basis"], **MONEY),
    PortfolioSensorDescription(
        key="unrealized_profit", translation_key="unrealized_profit", icon="mdi:chart-line", value=lambda p: p["unrealized_profit"], **MONEY
    ),
    PortfolioSensorDescription(
        key="realized_profit", translation_key="realized_profit", icon="mdi:cash-check", value=lambda p: p["realized_profit"], **MONEY
    ),
    PortfolioSensorDescription(
        key="positions",
        translation_key="positions",
        icon="mdi:format-list-bulleted",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda p: len(p["positions"]),
        attributes=_positions,
    ),
    PortfolioSensorDescription(
        key="pending_signals",
        translation_key="pending_signals",
        icon="mdi:bell-alert",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda p: len(p["pending_signals"]),
        attributes=lambda p: _signals(p, "pending_signals"),
    ),
    PortfolioSensorDescription(
        key="open_orders",
        translation_key="open_orders",
        icon="mdi:clock-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda p: len(p["open_orders"]),
        attributes=lambda p: _signals(p, "open_orders"),
    ),
    PortfolioSensorDescription(
        key="last_run",
        translation_key="last_run",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:robot",
        value=lambda p: _ts((p.get("last_run") or {}).get("started_at")),
        attributes=_run,
    ),
    PortfolioSensorDescription(
        key="forecast_median",
        translation_key="forecast_median",
        icon="mdi:chart-bell-curve",
        value=_forecast_value("median"),
        attributes=_forecast,
        **MONEY,
    ),
    PortfolioSensorDescription(
        key="forecast_low_80", translation_key="forecast_low_80", icon="mdi:arrow-collapse-down", value=_forecast_value("low_80"), **MONEY
    ),
    PortfolioSensorDescription(
        key="forecast_high_80", translation_key="forecast_high_80", icon="mdi:arrow-collapse-up", value=_forecast_value("high_80"), **MONEY
    ),
)

BROKER_SENSORS: tuple[PortfolioSensorDescription, ...] = (
    PortfolioSensorDescription(
        key="broker_synced",
        translation_key="broker_synced",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sync",
        value=lambda p: _ts(p.get("broker_synced_at")),
        attributes=lambda p: {"error": p.get("broker_sync_error")},
    ),
)


@dataclass(frozen=True, kw_only=True)
class MarketSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]
    attributes: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _risk(name: str, scale: bool = False) -> Callable[[dict[str, Any]], Any]:
    def get(data: dict[str, Any]) -> Any:
        risk = data.get("market_risk")
        if not risk:
            return None
        return _pct(risk[name]) if scale else risk[name]

    return get


RISK_LEVELS = ["low", "normal", "elevated", "high"]

MARKET_SENSORS: tuple[MarketSensorDescription, ...] = (
    MarketSensorDescription(key="market_status", translation_key="market_status", icon="mdi:bank", value=lambda d: d["market"]["status"]),
    MarketSensorDescription(
        key="next_open",
        translation_key="next_open",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda d: _ts(d["market"]["next_open"]),
    ),
    MarketSensorDescription(
        key="next_close",
        translation_key="next_close",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda d: _ts(d["market"]["next_close"]),
    ),
    MarketSensorDescription(
        key="drawdown_probability",
        translation_key="drawdown_probability",
        icon="mdi:chart-bell-curve-cumulative",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value=_risk("drawdown_probability", True),
        attributes=lambda d: {
            "typical": _risk("typical_drawdown_probability", True)(d),
            "data_time": (d.get("market_risk") or {}).get("data_time"),
        },
    ),
    MarketSensorDescription(
        key="risk_level",
        translation_key="risk_level",
        device_class=SensorDeviceClass.ENUM,
        options=RISK_LEVELS,
        icon="mdi:alert-decagram",
        value=lambda d: (d.get("market_risk") or {}).get("risk_level") or None,
    ),
    MarketSensorDescription(
        key="vix",
        translation_key="vix",
        icon="mdi:pulse",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=_risk("vix"),
    ),
    MarketSensorDescription(
        key="expected_volatility",
        translation_key="expected_volatility",
        icon="mdi:sine-wave",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value=_risk("expected_volatility_20d", True),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: AiTraderConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new: list[SensorEntity] = []
        for p in coordinator.portfolios.values():
            descriptions = PORTFOLIO_SENSORS + (BROKER_SENSORS if p["broker"] != "Paper" else ())
            for d in descriptions:
                uid = f"{p['id']}_{d.key}"
                if uid not in known:
                    known.add(uid)
                    new.append(PortfolioSensor(coordinator, p, d))
            for x in p["positions"]:
                uid = f"{p['id']}_position_{x['isin']}"
                if uid not in known:
                    known.add(uid)
                    new.append(PositionSensor(coordinator, p, x))
        if "market" not in known:
            known.add("market")
            new.extend(MarketSensor(coordinator, d) for d in MARKET_SENSORS)
        if new:
            async_add_entities(new)

    add_new()
    # Portfolios and positions added later in the app show up without reloading the integration.
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class PortfolioSensor(PortfolioEntity, SensorEntity):
    entity_description: PortfolioSensorDescription

    def __init__(self, coordinator: AiTraderCoordinator, portfolio: Portfolio, description: PortfolioSensorDescription) -> None:
        super().__init__(coordinator, portfolio, description.key)
        self.entity_description = description
        if description.currency:
            self._attr_native_unit_of_measurement = portfolio["currency"]

    @property
    def native_value(self) -> Any:
        p = self.portfolio
        return None if p is None else self.entity_description.value(p)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        p = self.portfolio
        if p is None or self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(p)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.history is not None:
            key = (self.portfolio_id, self.entity_description.key)
            self.coordinator.history_sensors[key] = self
            self.async_on_remove(lambda: self.coordinator.history_sensors.pop(key, None))
            self.hass.async_create_task(async_import_history(self.hass, self.coordinator, self.portfolio_id, self))


class PositionSensor(PortfolioEntity, SensorEntity):
    """Market value of one holding; unavailable after it is sold, so its history stays."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:finance"

    def __init__(self, coordinator: AiTraderCoordinator, portfolio: Portfolio, position: dict[str, Any]) -> None:
        super().__init__(coordinator, portfolio, f"position_{position['isin']}")
        self.isin: str = position["isin"]
        self._attr_name = position["name"]
        self._attr_native_unit_of_measurement = portfolio["currency"]

    @property
    def position(self) -> dict[str, Any] | None:
        p = self.portfolio
        return None if p is None else next((x for x in p["positions"] if x["isin"] == self.isin), None)

    @property
    def available(self) -> bool:
        return super().available and self.position is not None

    @property
    def native_value(self) -> float | None:
        x = self.position
        return None if x is None else x["market_value"]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        x = self.position
        if x is None:
            return None
        attrs = {
            "isin": x["isin"],
            "symbol": x.get("symbol"),
            "quantity": x["quantity"],
            "average_cost": x["average_cost"],
            "price": x.get("price"),
            "quote_time": x.get("quote_time"),
            "unrealized_profit": x["unrealized_profit"],
            "unrealized_percent": x.get("unrealized_percent"),
            "weight_percent": x["weight_percent"],
            "day_change": x.get("day_change"),
            "day_change_percent": x.get("day_change_percent"),
            "model_score_20d": x.get("score_20d"),
            "model_expected_excess_20d": _pct(x.get("expected_excess_20d")),
        }
        if f := x.get("forecast"):
            attrs |= {
                "forecast_target_date": f["target_date"][:10],
                "forecast_median": f["median"],
                "forecast_low_80": f["low_80"],
                "forecast_high_80": f["high_80"],
                "forecast_source": f["source"],
                "earnings_date": (f.get("earnings_date") or "")[:10] or None,
            }
        return attrs


class MarketSensor(MarketEntity, SensorEntity):
    entity_description: MarketSensorDescription

    def __init__(self, coordinator: AiTraderCoordinator, description: MarketSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attributes is None:
            return None
        return self.entity_description.attributes(self.coordinator.data)
