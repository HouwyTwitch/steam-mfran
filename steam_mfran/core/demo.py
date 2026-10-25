"""Offline demo backend with generated data (``--demo``).

Implements the same surface as :class:`LiveBackend`, so the whole UI works
without a Steam account: login (any password, any 5-character code), friend
requests with profiles and inventories, chat with an echo bot, broadcasts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import random
import time
from pathlib import Path
from typing import Any, Callable

from ..steam.community import InviteBasic, ProfileInfo
from ..steam.enums import EFriendRelationship
from ..steam.http import RateLimiter, SteamError
from ..steam.inventory import Inventory, InventoryItem
from ..steam.pricing import PROVIDERS
from .session import LoginError

ME = 76561198000000001

NAMES = [
    "Kai", "nebula", "s1lent.wolf", "Mira ✿", "ToxicTurtle", "dr.awp", "FrostByte", "Lumen",
    "ポテト", "ghostface", "Vex", "kirill_2004", "Oblivion", "sunny", "R3T4KE", "pixel.pirate",
    "BananaBandit", "Ivy", "maxim", "Zephyr", "NoScopeNina", "trade bot #771", "Aurora", "Dmitry",
]
ITEMS = [
    ("★ Karambit | Doppler (Factory New)", 1290.0, "eb4b4b", "Covert Knife"),
    ("AWP | Dragon Lore (Field-Tested)", 8100.0, "eb4b4b", "Covert Sniper Rifle"),
    ("AK-47 | Case Hardened (Field-Tested)", 182.4, "d32ce6", "Classified Rifle"),
    ("M4A1-S | Printstream (Minimal Wear)", 214.0, "eb4b4b", "Covert Rifle"),
    ("Glock-18 | Fade (Factory New)", 1650.0, "8847ff", "Restricted Pistol"),
    ("USP-S | Kill Confirmed (Field-Tested)", 61.2, "eb4b4b", "Covert Pistol"),
    ("Desert Eagle | Blaze (Factory New)", 498.0, "8847ff", "Restricted Pistol"),
    ("★ Sport Gloves | Vice (Minimal Wear)", 3300.0, "eb4b4b", "Extraordinary Gloves"),
    ("AK-47 | Redline (Field-Tested)", 32.1, "d32ce6", "Classified Rifle"),
    ("AWP | Asiimov (Battle-Scarred)", 108.0, "eb4b4b", "Covert Sniper Rifle"),
    ("Revolution Case", 0.62, "b0c3d9", "Base Grade Container"),
    ("Kilowatt Case", 0.94, "b0c3d9", "Base Grade Container"),
    ("Sticker | Natus Vincere (Holo) | Paris 2023", 3.8, "8847ff", "High Grade Sticker"),
    ("MP9 | Starlight Protector (Field-Tested)", 7.4, "d32ce6", "Classified SMG"),
    ("P250 | See Ya Later (Minimal Wear)", 4.1, "d32ce6", "Classified Pistol"),
    ("Music Kit | Darude, Moments CS:GO", 5.2, "4b69ff", "High Grade Music Kit"),
    ("Five-SeveN | Angry Mob (Field-Tested)", 2.9, "d32ce6", "Classified Pistol"),
    ("Operation Riptide Case", 11.3, "b0c3d9", "Base Grade Container"),
]
CHATS = {
    0: ["hey! saw your profile, wanna trade?", "I have a Doppler I could add", "lmk 🙂"],
    1: ["gg yesterday", "you still up for 5v5 tonight?"],
    2: ["Thanks for accepting!"],
}


def _avatar(seed: str) -> str:
    h = hashlib.sha1(seed.encode()).digest()
    hue = h[0] * 360 // 255
    hue2 = (hue + 40 + h[1] % 80) % 360
    letter = (seed.strip()[:1] or "?").upper()
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 184 184'>"
        f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='hsl({hue},70%,62%)'/><stop offset='1' stop-color='hsl({hue2},65%,42%)'/>"
        f"</linearGradient></defs><rect width='184' height='184' fill='url(#g)'/>"
        f"<text x='92' y='120' font-family='system-ui,sans-serif' font-size='92' font-weight='700' "
        f"text-anchor='middle' fill='white' fill-opacity='.92'>{letter}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _item_icon(name: str, color: str) -> str:
    h = hashlib.sha1(name.encode()).digest()
    rot = h[2] % 40 - 20
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 96'>"
        f"<defs><radialGradient id='r' cx='.5' cy='.6' r='.7'><stop offset='0' stop-color='#{color}' stop-opacity='.55'/>"
        f"<stop offset='1' stop-color='#{color}' stop-opacity='0'/></radialGradient></defs>"
        "<rect width='128' height='96' fill='url(#r)'/>"
        f"<g transform='rotate({rot} 64 48)' fill='#{color}'>"
        "<rect x='18' y='40' width='80' height='12' rx='5'/><rect x='88' y='36' width='26' height='8' rx='3'/>"
        "<rect x='40' y='50' width='12' height='26' rx='4' transform='rotate(12 46 62)'/>"
        "<rect x='22' y='34' width='24' height='8' rx='3' fill-opacity='.6'/></g></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


class DemoPrices:
    def __init__(self) -> None:
        self.provider = "csgotrader"

    def status(self) -> dict[str, Any]:
        return {"provider": self.provider, "label": PROVIDERS[self.provider].label + " (demo)",
                "count": len(ITEMS), "updated_at": time.time(), "error": None}

    def set_provider(self, provider: str) -> None:
        if provider in PROVIDERS:
            self.provider = provider

    async def ensure_loaded(self, force: bool = False) -> None:
        return None

    async def usd_rate(self, currency: str) -> float | None:
        return {"USD": 1.0, "EUR": 0.92, "RUB": 96.5, "GBP": 0.79, "UAH": 41.3, "KZT": 503.0,
                "CNY": 7.2, "PLN": 3.95, "TRY": 34.1, "BRL": 5.6}.get(currency.upper())


class DemoChat:
    def __init__(self, backend: "DemoBackend", emit: Callable[[str, dict[str, Any]], None]):
        self.backend = backend
        self.emit = emit
        self.personas: dict[int, dict[str, Any]] = {}
        self.friends: dict[int, int] = {}
        self.messages: dict[int, list[dict[str, Any]]] = {}
        self.on_relationships: Callable[[dict[int, int], bool], None] | None = None
        self.state = "offline"

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "error": None, "transport": "demo"}

    def start(self) -> None:
        self.state = "online"
        self.emit("chat:status", self.status())

    async def stop(self) -> None:
        self.state = "offline"

    def add_friend(self, sid: int, name: str, avatar: str, state: int = 1, game: str | None = None) -> None:
        self.friends[sid] = EFriendRelationship.Friend
        self.personas[sid] = {"steamid": str(sid), "name": name, "avatar": avatar, "state": state, "game": game}

    def _msg(self, partner: int, outgoing: bool, text: str, ts: int, ordinal: int = 0) -> dict[str, Any]:
        sender = ME if outgoing else partner
        return {"id": f"{ts}:{ordinal}:{sender}", "partner": str(partner), "sender": str(sender),
                "outgoing": outgoing, "text": text, "timestamp": ts, "ordinal": ordinal}

    async def sessions(self) -> list[dict[str, Any]]:
        out = []
        for sid, msgs in self.messages.items():
            if msgs:
                unread = sum(1 for m in msgs if not m["outgoing"] and m["timestamp"] > m.get("_seen", 0))
                out.append({"partner": str(sid), "last_message": msgs[-1]["timestamp"],
                            "last_view": 0, "unread": unread if sid in self.backend.unread else 0})
        return out

    async def history(self, partner: int, before_ts: int | None = None, before_ordinal: int | None = None,
                      count: int = 40) -> dict[str, Any]:
        msgs = self.messages.get(partner, [])
        if before_ts:
            msgs = [m for m in msgs if (m["timestamp"], m["ordinal"]) < (before_ts, before_ordinal or 0)]
        return {"messages": msgs[-count:], "more": len(msgs) > count}

    async def send(self, partner: int, text: str, raw: bool = False) -> dict[str, Any]:
        await asyncio.sleep(0.15)
        msg = self._msg(partner, True, text, int(time.time()), len(self.messages.get(partner, [])))
        self.messages.setdefault(partner, []).append(msg)
        self.emit("chat:message", msg)
        asyncio.ensure_future(self._auto_reply(partner, text))
        return msg

    async def _auto_reply(self, partner: int, text: str) -> None:
        await asyncio.sleep(1.2)
        self.emit("chat:typing", {"partner": str(partner)})
        await asyncio.sleep(1.8)
        replies = ["sounds good 👍", "ok, one sec", "haha nice", "sure, send me a trade offer",
                   f"you said: “{text[:40]}”"]
        msg = self._msg(partner, False, random.choice(replies), int(time.time()), len(self.messages.get(partner, [])))
        self.messages.setdefault(partner, []).append(msg)
        self.backend.unread.add(partner)
        self.emit("chat:message", msg)

    async def typing(self, partner: int) -> None:
        return None

    async def ack(self, partner: int, timestamp: int) -> None:
        self.backend.unread.discard(partner)

    async def send_image(self, partner: int, content: bytes, mime: str, width: int, height: int,
                         spoiler: bool = False) -> str:
        url = f"data:{mime};base64," + base64.b64encode(content).decode()
        msg = self._msg(partner, True, f"[img src={url} width={width} height={height}][/img]",
                        int(time.time()), len(self.messages.get(partner, [])))
        self.messages.setdefault(partner, []).append(msg)
        self.emit("chat:message", msg)
        return url

    async def request_personas(self, steamids: list[int]) -> None:
        return None

    def friend_list(self) -> list[dict[str, Any]]:
        return [dict(self.personas[s], relationship=3) for s, r in self.friends.items() if r == 3]


class DemoBackend:
    demo = True

    def __init__(self, data_dir: Path, emit: Callable[[str, dict[str, Any]], None], **_: Any):
        self.emit = emit
        self.state = "logged_out"
        self.error: str | None = None
        self.account = "demo_user"
        self.guard = False
        self.prices = DemoPrices()
        self.chat = DemoChat(self, emit)
        self.inventory_limiter = RateLimiter(0.3)
        self.on_auth_change: Callable[[], None] | None = None
        self.unread: set[int] = set()
        self.rng = random.Random(7)
        self.invites: dict[int, dict[str, Any]] = {}
        self._seed()

    def _seed(self) -> None:
        now = int(time.time())
        for i, name in enumerate(NAMES):
            sid = 76561198100000000 + i * 7919
            self.invites[sid] = {"name": name, "avatar": _avatar(name), "level": self.rng.choice(
                [0, 1, 2, 4, 7, 10, 12, 15, 23, 31, 48, 64, 87, 120, 213])}
        for i, (name, state, game) in enumerate([
            ("Alyx", 1, "Counter-Strike 2"), ("Gordon", 1, None), ("Shepard", 3, None),
            ("Lara", 0, None), ("Geralt", 1, "The Witcher 3"), ("Jill", 0, None),
        ]):
            sid = 76561198200000000 + i * 104729
            self.chat.add_friend(sid, name, _avatar(name), state, game)
            lines = CHATS.get(i)
            if lines:
                base = now - 3600 * (i + 1) * 5
                msgs = []
                for j, line in enumerate(lines):
                    msgs.append(self.chat._msg(sid, j == len(lines) - 1 and i == 2, line, base + j * 70, 0))
                msgs.insert(1, self.chat._msg(sid, True, "hi! what do you have in mind?", base + 30, 0))
                self.chat.messages[sid] = msgs
                if i == 0:
                    self.unread.add(sid)

    # ------------------------------------------------------------ lifecycle / auth
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def auth_state(self) -> dict[str, Any]:
        guard = {"code": "device", "confirmation": True, "hint": ""} if self.state == "awaiting_guard" else None
        return {"state": self.state, "account_name": self.account, "steamid": str(ME),
                "guard": guard, "error": self.error, "session_expires_at": int(time.time()) + 200 * 86400}

    def _set(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.error = error
        if state == "authenticated":
            self.chat.start()
        self.emit("auth", self.auth_state())
        if self.on_auth_change:
            self.on_auth_change()

    @property
    def my_steamid(self) -> int | None:
        return ME if self.state == "authenticated" else None

    async def login(self, username: str, password: str) -> None:
        await asyncio.sleep(0.6)
        if not username or not password:
            self._set("logged_out", "Incorrect account name or password.")
            raise LoginError(self.error)
        self.account = username
        self._set("awaiting_guard")

    async def submit_code(self, code: str) -> None:
        await asyncio.sleep(0.5)
        if len(code.strip()) != 5:
            self.error = "That Steam Guard code is not correct."
            self.emit("auth", self.auth_state())
            raise LoginError(self.error)
        self._set("authenticated")
        asyncio.get_running_loop().call_later(25, self._new_invite)

    async def cancel_login(self) -> None:
        self._set("logged_out")

    async def logout(self) -> None:
        self._set("logged_out")

    async def retry(self) -> None:
        return None

    def _new_invite(self) -> None:
        if self.state != "authenticated":
            return
        sid = 76561198100000000 + len(self.invites) * 7919 + 13
        name = self.rng.choice(["Newcomer", "fresh_acc", "LuckyLuke", "Hana"])
        self.invites[sid] = {"name": name, "avatar": _avatar(name + str(sid)), "level": self.rng.randint(0, 40)}
        if self.chat.on_relationships:
            self.chat.on_relationships({sid: EFriendRelationship.RequestRecipient}, False)

    # ------------------------------------------------------------ invites
    async def fetch_invites(self) -> list[InviteBasic]:
        await asyncio.sleep(0.4)
        return [InviteBasic(steamid=sid, name=d["name"], avatar=d["avatar"], level=d["level"])
                for sid, d in self.invites.items()]

    async def invite_action(self, action: str, steamids: list[int]) -> dict[int, str | None]:
        await asyncio.sleep(0.5)
        out: dict[int, str | None] = {}
        for sid in steamids:
            d = self.invites.pop(sid, None)
            out[sid] = None if d else "Invite no longer exists"
            if d and action == "accept":
                self.chat.add_friend(sid, d["name"], d["avatar"], 1)
        return out

    async def remove_friend(self, steamid: int) -> None:
        await asyncio.sleep(0.4)
        if self.chat.friends.pop(steamid, None) is None:
            raise SteamError("You are not friends with this user")
        if self.chat.on_relationships:
            self.chat.on_relationships({steamid: EFriendRelationship.NoRelationship}, False)
        self.emit("friends", {"incremental": True})

    async def fetch_profile(self, steamid: int) -> ProfileInfo:
        await asyncio.sleep(self.rng.uniform(0.2, 0.7))
        r = random.Random(steamid)
        friend = self.chat.personas.get(steamid)
        d = self.invites.get(steamid) or (
            {"name": friend["name"], "avatar": friend["avatar"], "level": r.choice([3, 9, 14, 27, 42, 65, 110])}
            if friend else {"name": "?", "avatar": _avatar("?"), "level": 0})
        years = r.choice([0.05, 0.2, 0.6, 1, 2, 3, 5, 8, 11, 14])
        return ProfileInfo(
            steamid=steamid, name=d["name"], avatar=d["avatar"], level=d["level"],
            profile_url=f"https://steamcommunity.com/profiles/{steamid}/",
            member_since=int(time.time() - years * 365 * 86400),
            privacy=r.choice(["public"] * 6 + ["friendsonly", "private"]),
            vac_banned=r.random() < 0.12, trade_ban=r.choice(["None"] * 9 + ["Probation"]),
            limited=d["level"] == 0 and r.random() < 0.7, online_state=r.choice(["online", "offline", "in-game"]),
            location=r.choice([None, "Germany", "Russian Federation", "Brazil", "Poland", "Ukraine", "Kazakhstan"]),
        )

    async def fetch_inventory(self, steamid: int) -> Inventory:
        await self.inventory_limiter.wait()
        await asyncio.sleep(self.rng.uniform(0.3, 1.1))
        r = random.Random(steamid * 31)
        roll = r.random()
        if roll < 0.15:
            return Inventory(steamid, "private")
        if roll < 0.25:
            return Inventory(steamid, "empty")
        if roll < 0.28:
            raise SteamError("HTTP 500 from Steam (demo)")
        items = []
        richness = r.random() ** 2
        for name, price, color, type_ in ITEMS:
            if r.random() < (0.12 + richness * 0.55 if price > 50 else 0.55):
                items.append(InventoryItem(
                    market_hash_name=name, name=name, icon=_item_icon(name, color), type=type_,
                    name_color="", rarity=type_.split()[0], rarity_color=color, tradable=r.random() > 0.15,
                    marketable=True, count=r.randint(1, 6) if price < 15 else 1, price=price,
                ))
        return Inventory(steamid, "ok" if items else "empty", items=items,
                         total_count=sum(i.count for i in items) + r.randint(0, 12))

    def set_inventory_interval(self, seconds: float) -> None:
        self.inventory_limiter.interval = min(seconds, 0.5)
