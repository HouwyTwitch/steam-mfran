"""CS2 item price sources used to estimate inventory value.

All bulk providers return a ``{market_hash_name: usd_price}`` map that we cache
on disk. Prices are approximate by design: the goal is to rank friend
requests by inventory value, not to appraise items to the cent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from ..storage import JsonStore
from .http import COMMUNITY, RateLimiter, RateLimited, SteamError, SteamHttp
from .inventory import Inventory

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    description: str
    bulk: bool = True


PROVIDERS: dict[str, ProviderInfo] = {
    "csgotrader": ProviderInfo(
        "csgotrader", "Steam Market (via csgotrader.app)",
        "Recent Steam Community Market sale prices, refreshed by csgotrader.app every few hours."),
    "skinport": ProviderInfo(
        "skinport", "Skinport",
        "Skinport's suggested price for every CS2 item (public API, cached 1 hour)."),
    "marketcsgo": ProviderInfo(
        "marketcsgo", "Market.CSGO",
        "Lowest listing prices on market.csgo.com."),
    "steam": ProviderInfo(
        "steam", "Steam Market (live, slow)",
        "Asks the Steam Market for each item. Accurate, but rate limited to ~20 items per minute.",
        bulk=False),
}

_NUMBER_RE = re.compile(r"[\d.,]+")


def _first_number(*values: Any) -> float | None:
    for v in values:
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def parse_csgotrader(data: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(data, dict):
        return out
    for name, entry in data.items():
        if isinstance(entry, dict):
            price = _first_number(entry.get("last_24h"), entry.get("last_7d"), entry.get("last_30d"), entry.get("last_90d"))
        else:
            price = _first_number(entry)
        if price:
            out[name] = price
    return out


def parse_skinport(data: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    for entry in data if isinstance(data, list) else []:
        name = entry.get("market_hash_name")
        price = _first_number(entry.get("suggested_price"), entry.get("median_price"), entry.get("min_price"))
        if name and price:
            out[name] = price
    return out


def parse_marketcsgo(data: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    items = data.get("items") if isinstance(data, dict) else None
    for entry in items or []:
        name = entry.get("market_hash_name")
        price = _first_number(entry.get("price"))
        if name and price:
            out[name] = price
    return out


def parse_steam_price(text: str | None) -> float | None:
    """Parse a USD Steam market price string like ``$1,234.56``."""
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


BULK_SOURCES: dict[str, tuple[str, dict[str, str], Callable[[Any], dict[str, float]], int]] = {
    # id: (url, extra headers, parser, ttl seconds)
    "csgotrader": ("https://prices.csgotrader.app/latest/steam.json", {}, parse_csgotrader, 6 * 3600),
    "skinport": (
        "https://api.skinport.com/v1/items?app_id=730&currency=USD&tradable=0",
        {"Accept-Encoding": "br"},
        parse_skinport,
        3600,
    ),
    "marketcsgo": ("https://market.csgo.com/api/v2/prices/USD.json", {}, parse_marketcsgo, 3600),
}


class PriceService:
    def __init__(self, http: SteamHttp, cache_dir: Path, provider: str = "csgotrader"):
        self.http = http
        self.cache_dir = cache_dir
        self.provider = provider if provider in PROVIDERS else "csgotrader"
        self.prices: dict[str, float] = {}
        self.updated_at: float = 0
        self.error: str | None = None
        self._lock = asyncio.Lock()
        self._steam_cache = JsonStore(cache_dir / "prices_steam_items.json")
        self._steam_prices: dict[str, list] = self._steam_cache.load({}) or {}
        self._steam_limiter = RateLimiter(3.2)
        self._rates: dict[str, float] = {"USD": 1.0}
        self._rates_at = 0.0

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": PROVIDERS[self.provider].label,
            "count": len(self.prices) if PROVIDERS[self.provider].bulk else len(self._steam_prices),
            "updated_at": self.updated_at or None,
            "error": self.error,
        }

    def set_provider(self, provider: str) -> None:
        if provider in PROVIDERS and provider != self.provider:
            self.provider = provider
            self.prices = {}
            self.updated_at = 0

    async def ensure_loaded(self, force: bool = False) -> None:
        if not PROVIDERS[self.provider].bulk:
            return
        url, headers, parser, ttl = BULK_SOURCES[self.provider]
        async with self._lock:
            if not force and self.prices and time.time() - self.updated_at < ttl:
                return
            store = JsonStore(self.cache_dir / f"prices_{self.provider}.json")
            cached = store.load(None)
            if not force and cached and time.time() - cached.get("updated_at", 0) < ttl:
                self.prices = cached.get("prices", {})
                self.updated_at = cached["updated_at"]
                return
            try:
                resp, body = await self.http.request("GET", url, headers=headers, retries=2)
                if resp.status >= 400:
                    raise SteamError(f"HTTP {resp.status}")
                prices = parser(json.loads(body))
                if not prices:
                    raise SteamError("price list was empty")
                self.prices = prices
                self.updated_at = time.time()
                self.error = None
                store.save({"updated_at": self.updated_at, "prices": prices})
                log.info("Loaded %d prices from %s", len(prices), self.provider)
            except Exception as exc:  # noqa: BLE001 - any failure falls back to cache
                self.error = f"{PROVIDERS[self.provider].label}: {exc}"
                log.warning("Price refresh failed: %s", self.error)
                if cached and not self.prices:
                    self.prices = cached.get("prices", {})
                    self.updated_at = cached.get("updated_at", 0)

    async def _steam_price(self, name: str) -> float | None:
        cached = self._steam_prices.get(name)
        if cached and time.time() - cached[1] < 24 * 3600:
            return cached[0]
        await self._steam_limiter.wait()
        try:
            data = await self.http.get_json(
                f"{COMMUNITY}/market/priceoverview/",
                params={"country": "US", "currency": "1", "appid": "730", "market_hash_name": name},
                headers={"Referer": f"{COMMUNITY}/market/listings/730/{quote(name)}"},
            )
        except RateLimited:
            self._steam_limiter.penalize(60)
            return cached[0] if cached else None
        except (SteamError, ValueError):
            return cached[0] if cached else None
        price = None
        if isinstance(data, dict) and data.get("success"):
            price = parse_steam_price(data.get("lowest_price")) or parse_steam_price(data.get("median_price"))
        self._steam_prices[name] = [price, time.time()]
        self._steam_cache.save(self._steam_prices)
        return price

    async def price_inventory(self, inv: Inventory) -> None:
        """Fill ``item.price`` (USD) for every item we can price."""
        if inv.state != "ok":
            return
        if PROVIDERS[self.provider].bulk:
            await self.ensure_loaded()
            for item in inv.items:
                item.price = self.prices.get(item.market_hash_name)
            return
        for item in sorted(inv.items, key=lambda i: not i.marketable):
            item.price = await self._steam_price(item.market_hash_name) if item.marketable else None

    # ------------------------------------------------------------ currency
    async def usd_rate(self, currency: str) -> float | None:
        currency = (currency or "USD").upper()
        if currency == "USD":
            return 1.0
        if time.time() - self._rates_at > 12 * 3600 or currency not in self._rates:
            try:
                data = await self.http.get_json("https://open.er-api.com/v6/latest/USD")
                if isinstance(data, dict) and data.get("rates"):
                    self._rates = {k.upper(): float(v) for k, v in data["rates"].items()}
                    self._rates_at = time.time()
            except (SteamError, ValueError) as exc:
                log.warning("Currency rates unavailable: %s", exc)
        return self._rates.get(currency)


def summarize(inv: Inventory) -> dict[str, Any]:
    """Compact summary stored with each friend request."""
    priced = [i for i in inv.items if i.price]
    value = sum(i.price * i.count for i in priced)
    tradable_value = sum(i.price * i.count for i in priced if i.tradable)
    top = sorted(priced, key=lambda i: i.price or 0, reverse=True)[:8]
    return {
        "state": inv.state,
        "error": inv.error,
        "total_count": inv.total_count,
        "unique_count": len(inv.items),
        "priced_count": sum(i.count for i in priced),
        "value": round(value, 2),
        "tradable_value": round(tradable_value, 2),
        "top_items": [
            {"name": i.market_hash_name, "icon": i.icon, "price": i.price, "count": i.count,
             "rarity_color": i.rarity_color, "name_color": i.name_color}
            for i in top
        ],
        "fetched_at": inv.fetched_at,
    }
