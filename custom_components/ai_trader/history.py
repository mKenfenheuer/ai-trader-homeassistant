"""Fills the long-term statistics of the value sensors with the app's daily history.

The app knows every portfolio's value since its first deposit; Home Assistant only from the day the integration was
installed. On the first start each value sensor imports the days before that, once. The recorder takes over from
there with its own hourly statistics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import AiTraderError

if TYPE_CHECKING:
    from .coordinator import AiTraderCoordinator
    from .sensor import PortfolioSensor

_LOGGER = logging.getLogger(__name__)


async def async_import_history(
    hass: HomeAssistant, coordinator: AiTraderCoordinator, portfolio_id: int, sensor: PortfolioSensor, force: bool = False
) -> int:
    """Imports the daily values before the sensor's first own statistic; returns the number of days."""
    entity_id = sensor.entity_id
    key = f"{portfolio_id}_{sensor.entity_description.key}"
    cutoff = await coordinator.async_history_cutoff(key)
    if cutoff is not None and not force:
        return 0
    if "recorder" not in hass.config.components:
        return 0

    try:
        points = await coordinator.async_history(portfolio_id)
    except AiTraderError as err:
        _LOGGER.warning("History of portfolio %s not available: %s", portfolio_id, err)
        return 0

    # Days before the first hour the recorder compiled itself, so imported and recorded values never overlap. A
    # repeated import keeps the limit of the first one; by then the imported days are statistics themselves.
    if cutoff is None:
        first_own = await get_instance(hass).async_add_executor_job(_first_statistic, hass, entity_id)
        cutoff = first_own or dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    pick = sensor.entity_description.history
    rows: list[StatisticData] = []
    for point in points:
        start = dt_util.parse_datetime(point["time"])
        if start is None:
            continue
        start = dt_util.as_utc(start).replace(minute=0, second=0, microsecond=0)
        if start >= cutoff:
            continue
        value = pick(point)
        if value is None:
            continue
        rows.append(StatisticData(start=start, mean=value, min=value, max=value))

    if rows:
        metadata = StatisticMetaData(
            has_sum=False,
            mean_type=StatisticMeanType.ARITHMETIC,
            name=None,
            source="recorder",
            statistic_id=entity_id,
            unit_class=None,
            unit_of_measurement=sensor.native_unit_of_measurement,
        )
        async_import_statistics(hass, metadata, rows)
        _LOGGER.info("Imported %s days of %s from AI Trader", len(rows), entity_id)
    await coordinator.async_set_history_cutoff(key, cutoff)
    return len(rows)


def _first_statistic(hass: HomeAssistant, statistic_id: str) -> datetime | None:
    """Start of the oldest statistic the recorder has for the sensor, or None."""
    # The newest statistic tells whether there is any; then the oldest is searched in daily steps.
    if not get_last_statistics(hass, 1, statistic_id, False, {"mean"}):
        return None
    rows = statistics_during_period(hass, dt_util.utcnow() - timedelta(days=365 * 30), None, {statistic_id}, "day", None, {"mean"})
    series = rows.get(statistic_id) or []
    if not series:
        return None
    return dt_util.utc_from_timestamp(series[0]["start"])
