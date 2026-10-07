"""Live Steam backend: wires the Steam modules together behind one facade.

:class:`~steam_mfran.core.demo.DemoBackend` implements the same surface with
fake data so the UI can be explored without a Steam account.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from ..steam.chat import LiveChat
from ..steam.community import Community, InviteBasic, ProfileInfo
from ..steam.http import RateLimiter, SessionExpired, SteamHttp
from ..steam.inventory import Inventory, fetch_cs2_inventory
from ..steam.pricing import PriceService
from ..storage import JsonStore
from .session import SessionManager

log = logging.getLogger(__name__)
T = TypeVar("T")


class LiveBackend:
    demo = False

    def __init__(self, data_dir: Path, emit: Callable[[str, dict[str, Any]], None], proxy: str | None = None,
                 price_provider: str = "csgotrader", inventory_interval: float = 4.0):
        self.emit = emit
        self.http = SteamHttp(proxy=proxy)
        self.session = SessionManager(self.http, JsonStore(data_dir / "session.json", private=True), self._auth_changed)
        self.community = Community(self.http, lambda: self.session.steamid)
        self.prices = PriceService(self.http, data_dir / "cache", price_provider)
        self.inventory_limiter = RateLimiter(inventory_interval)
        self.profile_limiter = RateLimiter(0.6)
        self.chat = LiveChat(
            self.http, self.community,
            my_steamid=lambda: self.session.steamid,
            access_token=lambda: self.session.access_token,
            refresh_session=lambda: self.session.ensure_fresh(force=True),
            emit=emit,
        )
        self.on_auth_change: Callable[[], None] | None = None
        self._keepalive: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.session.restore()
        self._keepalive = asyncio.create_task(self.session.keepalive(), name="session-keepalive")

    async def stop(self) -> None:
        if self._keepalive:
            self._keepalive.cancel()
        await self.chat.stop()
        await self.http.close()

    def _auth_changed(self) -> None:
        if self.session.state == "authenticated":
            self.chat.start()
        elif self.session.state in ("logged_out", "expired"):
            asyncio.ensure_future(self.chat.cm.stop())
        self.emit("auth", self.auth_state())
        if self.on_auth_change:
            self.on_auth_change()

    # ------------------------------------------------------------ auth
    def auth_state(self) -> dict[str, Any]:
        return self.session.public()

    @property
    def my_steamid(self) -> int | None:
        return self.session.steamid

    async def login(self, username: str, password: str) -> None:
        await self.session.login(username, password)

    async def submit_code(self, code: str) -> None:
        await self.session.submit_code(code)

    async def cancel_login(self) -> None:
        await self.session.cancel()

    async def logout(self) -> None:
        await self.chat.stop()
        await self.session.logout()

    async def retry(self) -> None:
        if self.session.state in ("error", "starting"):
            await self.session.restore()

    async def _with_session(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn``; on an expired web session refresh once and retry."""
        try:
            return await fn()
        except SessionExpired:
            await self.session.ensure_fresh(force=True)
            return await fn()

    # ------------------------------------------------------------ invites
    async def fetch_invites(self) -> list[InviteBasic]:
        return await self._with_session(self.community.get_pending_invites)

    async def invite_action(self, action: str, steamids: list[int]) -> dict[int, str | None]:
        return await self._with_session(lambda: self.community.friend_action(action, steamids))

    async def fetch_profile(self, steamid: int) -> ProfileInfo:
        await self.profile_limiter.wait()
        return await self.community.get_profile(steamid)

    async def fetch_inventory(self, steamid: int) -> Inventory:
        inv = await fetch_cs2_inventory(self.http, steamid, self.inventory_limiter)
        await self.prices.price_inventory(inv)
        return inv

    def set_inventory_interval(self, seconds: float) -> None:
        self.inventory_limiter.interval = seconds
