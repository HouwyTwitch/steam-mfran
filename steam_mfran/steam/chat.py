"""Steam friend chat built on the CM client, with a WebAPI fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import proto
from .cm import CMClient, CMNotConnected
from .community import Community
from .enums import PERSONA_FLAGS_DEFAULT, EChatEntryType, EFriendRelationship, EMsg
from .http import SteamError, SteamHttp
from .ids import avatar_url, from_account_id, is_individual

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]

# Unified methods: (request schema, response schema)
METHODS = {
    "FriendMessages.SendMessage#1": ("CFriendMessages_SendMessage_Request", "CFriendMessages_SendMessage_Response"),
    "FriendMessages.GetRecentMessages#1": (
        "CFriendMessages_GetRecentMessages_Request", "CFriendMessages_GetRecentMessages_Response"),
    "FriendMessages.GetActiveMessageSessions#1": (
        "CFriendsMessages_GetActiveMessageSessions_Request", "CFriendsMessages_GetActiveMessageSessions_Response"),
    "FriendMessages.AckMessage#1": ("CFriendMessages_AckMessage_Notification", "NoResponse"),
}


def escape_bbcode(text: str) -> str:
    """Steam clients escape user-typed brackets so they are not parsed as BBCode."""
    return text.replace("[", "\\[")


@dataclass
class ChatMessage:
    partner: int
    sender: int
    text: str
    timestamp: int
    ordinal: int
    outgoing: bool

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.timestamp, self.ordinal, self.sender)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": f"{self.timestamp}:{self.ordinal}:{self.sender}",
            "partner": str(self.partner),
            "sender": str(self.sender),
            "outgoing": self.outgoing,
            "text": self.text,
            "timestamp": self.timestamp,
            "ordinal": self.ordinal,
        }


class LiveChat:
    def __init__(
        self,
        http: SteamHttp,
        community: Community,
        my_steamid: Callable[[], int | None],
        access_token: Callable[[], str | None],
        refresh_session: Callable[[], Awaitable[None]],
        emit: Emit,
    ):
        self.http = http
        self.community = community
        self._me = my_steamid
        self._access_token = access_token
        self.emit = emit
        self.friends: dict[int, int] = {}  # steamid -> EFriendRelationship
        self.friends_loaded = False
        self.personas: dict[int, dict[str, Any]] = {}
        self.on_relationships: Callable[[dict[int, int], bool], None] | None = None
        self.cm = CMClient(http, self._credentials)
        self.cm.on_state = self._on_cm_state
        self.cm.on_auth_failure = refresh_session
        self.cm.notification_handlers["FriendMessagesClient.IncomingMessage#1"] = self._on_incoming
        self.cm.emsg_handlers[EMsg.ClientFriendsList] = self._on_friends_list
        self.cm.emsg_handlers[EMsg.ClientPersonaState] = self._on_persona_state
        self._poll_task: asyncio.Task | None = None
        self._last_poll = int(time.time())

    @property
    def me(self) -> int:
        sid = self._me()
        if not sid:
            raise SteamError("not logged in")
        return sid

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.cm.start()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._fallback_poll(), name="chat-poll")

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        await self.cm.stop()

    def status(self) -> dict[str, Any]:
        return {"state": self.cm.state, "error": self.cm.last_error, "transport": "cm" if self.cm.online else "webapi"}

    async def _credentials(self) -> dict[str, Any]:
        return await self.community.get_client_js_token()

    def _on_cm_state(self, state: str) -> None:
        self.emit("chat:status", self.status())
        if state == "online" and self._me():
            asyncio.ensure_future(self.request_personas([self.me]))

    # ------------------------------------------------------------ transport
    async def _call(self, method: str, request: dict[str, Any], http_method: str = "POST") -> dict[str, Any]:
        req_schema, resp_schema = METHODS[method]
        if self.cm.online:
            try:
                return await self.cm.call(method, req_schema, request, resp_schema)
            except (CMNotConnected, asyncio.TimeoutError) as exc:
                log.debug("CM call %s failed (%s); using WebAPI", method, exc)
        service, name = method.split("#")[0].split(".")
        return await self.http.call_service(
            service, name, req_schema, request, resp_schema,
            http_method=http_method, access_token=self._access_token(),
        )

    # ------------------------------------------------------------ messages
    async def sessions(self) -> list[dict[str, Any]]:
        resp = await self._call(
            "FriendMessages.GetActiveMessageSessions#1",
            {"only_sessions_with_messages": True}, http_method="GET",
        )
        out = []
        for s in resp.get("message_sessions", []):
            sid = from_account_id(s.get("accountid_friend", 0))
            out.append({
                "partner": str(sid),
                "last_message": s.get("last_message", 0),
                "last_view": s.get("last_view", 0),
                "unread": s.get("unread_message_count", 0),
            })
        return out

    async def history(
        self, partner: int, before_ts: int | None = None, before_ordinal: int | None = None, count: int = 40
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "steamid1": self.me,
            "steamid2": partner,
            "count": count,
            "most_recent_conversation": False,
            "bbcode_format": True,
            "time_last": before_ts or 0x7FFFFFFF,
        }
        if before_ts and before_ordinal is not None:
            req["ordinal_last"] = before_ordinal
        resp = await self._call("FriendMessages.GetRecentMessages#1", req, http_method="GET")
        msgs: dict[tuple, ChatMessage] = {}
        for m in resp.get("messages", []):
            sender = from_account_id(m.get("accountid", 0))
            msg = ChatMessage(
                partner=partner, sender=sender, text=m.get("message", ""),
                timestamp=m.get("timestamp", 0), ordinal=m.get("ordinal", 0),
                outgoing=sender == self.me,
            )
            if before_ts and (msg.timestamp, msg.ordinal) >= (before_ts, before_ordinal or 0):
                continue
            msgs[msg.key] = msg
        ordered = sorted(msgs.values(), key=lambda m: (m.timestamp, m.ordinal))
        return {"messages": [m.to_dict() for m in ordered], "more": bool(resp.get("more_available"))}

    async def send(self, partner: int, text: str, raw: bool = False) -> dict[str, Any]:
        body = text if raw else escape_bbcode(text)
        resp = await self._call("FriendMessages.SendMessage#1", {
            "steamid": partner,
            "chat_entry_type": EChatEntryType.ChatMsg,
            "message": body,
            "contains_bbcode": True,
        })
        msg = ChatMessage(
            partner=partner, sender=self.me, text=resp.get("modified_message") or body,
            timestamp=resp.get("server_timestamp") or int(time.time()), ordinal=resp.get("ordinal", 0),
            outgoing=True,
        )
        data = msg.to_dict()
        self.emit("chat:message", data)
        return data

    async def typing(self, partner: int) -> None:
        await self._call("FriendMessages.SendMessage#1", {
            "steamid": partner, "chat_entry_type": EChatEntryType.Typing, "message": "",
        })

    async def ack(self, partner: int, timestamp: int) -> None:
        req = {"steamid_partner": partner, "timestamp": timestamp}
        if self.cm.online:
            await self.cm.notify("FriendMessages.AckMessage#1", "CFriendMessages_AckMessage_Notification", req)
        else:
            await self._call("FriendMessages.AckMessage#1", req)

    async def send_image(
        self, partner: int, content: bytes, mime: str, width: int, height: int, spoiler: bool = False
    ) -> str:
        url = await self.community.upload_chat_image(partner, content, mime, width, height, spoiler)
        # Steam posts the image itself; the CM echoes it back (local_echo) when online.
        if not self.cm.online:
            self.emit("chat:message", ChatMessage(
                partner=partner, sender=self.me, text=f"[img src={url}][/img]",
                timestamp=int(time.time()), ordinal=0, outgoing=True,
            ).to_dict())
        return url

    def _on_incoming(self, body: bytes) -> None:
        n = proto.decode("CFriendMessages_IncomingMessage_Notification", body)
        partner = n.get("steamid_friend", 0)
        entry = n.get("chat_entry_type", 0)
        if entry == EChatEntryType.Typing:
            if not n.get("local_echo"):
                self.emit("chat:typing", {"partner": str(partner)})
            return
        if entry != EChatEntryType.ChatMsg:
            return
        echo = bool(n.get("local_echo"))
        msg = ChatMessage(
            partner=partner,
            sender=self.me if echo else partner,
            text=n.get("message", ""),
            timestamp=n.get("rtime32_server_timestamp") or int(time.time()),
            ordinal=n.get("ordinal", 0),
            outgoing=echo,
        )
        self._last_poll = max(self._last_poll, msg.timestamp)
        self.emit("chat:message", msg.to_dict())
        if partner not in self.personas:
            asyncio.ensure_future(self.request_personas([partner]))

    async def _fallback_poll(self) -> None:
        """When the CM link is down, poll the WebAPI for new messages."""
        seen: dict[int, int] = {}
        while True:
            await asyncio.sleep(12)
            if self.cm.online or not self._access_token():
                continue
            try:
                for s in await self.sessions():
                    sid = int(s["partner"])
                    last = s["last_message"]
                    if last > max(seen.get(sid, 0), self._last_poll) and s["unread"]:
                        hist = await self.history(sid, count=10)
                        for m in hist["messages"]:
                            if m["timestamp"] > max(seen.get(sid, 0), self._last_poll) and not m["outgoing"]:
                                self.emit("chat:message", m)
                    seen[sid] = max(seen.get(sid, 0), last)
                self._last_poll = int(time.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("Chat fallback poll failed: %s", exc)

    # ------------------------------------------------------------ friends & personas
    def _on_friends_list(self, body: bytes) -> None:
        data = proto.decode("CMsgClientFriendsList", body)
        incremental = bool(data.get("bincremental"))
        if not incremental:
            self.friends.clear()
        changes: dict[int, int] = {}
        for f in data.get("friends", []):
            sid = int(f.get("ulfriendid", 0))
            if not is_individual(sid):
                continue
            rel = int(f.get("efriendrelationship", 0))
            if rel == EFriendRelationship.NoRelationship:
                self.friends.pop(sid, None)
            else:
                self.friends[sid] = rel
            changes[sid] = rel
        self.friends_loaded = True
        if self.on_relationships:
            self.on_relationships(changes, not incremental)
        self.emit("friends", {"incremental": incremental})
        new = [sid for sid, rel in changes.items() if rel == EFriendRelationship.Friend and sid not in self.personas]
        if new:
            asyncio.ensure_future(self.request_personas(new))

    def _on_persona_state(self, body: bytes) -> None:
        data = proto.decode("CMsgClientPersonaState", body)
        for f in data.get("friends", []):
            sid = int(f.get("friendid", 0))
            if not is_individual(sid):
                continue
            p = self.personas.setdefault(sid, {"steamid": str(sid)})
            if "player_name" in f:
                p["name"] = f["player_name"]
            if "avatar_hash" in f:
                p["avatar"] = avatar_url(f["avatar_hash"])
            if "persona_state" in f:
                p["state"] = f["persona_state"]
            if "game_name" in f or "game_played_app_id" in f:
                p["game"] = f.get("game_name") or (f"App {f['game_played_app_id']}" if f.get("game_played_app_id") else None)
            if f.get("last_seen_online") or f.get("last_logoff"):
                p["last_seen"] = f.get("last_seen_online") or f.get("last_logoff")
            self.emit("persona", p)

    async def request_personas(self, steamids: list[int]) -> None:
        steamids = [s for s in steamids if is_individual(s)]
        if not steamids:
            return
        if self.cm.online:
            try:
                await self.cm.send_message(EMsg.ClientRequestFriendData, "CMsgClientRequestFriendData", {
                    "persona_state_requested": PERSONA_FLAGS_DEFAULT, "friends": steamids,
                })
                return
            except CMNotConnected:
                pass
        for sid in steamids[:20]:
            try:
                mini = await self.community.get_miniprofile(sid)
            except Exception:  # noqa: BLE001
                continue
            p = self.personas.setdefault(sid, {"steamid": str(sid)})
            p["name"] = mini.get("persona_name") or p.get("name")
            p["avatar"] = mini.get("avatar_url") or p.get("avatar")
            self.emit("persona", p)

    def friend_list(self) -> list[dict[str, Any]]:
        out = []
        for sid, rel in self.friends.items():
            if rel != EFriendRelationship.Friend:
                continue
            p = dict(self.personas.get(sid) or {"steamid": str(sid)})
            p["relationship"] = rel
            out.append(p)
        return out
