"""Steam's IAuthenticationService login flow (the one steamcommunity.com uses).

Flow (mirrors DoctorMcKay/node-steam-session for the WebBrowser platform):

1. ``GetPasswordRSAPublicKey`` → RSA-encrypt the password.
2. ``BeginAuthSessionViaCredentials`` → client_id/request_id + allowed guard types.
3. Either submit a Steam Guard code (``UpdateAuthSessionWithSteamGuardCode``)
   or wait for the user to approve the login in the Steam mobile app.
4. ``PollAuthSessionStatus`` until it hands back a refresh token.
5. ``login.steampowered.com/jwt/finalizelogin`` with ``nonce=<refresh token>``
   gives a list of per-domain "transfer" URLs that set ``steamLoginSecure``.

Step 5 can be repeated at any time while the refresh token is valid, which is
how we resume a session on startup without asking for the password again.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import unquote

import rsa

from .enums import EAuthSessionGuardType, EAuthTokenPlatformType, EResult, ESessionPersistence
from .http import API_HEADERS, COMMUNITY, LOGIN, EResultError, SteamError, SteamHttp
from .ids import decode_jwt

log = logging.getLogger(__name__)

CODE_TYPES = (EAuthSessionGuardType.EmailCode, EAuthSessionGuardType.DeviceCode)
CONFIRM_TYPES = (EAuthSessionGuardType.DeviceConfirmation, EAuthSessionGuardType.EmailConfirmation)


@dataclass
class GuardOption:
    type: EAuthSessionGuardType
    message: str = ""


@dataclass
class PendingLogin:
    account_name: str
    client_id: int
    request_id: bytes
    steamid: int
    interval: float
    options: list[GuardOption] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def allows(self, *types: EAuthSessionGuardType) -> bool:
        return any(o.type in types for o in self.options)

    @property
    def code_type(self) -> EAuthSessionGuardType | None:
        for t in CODE_TYPES:
            if self.allows(t):
                return t
        return None


@dataclass
class AuthTokens:
    steamid: int
    account_name: str
    refresh_token: str
    access_token: str | None = None
    guard_data: str | None = None


def token_expires_at(token: str | None) -> int:
    if not token:
        return 0
    try:
        return int(decode_jwt(token).get("exp", 0))
    except (ValueError, json.JSONDecodeError):
        return 0


class SteamAuthenticator:
    def __init__(self, http: SteamHttp):
        self.http = http

    # ------------------------------------------------------------------ login
    async def begin(self, account_name: str, password: str, guard_data: str | None = None) -> PendingLogin:
        key = await self.http.call_service(
            "Authentication",
            "GetPasswordRSAPublicKey",
            "CAuthentication_GetPasswordRSAPublicKey_Request",
            {"account_name": account_name},
            "CAuthentication_GetPasswordRSAPublicKey_Response",
            http_method="GET",
        )
        try:
            pub = rsa.PublicKey(int(key["publickey_mod"], 16), int(key["publickey_exp"], 16))
            timestamp = int(key["timestamp"])
        except (KeyError, ValueError) as exc:
            raise SteamError("Steam did not return an RSA key for this account") from exc
        encrypted = base64.b64encode(rsa.encrypt(password.encode("utf-8"), pub)).decode()

        request = {
            "account_name": account_name,
            "encrypted_password": encrypted,
            "encryption_timestamp": timestamp,
            "remember_login": True,
            "persistence": ESessionPersistence.Persistent,
            "website_id": "Community",
            "device_details": {
                "device_friendly_name": self.http.user_agent,
                "platform_type": EAuthTokenPlatformType.WebBrowser,
            },
        }
        if guard_data:
            request["guard_data"] = guard_data
        resp = await self.http.call_service(
            "Authentication",
            "BeginAuthSessionViaCredentials",
            "CAuthentication_BeginAuthSessionViaCredentials_Request",
            request,
            "CAuthentication_BeginAuthSessionViaCredentials_Response",
        )
        if not resp.get("client_id"):
            raise SteamError(resp.get("extended_error_message") or "Steam refused to start a login session")
        request_id = resp.get("request_id") or b""
        if isinstance(request_id, str):  # JSON responses carry bytes as base64
            request_id = base64.b64decode(request_id)
        options = [
            GuardOption(EAuthSessionGuardType(int(c.get("confirmation_type", 0))), c.get("associated_message", ""))
            for c in resp.get("allowed_confirmations", [])
        ]
        pending = PendingLogin(
            account_name=account_name,
            client_id=int(resp["client_id"]),
            request_id=request_id,
            steamid=int(resp.get("steamid") or 0),
            interval=float(resp.get("interval") or 5.0),
            options=options,
        )
        log.info("Login session started; Steam allows: %s", [o.type.name for o in options])

        # E-mail Steam Guard: a remembered machine token can skip the code, and
        # this same call is what makes Steam actually send the e-mail.
        if pending.allows(EAuthSessionGuardType.EmailCode) and pending.allows(EAuthSessionGuardType.MachineToken):
            if await self._check_device(pending, guard_data):
                pending.options = [GuardOption(EAuthSessionGuardType.NoGuard)]
        return pending

    async def _check_device(self, pending: PendingLogin, guard_data: str | None) -> bool:
        headers = dict(API_HEADERS)
        if guard_data:
            headers["Cookie"] = f"steamMachineAuth{pending.steamid}={guard_data}"
        try:
            resp, body = await self.http.request(
                "POST",
                f"{LOGIN}/jwt/checkdevice",
                data={"clientid": str(pending.client_id), "steamid": str(pending.steamid)},
                headers=headers,
            )
            result = json.loads(body or b"{}").get("result")
            return result == EResult.OK
        except (SteamError, ValueError) as exc:
            log.debug("checkdevice failed: %s", exc)
            return False

    async def submit_code(self, pending: PendingLogin, code: str) -> None:
        code_type = pending.code_type
        if code_type is None:
            raise SteamError("This login does not accept a Steam Guard code")
        await self.http.call_service(
            "Authentication",
            "UpdateAuthSessionWithSteamGuardCode",
            "CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request",
            {
                "client_id": pending.client_id,
                "steamid": pending.steamid,
                "code": code.strip().upper() if code_type == EAuthSessionGuardType.DeviceCode else code.strip(),
                "code_type": code_type,
            },
            "CAuthentication_UpdateAuthSessionWithSteamGuardCode_Response",
        )

    async def poll(self, pending: PendingLogin) -> AuthTokens | None:
        resp = await self.http.call_service(
            "Authentication",
            "PollAuthSessionStatus",
            "CAuthentication_PollAuthSessionStatus_Request",
            {"client_id": pending.client_id, "request_id": pending.request_id},
            "CAuthentication_PollAuthSessionStatus_Response",
        )
        if resp.get("new_client_id"):
            pending.client_id = int(resp["new_client_id"])
        if not resp.get("refresh_token"):
            return None
        refresh = resp["refresh_token"]
        steamid = pending.steamid or int(decode_jwt(refresh)["sub"])
        return AuthTokens(
            steamid=steamid,
            account_name=resp.get("account_name") or pending.account_name,
            refresh_token=refresh,
            access_token=resp.get("access_token") or None,
            guard_data=resp.get("new_guard_data") or None,
        )

    async def wait_for_tokens(self, pending: PendingLogin, timeout: float = 300) -> AuthTokens:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tokens = await self.poll(pending)
            if tokens:
                return tokens
            await asyncio.sleep(max(1.0, pending.interval))
        raise SteamError("Timed out waiting for Steam Guard confirmation")

    # ------------------------------------------------------------- web session
    async def create_web_session(self, refresh_token: str, steamid: int) -> str:
        """Exchange the refresh token for fresh ``steamLoginSecure`` cookies.

        Returns the community access token (the JWT inside ``steamLoginSecure``).
        """
        session_id = self.http.session_id
        self.http.jar.clear(lambda morsel: morsel.key == "steamLoginSecure")
        resp, body = await self.http.request(
            "POST",
            f"{LOGIN}/jwt/finalizelogin",
            data={"nonce": refresh_token, "sessionid": session_id, "redir": f"{COMMUNITY}/login/home/?goto="},
            headers=API_HEADERS,
        )
        try:
            data = json.loads(body or b"null")
        except ValueError as exc:
            raise SteamError(f"finalizelogin returned non-JSON (HTTP {resp.status})") from exc
        if not isinstance(data, dict):
            raise SteamError("finalizelogin returned an empty response")
        if data.get("error"):
            raise EResultError(int(data["error"]), f"Steam rejected the saved session ({EResult.name_of(int(data['error']))})")
        transfers = data.get("transfer_info") or []
        if not transfers:
            raise SteamError("finalizelogin response had no transfer_info")
        sid = str(data.get("steamID") or steamid)

        async def transfer(url: str, params: dict) -> None:
            last: Exception | None = None
            for _ in range(4):
                try:
                    r, b = await self.http.request("POST", url, data={**{k: str(v) for k, v in params.items()}, "steamID": sid})
                    if r.status >= 400:
                        raise SteamError(f"transfer {url} -> HTTP {r.status}")
                    try:
                        result = json.loads(b or b"{}").get("result", 1)
                    except ValueError:
                        result = 1
                    if result != EResult.OK:
                        raise EResultError(result)
                    return
                except SteamError as exc:
                    last = exc
                    await asyncio.sleep(0.6)
            raise last or SteamError(f"transfer {url} failed")

        results = await asyncio.gather(
            *(transfer(t["url"], t.get("params") or {}) for t in transfers), return_exceptions=True
        )
        for t, res in zip(transfers, results):
            if isinstance(res, Exception):
                log.warning("Session transfer to %s failed: %s", t.get("url"), res)

        cookie = self.http.get_cookie("steamcommunity.com", "steamLoginSecure")
        if not cookie:
            raise SteamError("Steam did not set a steamcommunity.com login cookie")
        access_token = unquote(cookie).split("||", 1)[-1]
        # One sessionid shared by every domain keeps CSRF checks simple.
        for domain in ("steamcommunity.com", "store.steampowered.com", "help.steampowered.com"):
            self.http.set_cookie(domain, "sessionid", session_id)
        return access_token
