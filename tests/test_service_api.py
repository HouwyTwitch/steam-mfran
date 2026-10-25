"""AppService + HTTP API against the demo backend."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from steam_mfran.core.demo import DemoBackend
from steam_mfran.core.events import EventHub
from steam_mfran.core.service import AppService
from steam_mfran.core.settings import Rule, RuleConditions, SettingsStore
from steam_mfran.server import create_app
from steam_mfran.steam.http import SteamError
from steam_mfran.storage import JsonStore


async def wait_for(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@pytest.fixture
async def service(tmp_path):
    hub = EventHub()
    settings = SettingsStore(JsonStore(tmp_path / "settings.json"))
    settings.update({"analysis_workers": 4})
    backend = DemoBackend(tmp_path, hub.emit)
    svc = AppService(backend, settings, tmp_path, hub)
    await svc.start()
    await backend.login("me", "pw")
    await backend.submit_code("ABCDE")
    yield svc
    await svc.stop()


async def test_invites_are_fetched_and_analysed(service):
    await wait_for(lambda: len(service.records) == 24)
    await wait_for(lambda: all(r["analysis"]["state"] in ("done", "error") for r in service.records.values()), 30)
    states = {r["inventory"]["state"] for r in service.records.values() if r.get("inventory")}
    assert {"ok", "private"} <= states
    rich = max(service.records.values(), key=lambda r: (r.get("inventory") or {}).get("value") or 0)
    assert rich["inventory"]["value"] > 0 and rich["inventory"]["top_items"]
    assert rich["profile"]["member_since"]


async def test_bulk_accept_then_message(service):
    await wait_for(lambda: len(service.records) == 24)
    sids = [int(s) for s in list(map(str, service.records))[:3]]
    service.settings.update({"broadcast_interval_seconds": 0.5})
    result = await service.act("accept", sids, message="welcome!")
    assert all(v is None for v in result["results"].values())
    assert all(service.records[s]["status"] == "accepted" for s in sids)
    job = service.broadcasts[result["broadcast"]["id"]]
    await wait_for(lambda: job["state"] == "done", 20)
    assert job["sent"] == 3 and job["failed"] == 0
    assert service.history[-1]["action"] == "accept"


async def test_automation_rules_apply_after_analysis(service):
    await service.update_settings({
        "automation_enabled": True,
        "rules": [Rule(name="no private", action="ignore", conditions=RuleConditions(inventory="private")).model_dump()],
    })
    await wait_for(lambda: len(service.records) == 24)
    await wait_for(lambda: all(r["analysis"]["state"] in ("done", "error") for r in service.records.values()), 30)
    await wait_for(lambda: all(r["status"] == "ignored" for r in service.records.values()
                               if (r.get("inventory") or {}).get("state") == "private"))
    assert any(h["source"] == "rule: no private" for h in service.history)


async def test_removed_chat_returns_with_next_message(service, tmp_path):
    convs = await service.conversations()
    partner = convs[-1]["partner"]
    await service.hide_chat(int(partner), convs[-1]["last_message"])
    assert partner not in [c["partner"] for c in await service.conversations()]
    assert JsonStore(tmp_path / "chat_hidden.json").load({}) == {partner: convs[-1]["last_message"]}

    await service.backend.chat.send(int(partner), "still there?")
    assert partner not in service.hidden_chats
    assert partner in [c["partner"] for c in await service.conversations()]


async def test_message_in_the_same_second_unhides_chat(service):
    partner = (await service.conversations())[-1]["partner"]
    await service.hide_chat(int(partner), int(time.time()) + 60)  # newest message shares the hidden timestamp
    await service.backend.chat.send(int(partner), "ping")
    assert partner in [c["partner"] for c in await service.conversations()]


async def test_chat_partners_get_level_and_inventory(service):
    convs = await service.conversations()
    sids = [int(c["partner"]) for c in convs]
    assert any(c["info"]["pending"] for c in convs)
    await wait_for(lambda: not any(service._needs_partner_info(s) or service.partner_info(s)["pending"] for s in sids), 40)
    infos = [c["info"] for c in await service.conversations()]
    assert all(i["level"] for i in infos) and all(i["friend"] is True for i in infos)
    assert all(i["inventory"] or i["error"] for i in infos)
    assert any(i["inventory"] and i["inventory"]["state"] in ("ok", "empty", "private") for i in infos)


async def test_remove_friend(service):
    sid = int((await service.conversations())[0]["partner"])
    await service.remove_friend(sid)
    assert service.partner_info(sid)["friend"] is False
    assert str(sid) not in {f["steamid"] for f in service.friends()}
    with pytest.raises(SteamError):
        await service.remove_friend(sid)


def test_http_api_demo_flow(tmp_path):
    app = create_app(tmp_path, demo=True)
    with TestClient(app, base_url="http://127.0.0.1:8777") as client:
        assert client.get("/api/state").json()["auth"]["state"] == "logged_out"
        assert client.post("/api/invites/refresh").status_code == 401
        r = client.post("/api/auth/login", json={"username": "me", "password": "pw"})
        assert r.json()["state"] == "awaiting_guard"
        assert client.post("/api/auth/code", json={"code": "nope"}).status_code == 400
        assert client.post("/api/auth/code", json={"code": "abcde"}).json()["state"] == "authenticated"
        client.post("/api/invites/refresh")
        items = client.get("/api/state").json()["invites"]["items"]
        assert len(items) == 24
        r = client.post("/api/invites/action", json={"action": "ignore", "steamids": [items[0]["steamid"]]})
        assert r.json()["results"] == {items[0]["steamid"]: None}
        convs = client.get("/api/chat/conversations").json()
        assert convs and convs[0]["persona"]["name"]
        msg = client.post(f"/api/chat/{convs[0]['partner']}/send", json={"text": "hey"}).json()
        assert msg["outgoing"] and msg["text"] == "hey"
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (32).to_bytes(4, "big") + b"\x08\x02"
        r = client.post(f"/api/chat/{convs[0]['partner']}/image", files={"file": ("a.png", png, "image/png")})
        assert r.status_code == 200 and r.json()["url"].startswith("data:image/png")
        assert client.post(f"/api/chat/{convs[0]['partner']}/image", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400
        convs = client.get("/api/chat/conversations").json()
        quiet = convs[-1]["partner"]
        assert convs[-1]["info"]["friend"] is True
        assert client.post(f"/api/chat/{quiet}/hide", json={"last_message": convs[-1]["last_message"]}).json() == {"ok": True}
        assert quiet not in [c["partner"] for c in client.get("/api/chat/conversations").json()]
        assert client.delete(f"/api/chat/{quiet}/hide").status_code == 200
        assert quiet in [c["partner"] for c in client.get("/api/chat/conversations").json()]
        assert client.post(f"/api/friends/{quiet}/remove").json() == {"ok": True}
        assert client.post(f"/api/friends/{quiet}/remove").status_code == 502
        s = client.put("/api/settings", json={"currency": "eur", "theme": "dark"}).json()
        assert s["currency"] == "EUR" and s["theme"] == "dark"
        assert client.get("/").status_code == 200
        with client.websocket_connect("/api/events", headers={"host": "127.0.0.1:8777"}) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_host_and_origin_guards(tmp_path):
    with TestClient(create_app(tmp_path, demo=True), base_url="http://127.0.0.1:8777") as client:
        assert client.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
        r = client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"})
        assert r.status_code == 403
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/events", headers={"host": "127.0.0.1:8777",
                                                                  "origin": "https://attacker.example"}) as ws:
                ws.receive_json()


def test_access_token(tmp_path):
    with TestClient(create_app(tmp_path, demo=True, token="s3cret"), base_url="http://127.0.0.1:8777") as client:
        assert client.get("/api/state").status_code == 401
        assert client.get("/?token=s3cret").status_code == 200  # sets the cookie
        assert client.get("/api/state").status_code == 200
