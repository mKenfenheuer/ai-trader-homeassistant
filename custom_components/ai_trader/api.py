"""Client of the AI Trader token API (``/api/v1``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp


class AiTraderError(Exception):
    """The app could not be reached or answered with an error."""


class AiTraderAuthError(AiTraderError):
    """The token was rejected (401)."""


class AiTraderRequestError(AiTraderError):
    """The app refused the request, e.g. a proposal that is no longer open (404, 409)."""


class AiTraderClient:
    """Thin wrapper around the endpoints; all times are UTC ISO strings."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str, verify_ssl: bool = True) -> None:
        self._session = session
        self._base = url.rstrip("/") + "/api/v1/"
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._ssl = None if verify_ssl else False
        self.url = url.rstrip("/")

    async def summary(self) -> dict[str, Any]:
        return await self._request("GET", "summary")

    async def history(self, portfolio_id: int, since: datetime | None = None) -> list[dict[str, Any]]:
        params = {"since": _utc(since)} if since else None
        return await self._request("GET", f"portfolios/{portfolio_id}/history", params=params)

    async def events(self, portfolio_id: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return await self._request("GET", f"portfolios/{portfolio_id}/events", params=_window(start, end))

    async def market_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return await self._request("GET", "market/events", params=_window(start, end))

    async def approve(self, signal_id: int) -> dict[str, Any]:
        return await self._request("POST", f"signals/{signal_id}/approve")

    async def reject(self, signal_id: int) -> dict[str, Any]:
        return await self._request("POST", f"signals/{signal_id}/reject")

    async def run(self, portfolio_id: int, engine: str | None = None, instruction: str | None = None) -> dict[str, Any]:
        return await self._request("POST", f"portfolios/{portfolio_id}/run", json={"engine": engine, "instruction": instruction})

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with self._session.request(
                method, self._base + path, headers=self._headers, ssl=self._ssl, timeout=aiohttp.ClientTimeout(total=30), **kwargs
            ) as response:
                if response.status == 401:
                    raise AiTraderAuthError("API token rejected")
                if response.status in (400, 404, 409):
                    raise AiTraderRequestError(await _error(response))
                if response.status >= 400:
                    raise AiTraderError(f"HTTP {response.status}: {await _error(response)}")
                return await response.json()
        except aiohttp.ClientError as err:
            raise AiTraderError(str(err) or type(err).__name__) from err
        except TimeoutError as err:
            raise AiTraderError("timeout") from err


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": _utc(start), "end": _utc(end)}


def _utc(t: datetime) -> str:
    """UTC with a Z: a "+00:00" offset would arrive as a space in the query string."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _error(response: aiohttp.ClientResponse) -> str:
    try:
        body = await response.json(content_type=None)
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except (ValueError, aiohttp.ContentTypeError):
        pass
    return f"HTTP {response.status}"
