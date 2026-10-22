"""Persistent Steam login: password + code once, refresh token afterwards."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from ..steam.auth import CONFIRM_TYPES, AuthTokens, PendingLogin, SteamAuthenticator, token_expires_at
from ..steam.enums import EAuthSessionGuardType, EResult
from ..steam.http import COMMUNITY, EResultError, SessionExpired, SteamError, SteamHttp
from ..storage import JsonStore

log = logging.getLogger(__name__)

FRIENDLY_ERRORS = {
    EResult.InvalidPassword: "Incorrect account name or password.",
    EResult.RateLimitExceeded: "Too many login attempts. Wait a while and try again.",
    EResult.AccountLoginDeniedThrottle: "Too many login attempts. Wait a while and try again.",
    EResult.InvalidLoginAuthCode: "That Steam Guard code is not correct.",
    EResult.TwoFactorCodeMismatch: "That Steam Guard code is not correct.",
    EResult.ExpiredLoginAuthCode: "That Steam Guard code has expired.",
    EResult.Expired: "This login attempt expired. Please start again.",
    EResult.FileNotFound: "This login attempt expired. Please start again.",
    EResult.AccountNotFound: "Incorrect account name or password.",
    EResult.AccessDenied: "Steam denied access to this session.",
    EResult.Revoked: "This session was revoked. Please log in again.",
}
CODE_ERRORS = {EResult.InvalidLoginAuthCode, EResult.TwoFactorCodeMismatch, EResult.ExpiredLoginAuthCode}


class LoginError(Exception):
    pass


def friendly(exc: Exception) -> str:
    if isinstance(exc, EResultError):
        return FRIENDLY_ERRORS.get(exc.eresult, f"Steam error: {exc}")
    return str(exc) or exc.__class__.__name__


class SessionManager:
    """State machine: logged_out → authenticating → awaiting_guard → authenticated (→ expired)."""

    def __init__(self, http: SteamHttp, store: JsonStore, on_change: Callable[[], None]):
        self.http = http
        self.store = store
        self.auth = SteamAuthenticator(http)
        self.on_change = on_change
        self.data: dict[str, Any] = store.load({}) or {}
        self.state = "starting"
        self.error: str | None = None
        self.pending: PendingLogin | None = None
        self._confirm_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0

    # ------------------------------------------------------------ properties
    @property
    def steamid(self) -> int | None:
        sid = self.data.get("steamid")
        return int(sid) if sid and self.state == "authenticated" else None

    @property
    def access_token(self) -> str | None:
        return self.data.get("access_token") if self.state == "authenticated" else None

    def public(self) -> dict[str, Any]:
        guard = None
        if self.pending and self.state == "awaiting_guard":
            code_type = self.pending.code_type
            guard = {
                "code": {EAuthSessionGuardType.EmailCode: "email", EAuthSessionGuardType.DeviceCode: "device"}.get(code_type),
                "confirmation": self.pending.allows(*CONFIRM_TYPES),
                "hint": next((o.message for o in self.pending.options if o.message), ""),
            }
        return {
            "state": self.state,
            "account_name": self.data.get("account_name"),
            "steamid": str(self.data["steamid"]) if self.data.get("steamid") else None,
            "guard": guard,
            "error": self.error,
            "session_expires_at": token_expires_at(self.data.get("refresh_token")) or None,
        }

    def _set(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.error = error
        self.on_change()

    def _save(self) -> None:
        self.store.save(self.data)

    # ------------------------------------------------------------ restore
    async def restore(self) -> None:
        refresh = self.data.get("refresh_token")
        if not refresh or not self.data.get("steamid"):
            self._set("logged_out")
            return
        if token_expires_at(refresh) and token_expires_at(refresh) < time.time() + 300:
            self._set("expired", "Your saved Steam session expired. Log in again.")
            return
        access = self.data.get("access_token")
        if access and token_expires_at(access) > time.time() + 600:
            self.http.set_login_cookie(int(self.data["steamid"]), access)
            if await self._verify():
                self._set("authenticated")
                return
        try:
            await self._refresh_web_session()
            self._set("authenticated")
        except (EResultError, SessionExpired) as exc:
            log.warning("Saved session no longer works: %s", exc)
            self._set("expired", "Steam no longer accepts the saved session. Log in again.")
        except SteamError as exc:
            log.warning("Could not restore session (network?): %s", exc)
            self._set("error", f"Could not reach Steam: {exc}")

    async def _verify(self) -> bool:
        try:
            resp, body = await self.http.request("GET", f"{COMMUNITY}/chat/clientjstoken", retries=1)
            return resp.status == 200 and bool(json.loads(body or b"{}").get("logged_in"))
        except (SteamError, ValueError):
            return False

    async def _refresh_web_session(self) -> None:
        token = await self.auth.create_web_session(self.data["refresh_token"], int(self.data["steamid"]))
        self.data["access_token"] = token
        self.data["refreshed_at"] = int(time.time())
        self._last_refresh = time.time()
        self._save()

    async def ensure_fresh(self, force: bool = False) -> None:
        """Refresh the web session if it is close to expiry (or ``force``)."""
        async with self._lock:
            if self.state not in ("authenticated", "error"):
                return
            if force and time.time() - self._last_refresh < 60:
                return  # someone else refreshed moments ago
            exp = token_expires_at(self.data.get("access_token"))
            if not force and exp > time.time() + 2 * 3600:
                return
            try:
                await self._refresh_web_session()
                if self.state != "authenticated":
                    self._set("authenticated")
            except (EResultError, SessionExpired) as exc:
                log.warning("Session refresh rejected: %s", exc)
                self._set("expired", "Steam session expired. Log in again to continue.")
                raise SessionExpired(str(exc)) from exc

    async def keepalive(self) -> None:
        while True:
            await asyncio.sleep(60 if self.state == "error" else 300)
            try:
                if self.state == "error":
                    await self.restore()
                else:
                    await self.ensure_fresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Session keepalive: %s", exc)

    # ------------------------------------------------------------ login
    async def login(self, account_name: str, password: str) -> None:
        await self.cancel(silent=True)
        account_name = account_name.strip()
        self._set("authenticating")
        guard_data = self.data.get("guard_data") if self.data.get("account_name") == account_name else None
        try:
            self.pending = await self.auth.begin(account_name, password, guard_data)
        except Exception as exc:  # noqa: BLE001
            self._set("logged_out" if self.state != "expired" else "expired", friendly(exc))
            raise LoginError(friendly(exc)) from exc
        if self.pending.allows(EAuthSessionGuardType.NoGuard):
            await self._finish(await self.auth.wait_for_tokens(self.pending, timeout=30))
            return
        self._set("awaiting_guard")
        if self.pending.allows(*CONFIRM_TYPES):
            self._confirm_task = asyncio.create_task(self._wait_confirmation(self.pending))

    async def _wait_confirmation(self, pending: PendingLogin) -> None:
        try:
            tokens = await self.auth.wait_for_tokens(pending, timeout=300)
            if self.pending is pending:
                await self._finish(tokens)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if self.pending is pending and self.state == "awaiting_guard":
                self.pending = None
                self._set("logged_out", friendly(exc))

    async def submit_code(self, code: str) -> None:
        pending = self.pending
        if not pending or self.state != "awaiting_guard":
            raise LoginError("There is no login waiting for a code.")
        try:
            await self.auth.submit_code(pending, code)
        except EResultError as exc:
            if exc.eresult in CODE_ERRORS:
                self.error = friendly(exc)
                self.on_change()
                raise LoginError(friendly(exc)) from exc
            self.pending = None
            self._set("logged_out", friendly(exc))
            raise LoginError(friendly(exc)) from exc
        tokens = await self.auth.wait_for_tokens(pending, timeout=30)
        await self._finish(tokens)

    async def _finish(self, tokens: AuthTokens) -> None:
        if self._confirm_task and self._confirm_task is not asyncio.current_task():
            self._confirm_task.cancel()
        self._confirm_task = None
        self.pending = None
        self.data = {
            "account_name": tokens.account_name,
            "steamid": str(tokens.steamid),
            "refresh_token": tokens.refresh_token,
            "access_token": None,
            "guard_data": tokens.guard_data or self.data.get("guard_data"),
            "logged_in_at": int(time.time()),
        }
        try:
            await self._refresh_web_session()
        except SteamError as exc:
            self._set("logged_out", f"Logged in, but could not open a web session: {friendly(exc)}")
            raise LoginError(self.error) from exc
        log.info("Logged in to Steam as %s (%s)", tokens.account_name, tokens.steamid)
        self._set("authenticated")

    async def cancel(self, silent: bool = False) -> None:
        if self._confirm_task:
            self._confirm_task.cancel()
            self._confirm_task = None
        self.pending = None
        if not silent and self.state in ("awaiting_guard", "authenticating"):
            self._set("expired" if self.data.get("refresh_token") else "logged_out")

    async def logout(self) -> None:
        await self.cancel(silent=True)
        if self.state == "authenticated":
            try:
                await self.http.request(
                    "POST", f"{COMMUNITY}/login/logout/", data={"sessionid": self.http.session_id},
                    allow_redirects=False, retries=0,
                )
            except SteamError:
                pass
        self.http.clear_cookies()
        # Keep the account name and machine token so the next login is smoother.
        self.data = {k: v for k, v in self.data.items() if k in ("account_name", "guard_data")}
        self._save()
        self._set("logged_out")
