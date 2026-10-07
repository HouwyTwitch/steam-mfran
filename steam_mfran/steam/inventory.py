"""CS2 inventory fetching (``/inventory/<steamid>/730/2``)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .http import COMMUNITY, RateLimiter, SteamError, SteamHttp

log = logging.getLogger(__name__)

CS2_APPID = 730
ICON_CDN = "https://community.fastly.steamstatic.com/economy/image/"


@dataclass
class InventoryItem:
    market_hash_name: str
    name: str
    icon: str
    type: str = ""
    name_color: str = ""
    rarity: str = ""
    rarity_color: str = ""
    exterior: str = ""
    tradable: bool = False
    marketable: bool = False
    count: int = 0
    price: float | None = None  # unit price in USD, filled in by pricing

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Inventory:
    steamid: int
    state: str  # ok | empty | private | error
    items: list[InventoryItem] = field(default_factory=list)
    total_count: int = 0
    error: str | None = None
    fetched_at: float = field(default_factory=time.time)


def icon_url(path: str, size: str = "128fx128f") -> str:
    return f"{ICON_CDN}{path}/{size}" if path else ""


def aggregate(assets: list[dict], descriptions: list[dict]) -> list[InventoryItem]:
    """Group assets by market_hash_name using their class/instance descriptions."""
    desc_by_key = {(str(d.get("classid")), str(d.get("instanceid", "0"))): d for d in descriptions}
    grouped: dict[str, InventoryItem] = {}
    for asset in assets:
        d = desc_by_key.get((str(asset.get("classid")), str(asset.get("instanceid", "0"))))
        if not d:
            continue
        mhn = d.get("market_hash_name") or d.get("market_name") or d.get("name") or "Unknown item"
        try:
            amount = int(asset.get("amount", 1))
        except (TypeError, ValueError):
            amount = 1
        item = grouped.get(mhn)
        if item is None:
            rarity = exterior = rarity_color = ""
            for tag in d.get("tags") or []:
                cat = tag.get("category")
                if cat == "Rarity":
                    rarity = tag.get("localized_tag_name") or tag.get("name") or ""
                    rarity_color = tag.get("color") or ""
                elif cat == "Exterior":
                    exterior = tag.get("localized_tag_name") or tag.get("name") or ""
            item = InventoryItem(
                market_hash_name=mhn,
                name=d.get("name") or mhn,
                icon=icon_url(d.get("icon_url", "")),
                type=d.get("type", ""),
                name_color=d.get("name_color", ""),
                rarity=rarity,
                rarity_color=rarity_color,
                exterior=exterior,
                tradable=bool(d.get("tradable")),
                marketable=bool(d.get("marketable")),
            )
            grouped[mhn] = item
        item.count += amount
    return list(grouped.values())


async def fetch_cs2_inventory(
    http: SteamHttp,
    steamid: int,
    limiter: RateLimiter | None = None,
    max_pages: int = 10,
) -> Inventory:
    """Fetch every page of a user's CS2 inventory.

    Raises :class:`RateLimited` on HTTP 429 so the caller can back off.
    """
    url = f"{COMMUNITY}/inventory/{steamid}/{CS2_APPID}/2"
    params = {"l": "english", "count": "2000"}
    assets: list[dict] = []
    descriptions: list[dict] = []
    total = 0
    for _ in range(max_pages):
        if limiter:
            await limiter.wait()
        resp, body = await http.request(
            "GET", url, params=params, retries=1,
            headers={"Referer": f"{COMMUNITY}/profiles/{steamid}/inventory/", "Accept": "application/json"},
        )
        if resp.status in (401, 403):
            return Inventory(steamid, "private")
        if resp.status >= 400:
            return Inventory(steamid, "error", error=f"HTTP {resp.status}")
        try:
            data = json.loads(body or b"null")
        except ValueError:
            return Inventory(steamid, "error", error="invalid response")
        if data is None:
            return Inventory(steamid, "private")
        if not data.get("success"):
            err = data.get("error") or data.get("Error") or "Steam returned success=false"
            if "private" in str(err).lower():
                return Inventory(steamid, "private")
            return Inventory(steamid, "error", error=str(err))
        total = int(data.get("total_inventory_count") or 0)
        assets.extend(data.get("assets") or [])
        descriptions.extend(data.get("descriptions") or [])
        if data.get("more_items") and data.get("last_assetid"):
            params["start_assetid"] = str(data["last_assetid"])
            continue
        break
    if not assets:
        return Inventory(steamid, "empty", total_count=total)
    items = aggregate(assets, descriptions)
    return Inventory(steamid, "ok", items=items, total_count=total or sum(i.count for i in items))


class InventoryError(SteamError):
    pass
