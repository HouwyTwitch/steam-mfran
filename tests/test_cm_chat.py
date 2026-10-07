"""Exercise the CM client + chat against an in-process fake CM websocket server."""

import asyncio
import gzip
import struct

import pytest
from aiohttp import WSMsgType, web

from steam_mfran.steam import proto
from steam_mfran.steam.chat import LiveChat
from steam_mfran.steam.cm import build_packet, parse_packet
from steam_mfran.steam.community import Community
from steam_mfran.steam.enums import OS_TYPE_WEB, EMsg
from steam_mfran.steam.http import COMMUNITY, EResultError

from .conftest import Reply

ME = 76561198191298143
FRIEND = 76561198973784183
INVITER = 76561198000012345


def multi(*packets: bytes, zipped: bool = True) -> bytes:
    payload = b"".join(struct.pack("<I", len(p)) + p for p in packets)
    body = {"message_body": gzip.compress(payload), "size_unzipped": len(payload)} if zipped else {"message_body": payload}
    return build_packet(EMsg.Multi, {}, proto.encode("CMsgMulti", body))


class FakeCM:
    def __init__(self):
        self.logon = None
        self.calls = []
        self.ws = None

    async def handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        async for msg in ws:
            if msg.type != WSMsgType.BINARY:
                continue
            emsg, header, body = parse_packet(msg.data)
            if emsg == EMsg.ClientLogon:
                self.logon = (header, proto.decode("CMsgClientLogon", body))
                await ws.send_bytes(build_packet(EMsg.ClientLogOnResponse, {"steamid": ME, "client_sessionid": 777},
                                                 proto.encode("CMsgClientLogonResponse", {"eresult": 1, "heartbeat_seconds": 9})))
                friends = build_packet(EMsg.ClientFriendsList, {}, proto.encode("CMsgClientFriendsList", {
                    "bincremental": False,
                    "friends": [{"ulfriendid": FRIEND, "efriendrelationship": 3},
                                {"ulfriendid": INVITER, "efriendrelationship": 2},
                                {"ulfriendid": 103582791429521412, "efriendrelationship": 3}],  # a group
                }))
                persona = build_packet(EMsg.ClientPersonaState, {}, proto.encode("CMsgClientPersonaState", {
                    "friends": [{"friendid": FRIEND, "player_name": "Budi", "persona_state": 1,
                                 "avatar_hash": bytes.fromhex("aa11bb22cc33dd44ee55ff6677889900aabbccdd")}]}))
                incoming = build_packet(EMsg.ServiceMethod, {"target_job_name": "FriendMessagesClient.IncomingMessage#1"},
                                        proto.encode("CFriendMessages_IncomingMessage_Notification", {
                                            "steamid_friend": FRIEND, "chat_entry_type": 1, "message": "hello there",
                                            "rtime32_server_timestamp": 1791350000, "ordinal": 0}))
                await ws.send_bytes(multi(friends, persona, incoming))
            elif emsg == EMsg.ServiceMethodCallFromClient:
                name = header["target_job_name"]
                self.calls.append((name, header))
                job = header.get("jobid_source")
                if name == "FriendMessages.SendMessage#1":
                    req = proto.decode("CFriendMessages_SendMessage_Request", body)
                    self.calls[-1] += (req,)
                    eresult = 15 if req["message"] == "forbidden" else 1
                    await ws.send_bytes(build_packet(EMsg.ServiceMethodResponse, {"jobid_target": job, "eresult": eresult},
                                                     proto.encode("CFriendMessages_SendMessage_Response", {
                                                         "server_timestamp": 1791350100, "ordinal": 1,
                                                         "modified_message": req["message"]})))
                elif name == "FriendMessages.GetRecentMessages#1":
                    req = proto.decode("CFriendMessages_GetRecentMessages_Request", body)
                    self.calls[-1] += (req,)
                    await ws.send_bytes(build_packet(EMsg.ServiceMethodResponse, {"jobid_target": job, "eresult": 1},
                                                     proto.encode("CFriendMessages_GetRecentMessages_Response", {
                                                         "messages": [
                                                             {"accountid": FRIEND & 0xFFFFFFFF, "timestamp": 200, "message": "second"},
                                                             {"accountid": ME & 0xFFFFFFFF, "timestamp": 100, "message": "first"},
                                                         ], "more_available": True})))
        return ws


@pytest.fixture
async def cm_server():
    cm = FakeCM()
    app = web.Application()
    app.router.add_get("/cmsocket/", cm.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield cm, f"127.0.0.1:{port}"
    await runner.cleanup()


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_chat_over_cm(fake_http, cm_server):
    cm, endpoint = cm_server
    fake_http.route("GET", f"{COMMUNITY}/chat/clientjstoken", lambda c: Reply(
        body={"logged_in": True, "steamid": str(ME), "account_name": "houwy", "token": "web-token"}))
    events = []
    relationships = []
    chat = LiveChat(fake_http, Community(fake_http, lambda: ME), lambda: ME, lambda: "access",
                    refresh_session=lambda: asyncio.sleep(0), emit=lambda t, d: events.append((t, d)))
    chat.on_relationships = lambda changes, full: relationships.append((changes, full))
    chat.cm.scheme = "ws"
    chat.cm.servers_override = [endpoint]
    chat.start()
    try:
        assert await chat.cm.wait_online(5)
        header, logon = cm.logon
        assert header["steamid"] == ME and header.get("client_sessionid", 0) == 0
        assert logon["web_logon_nonce"] == "web-token" and logon["account_name"] == "houwy"
        assert logon["client_os_type"] == OS_TYPE_WEB and logon["chat_mode"] == 2 and logon["ui_mode"] == 4

        await wait_for(lambda: any(t == "chat:message" for t, _ in events))
        msg = next(d for t, d in events if t == "chat:message")
        assert msg["partner"] == str(FRIEND) and msg["text"] == "hello there" and msg["outgoing"] is False
        assert relationships[0] == ({FRIEND: 3, INVITER: 2}, True)
        assert chat.personas[FRIEND]["name"] == "Budi" and "aa11bb22" in chat.personas[FRIEND]["avatar"]
        assert [f["steamid"] for f in chat.friend_list()] == [str(FRIEND)]

        sent = await chat.send(FRIEND, "hi [b]")
        name, header, req = cm.calls[-1]
        assert name == "FriendMessages.SendMessage#1" and header["client_sessionid"] == 777
        assert req == {"steamid": FRIEND, "chat_entry_type": 1, "message": "hi \\[b]", "contains_bbcode": True}
        assert sent["timestamp"] == 1791350100 and sent["outgoing"]

        with pytest.raises(EResultError):
            await chat.send(FRIEND, "forbidden")

        hist = await chat.history(FRIEND)
        assert [m["text"] for m in hist["messages"]] == ["first", "second"]
        assert hist["messages"][0]["outgoing"] is True and hist["more"] is True
        req = cm.calls[-1][2]
        assert req["steamid1"] == ME and req["steamid2"] == FRIEND and req["time_last"] == 0x7FFFFFFF
    finally:
        await chat.stop()
