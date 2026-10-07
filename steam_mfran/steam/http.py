"""Shared aiohttp session with Steam cookies, retries and rate limiting."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from typing import Any

import aiohttp
from yarl import URL

from . import proto
from .enums import EResult

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
COMMUNITY = "https://steamcommunity.com"
STORE = "https://store.steampowered.com"
WEBAPI = "https://api.steampowered.com"
LOGIN = "https://login.steampowered.com"

# Headers Steam's own web login sends to cross-site API calls
API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Origin": COMMUNITY,
    "Referer": COMMUNITY + "/",
}

COOKIE_DOMAINS = ("steamcommunity.com", "store.steampowered.com", "help.steampowered.com")


class SteamError(Exception):
    """Base class for Steam-side failures."""


class EResultError(SteamError):
    def __init__(self, eresult: int, message: str | None = None):
        self.eresult = int(eresult)
        super().__init__(message or EResult.name_of(self.eresult))


class RateLimited(SteamError):
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__("Steam rate limit hit (HTTP 429)")


class SessionExpired(SteamError):
    """The web session cookies are no longer accepted."""


class RateLimiter:
    """Async limiter enforcing a minimum interval between calls."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = time.monotonic() + self.interval

    def penalize(self, seconds: float) -> None:
        self._next = max(self._next, time.monotonic() + seconds)


class SteamHttp:
    def __init__(self, proxy: str | None = None, user_agent: str = USER_AGENT):
        self.proxy = proxy or None
        self.user_agent = user_agent
        self._jar: aiohttp.CookieJar | None = None
        self._session: aiohttp.ClientSession | None = None

    @property
    def jar(self) -> aiohttp.CookieJar:
        # Created lazily: aiohttp wants a running event loop at construction.
        if self._jar is None:
            self._jar = aiohttp.CookieJar()
        return self._jar

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=self.jar,
                headers={"User-Agent": self.user_agent, "Accept-Language": "en-US,en;q=0.9"},
                timeout=aiohttp.ClientTimeout(total=40, connect=15),
                trust_env=True,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ cookies
    def set_cookie(self, domain: str, name: str, value: str) -> None:
        self.jar.update_cookies({name: value}, URL(f"https://{domain}/"))

    def get_cookie(self, domain: str, name: str) -> str | None:
        for cookie in self.jar.filter_cookies(URL(f"https://{domain}/")).values():
            if cookie.key == name:
                return cookie.value
        return None

    def clear_cookies(self) -> None:
        self.jar.clear()

    @property
    def session_id(self) -> str:
        """The ``sessionid`` CSRF cookie Steam expects echoed back in POST bodies."""
        sid = self.get_cookie("steamcommunity.com", "sessionid")
        if not sid:
            sid = secrets.token_hex(12)
            for domain in COOKIE_DOMAINS:
                self.set_cookie(domain, "sessionid", sid)
        return sid

    def set_login_cookie(self, steamid: int, access_token: str) -> None:
        value = f"{steamid}%7C%7C{access_token}"
        for domain in COOKIE_DOMAINS:
            self.set_cookie(domain, "steamLoginSecure", value)
        self.session_id  # ensure a sessionid exists

    # ------------------------------------------------------------------ requests
    async def request(
        self,
        method: str,
        url: str,
        *,
        retries: int = 2,
        allow_redirects: bool = True,
        **kwargs: Any,
    ) -> tuple[aiohttp.ClientResponse, bytes]:
        """Perform a request, retrying transient network/5xx errors.

        Returns the (already released) response and its body.
        """
        if self.proxy:
            kwargs.setdefault("proxy", self.proxy)
        delay = 1.5
        for attempt in range(retries + 1):
            try:
                async with self.session.request(
                    method, url, allow_redirects=allow_redirects, **kwargs
                ) as resp:
                    body = await resp.read()
                    if resp.status == 429:
                        ra = resp.headers.get("Retry-After")
                        raise RateLimited(float(ra) if ra and ra.isdigit() else None)
                    if resp.status >= 500 and attempt < retries:
                        log.debug("%s %s -> %s, retrying", method, url, resp.status)
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return resp, body
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                if attempt >= retries:
                    raise SteamError(f"network error talking to {URL(url).host}: {exc}") from exc
                log.debug("%s %s failed (%s), retrying", method, url, exc)
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp, body = await self.request("GET", url, **kwargs)
        if resp.status >= 400:
            raise SteamError(f"HTTP {resp.status} from {url}")
        return json.loads(body) if body else None

    # ------------------------------------------------------------------ webapi
    async def call_service(
        self,
        interface: str,
        method: str,
        request_schema: str,
        request: dict[str, Any],
        response_schema: str,
        *,
        http_method: str = "POST",
        access_token: str | None = None,
        version: int = 1,
    ) -> dict[str, Any]:
        """Call a unified (protobuf) WebAPI service method like Steam's own web pages do."""
        url = f"{WEBAPI}/I{interface}Service/{method}/v{version}/"
        encoded = base64.b64encode(proto.encode(request_schema, request)).decode()
        params: dict[str, str] = {}
        if access_token:
            params["access_token"] = access_token
        kwargs: dict[str, Any] = {"headers": API_HEADERS}
        if http_method == "GET":
            params["input_protobuf_encoded"] = encoded
        else:
            kwargs["data"] = {"input_protobuf_encoded": encoded}
        resp, body = await self.request(http_method, url, params=params, **kwargs)
        if resp.status in (401, 403):
            raise SessionExpired(f"WebAPI {interface}/{method} returned HTTP {resp.status}")
        if resp.status >= 400:
            raise SteamError(f"WebAPI {interface}/{method} returned HTTP {resp.status}")
        eresult = resp.headers.get("x-eresult")
        if eresult is not None and int(eresult) != EResult.OK:
            raise EResultError(int(eresult), resp.headers.get("x-error_message"))
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            data = json.loads(body or b"{}")
            return data.get("response", data) if isinstance(data, dict) else {}
        return proto.decode(response_schema, body)
