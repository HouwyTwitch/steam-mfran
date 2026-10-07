import pytest

from steam_mfran.steam.http import COMMUNITY, RateLimited
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
])
async def test_fetch_states(fake_http, reply, state):
    fake_http.route("GET", URL, lambda c: reply)
    assert (await fetch_cs2_inventory(fake_http, SID)).state == state


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
