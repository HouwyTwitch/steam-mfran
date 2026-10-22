import json
from pathlib import Path

import pytest

from steam_mfran.core.service import INVENTORY_CACHE_VERSION, cached_inventory, store_inventory
from steam_mfran.steam.http import COMMUNITY, RateLimited, SessionExpired
from steam_mfran.steam.inventory import Inventory, InventoryItem, aggregate, fetch_cs2_inventory
from steam_mfran.steam.pricing import (
    PriceService,
    parse_csgotrader,
    parse_marketcsgo,
    parse_skinport,
    parse_steam_price,
    summarize,
)

from .conftest import Reply

SID = 76561198973784183
URL = f"{COMMUNITY}/inventory/{SID}/730/2"
FIXTURES = Path(__file__).parent / "fixtures"


def desc(classid, name, **kw):
    return {"classid": classid, "instanceid": "0", "market_hash_name": name, "name": name, "icon_url": f"ic{classid}",
            "tradable": 1, "marketable": 1, "type": "Classified Rifle",
            "tags": [{"category": "Rarity", "localized_tag_name": "Classified", "color": "d32ce6"},
                     {"category": "Exterior", "localized_tag_name": "Field-Tested"}], **kw}


def test_aggregate_groups_by_name():
    assets = [{"classid": "1", "instanceid": "0", "amount": "1"}, {"classid": "1", "instanceid": "0", "amount": "1"},
              {"classid": "2", "instanceid": "0", "amount": "3"}, {"classid": "9", "instanceid": "0", "amount": "1"}]
    items = {i.market_hash_name: i for i in aggregate(assets, [desc("1", "AK-47 | Redline (Field-Tested)"),
                                                               desc("2", "Revolution Case", tradable=0)])}
    ak = items["AK-47 | Redline (Field-Tested)"]
    assert ak.count == 2 and ak.rarity == "Classified" and ak.exterior == "Field-Tested" and ak.rarity_color == "d32ce6"
    assert ak.icon.endswith("ic1/128fx128f")
    assert items["Revolution Case"].count == 3 and items["Revolution Case"].tradable is False


async def test_fetch_matches_browser_requests(fake_http):
    """Same params, page sizes and XHR headers as steamcommunity.com's own inventory page."""
    pages = [json.loads((FIXTURES / f"inventory_page{n}.json").read_text()) for n in (1, 2)]
    fake_http.route("GET", URL, lambda c: Reply(body=pages[len(fake_http.calls) - 1]))
    inv = await fetch_cs2_inventory(fake_http, SID)

    first, second = (c.kwargs for c in fake_http.calls)
    assert first["params"] == {"l": "english", "count": "75", "preserve_bbcode": "1", "raw_asset_properties": "1"}
    assert second["params"] == {**first["params"], "count": "2000", "start_assetid": pages[0]["last_assetid"]}
    headers = first["headers"]
    assert headers["X-Requested-With"] == "XMLHttpRequest" and headers["Accept"] == "*/*"
    assert headers["Referer"] == f"{COMMUNITY}/profiles/{SID}/inventory/"
    assert headers["Sec-Fetch-Site"] == "same-origin" and headers["Sec-Fetch-Mode"] == "cors"

    assert inv.state == "ok" and inv.total_count == 5
    by_name = {i.market_hash_name: i for i in inv.items}
    assert len(by_name) == 5 and sum(i.count for i in inv.items) == 5
    usp = by_name["USP-S | Tropical Breeze (Field-Tested)"]
    assert usp.rarity == "Mil-Spec Grade" and usp.exterior == "Field-Tested" and usp.tradable and usp.marketable
    assert usp.icon.startswith("https://community.fastly.steamstatic.com/economy/image/")


async def test_language_cookie_is_sent(fake_http):
    assert fake_http.get_cookie("steamcommunity.com", "Steam_Language") == "english"
    fake_http.clear_cookies()
    assert fake_http.get_cookie("steamcommunity.com", "Steam_Language") == "english"


async def test_fetch_paginates(fake_http):
    pages = []

    def handler(call):
        params = call.kwargs["params"]
        pages.append(params.get("start_assetid"))
        if "start_assetid" not in params:
            return Reply(body={"success": 1, "assets": [{"classid": "1", "instanceid": "0", "amount": "1"}],
                               "descriptions": [desc("1", "A")], "more_items": 1, "last_assetid": "555",
                               "total_inventory_count": 2})
        return Reply(body={"success": 1, "assets": [{"classid": "2", "instanceid": "0", "amount": "1"}],
                           "descriptions": [desc("2", "B")], "total_inventory_count": 2})
    fake_http.route("GET", URL, handler)
    inv = await fetch_cs2_inventory(fake_http, SID)
    assert pages == [None, "555"]
    assert inv.state == "ok" and inv.total_count == 2 and {i.name for i in inv.items} == {"A", "B"}


@pytest.mark.parametrize("reply,state", [
    (Reply(status=403, body="null"), "private"),
    (Reply(body="null"), "private"),
    (Reply(body={"success": 1, "total_inventory_count": 0}), "empty"),
    (Reply(body={"success": False, "Error": "This profile is private."}), "private"),
    (Reply(status=500, body=""), "error"),
    (Reply(status=400, body="null"), "error"),
    (Reply(body="<html>Sorry!</html>"), "error"),
])
async def test_fetch_states(fake_http, reply, state):
    fake_http.route("GET", URL, lambda c: reply)
    assert (await fetch_cs2_inventory(fake_http, SID)).state == state


async def test_fetch_error_carries_steam_message(fake_http):
    fake_http.route("GET", URL, lambda c: Reply(status=500, body={"success": False, "error": "Service Unavailable"}))
    inv = await fetch_cs2_inventory(fake_http, SID)
    assert inv.state == "error" and inv.error == "Steam answered HTTP 500: Service Unavailable"


async def test_fetch_401_means_expired_session(fake_http):
    fake_http.route("GET", URL, lambda c: Reply(status=401, body="null"))
    with pytest.raises(SessionExpired):
        await fetch_cs2_inventory(fake_http, SID)


def test_inventory_cache_skips_errors_and_old_versions():
    cache: dict = {}
    store_inventory(cache, {"state": "ok", "value": 3})
    assert cached_inventory(cache) == {"state": "ok", "value": 3} and cache["inventory_v"] == INVENTORY_CACHE_VERSION
    assert cached_inventory({**cache, "inventory_v": INVENTORY_CACHE_VERSION - 1}) is None
    assert cached_inventory({"inventory": {"state": "private"}, "inventory_at": 1}) is None  # pre-versioning entry
    assert cached_inventory({**cache, "inventory": {"state": "error"}}) is None


async def test_fetch_rate_limited_raises(fake_http):
    fake_http.route("GET", URL, lambda c: Reply(status=429))
    with pytest.raises(RateLimited):
        await fetch_cs2_inventory(fake_http, SID)


def test_price_parsers():
    assert parse_csgotrader({"A": {"last_24h": None, "last_7d": 1.5}, "B": {"last_24h": 0}, "C": 2}) == {"A": 1.5, "C": 2.0}
    assert parse_skinport([{"market_hash_name": "A", "suggested_price": None, "median_price": 3.2},
                           {"market_hash_name": "B", "min_price": None}]) == {"A": 3.2}
    assert parse_marketcsgo({"items": [{"market_hash_name": "A", "price": "0.05"}]}) == {"A": 0.05}
    assert parse_steam_price("$1,234.56") == 1234.56
    assert parse_steam_price(None) is None


async def test_price_service_bulk_and_cache(fake_http, tmp_path):
    hits = []
    fake_http.route("GET", "https://prices.csgotrader.app/latest/steam.json",
                    lambda c: hits.append(1) or Reply(body={"A": {"last_24h": 2.0}, "B": {"last_7d": 10.0}}))
    svc = PriceService(fake_http, tmp_path)
    inv = Inventory(1, "ok", items=[InventoryItem("A", "A", "", count=3), InventoryItem("B", "B", "", count=1, tradable=True),
                                    InventoryItem("Medal", "Medal", "", count=1)])
    await svc.price_inventory(inv)
    assert [i.price for i in inv.items] == [2.0, 10.0, None]
    summary = summarize(inv)
    assert summary["value"] == 16.0 and summary["tradable_value"] == 10.0 and summary["priced_count"] == 4
    assert summary["top_items"][0]["name"] == "B"

    svc2 = PriceService(fake_http, tmp_path)  # second instance reads the disk cache
    await svc2.ensure_loaded()
    assert svc2.prices == {"A": 2.0, "B": 10.0} and len(hits) == 1


async def test_price_service_failure_keeps_error(fake_http, tmp_path):
    fake_http.route("GET", "https://api.skinport.com/", lambda c: Reply(status=503))
    svc = PriceService(fake_http, tmp_path, "skinport")
    await svc.ensure_loaded()
    assert svc.prices == {} and "Skinport" in svc.status()["error"]
