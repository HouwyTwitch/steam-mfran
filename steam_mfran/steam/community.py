"""steamcommunity.com endpoints: friend invites, profiles, chat uploads."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp
from bs4 import BeautifulSoup

from .http import COMMUNITY, RateLimited, SessionExpired, SteamError, SteamHttp
from .ids import from_account_id, to_account_id

log = logging.getLogger(__name__)

STEAMID_RE = re.compile(r"^7656\d{13}$")
PROFILE_DATA_RE = re.compile(r"g_rgProfileData\s*=\s*(\{.*?\});", re.S)
ACCEPT_JS_RE = re.compile(r"FriendAccept\(\s*'(7656\d{13})'\s*,\s*'(accept|ignore|block|cancel)'")


@dataclass
class InviteBasic:
    steamid: int
    name: str = ""
    avatar: str = ""
    level: int | None = None
    profile_url: str = ""
    incoming: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steamid"] = str(self.steamid)
        return d


@dataclass
class ProfileInfo:
    steamid: int
    name: str = ""
    avatar: str = ""
    level: int | None = None
    profile_url: str = ""
    member_since: int | None = None  # unix timestamp
    privacy: str | None = None  # public / friendsonly / private
    vac_banned: bool | None = None
    trade_ban: str | None = None  # None / Probation / Banned
    limited: bool | None = None
    online_state: str | None = None
    location: str | None = None
    real_name: str | None = None
    headline: str | None = None
    favorite_badge: str | None = None
    in_game: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steamid"] = str(self.steamid)
        return d


# ---------------------------------------------------------------------------
# Parsers (pure functions, unit tested)
# ---------------------------------------------------------------------------

def _row_direction(row) -> bool:
    """Return True for an incoming invite row, False for one we sent."""
    for parent in row.parents:
        pid = (parent.get("id") or "") if hasattr(parent, "get") else ""
        if "sent" in pid.lower():
            return False
    text = str(row)
    if "'cancel'" in text and "'accept'" not in text:
        return False
    return True


def _int_or_none(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group()) if m else None


def parse_pending_page(html: str) -> tuple[list[InviteBasic], dict[str, Any] | None]:
    """Parse ``/friends/pending``. Returns (invites, g_rgProfileData)."""
    if re.search(r"g_steamID\s*=\s*false", html):
        raise SessionExpired("Steam community session is not logged in")

    profile_data = None
    m = PROFILE_DATA_RE.search(html)
    if m:
        try:
            profile_data = json.loads(m.group(1))
        except ValueError:
            profile_data = None

    soup = BeautifulSoup(html, "html.parser")
    invites: dict[int, InviteBasic] = {}
    for row in soup.select("[data-steamid]"):
        raw = (row.get("data-steamid") or "").strip()
        if not STEAMID_RE.match(raw):
            continue
        # Skip nested data-steamid elements already covered by their row.
        if any(STEAMID_RE.match((p.get("data-steamid") or "")) for p in row.parents if hasattr(p, "get")):
            continue
        sid = int(raw)
        name_el = row.select_one(".invite_block_name a, .invite_block_name, a.linkTitle, .friend_block_content")
        name = ""
        if name_el:
            name = name_el.get_text(" ", strip=True).split("\n")[0].strip()
        avatar = ""
        for img in row.select("img"):
            src = img.get("src") or ""
            if "avatar" in src:
                avatar = src.replace("_medium.jpg", "_full.jpg")
                break
        level_el = row.select_one(".friendPlayerLevelNum")
        link = row.select_one("a[href*='steamcommunity.com/id/'], a[href*='steamcommunity.com/profiles/']")
        invites[sid] = InviteBasic(
            steamid=sid,
            name=name,
            avatar=avatar,
            level=_int_or_none(level_el.get_text()) if level_el else None,
            profile_url=(link.get("href") if link else "") or f"{COMMUNITY}/profiles/{sid}",
            incoming=_row_direction(row),
        )

    # Fallback when markup changed but the inline JS actions are still there.
    if not invites:
        for sid_s, action in ACCEPT_JS_RE.findall(html):
            sid = int(sid_s)
            inv = invites.setdefault(sid, InviteBasic(steamid=sid, profile_url=f"{COMMUNITY}/profiles/{sid}"))
            if action == "cancel":
                inv.incoming = False
    return list(invites.values()), profile_data


_MEMBER_SINCE_FORMATS = ("%B %d, %Y", "%d %B, %Y", "%d %B %Y", "%B %d %Y", "%b %d, %Y")


def parse_member_since(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    for fmt in _MEMBER_SINCE_FORMATS:
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def parse_profile_xml(steamid: int, text: str) -> ProfileInfo:
    info = ProfileInfo(steamid=steamid)
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        info.errors.append("profile xml unavailable")
        return info
    if root.tag == "response":
        info.errors.append((root.findtext("error") or "profile not found").strip())
        return info

    def t(tag: str) -> str | None:
        v = root.findtext(tag)
        return v.strip() if v is not None and v.strip() else None

    info.name = t("steamID") or ""
    info.avatar = t("avatarFull") or ""
    info.privacy = t("privacyState")
    info.online_state = t("onlineState")
    vac = t("vacBanned")
    info.vac_banned = None if vac is None else vac == "1"
    info.trade_ban = t("tradeBanState")
    limited = t("isLimitedAccount")
    info.limited = None if limited is None else limited == "1"
    info.member_since = parse_member_since(t("memberSince"))
    info.location = t("location")
    info.real_name = t("realname")
    info.headline = t("headline")
    custom = t("customURL")
    info.profile_url = f"{COMMUNITY}/id/{custom}/" if custom else f"{COMMUNITY}/profiles/{steamid}/"
    return info


def merge_miniprofile(info: ProfileInfo, data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    if data.get("level") is not None:
        try:
            info.level = int(data["level"])
        except (TypeError, ValueError):
            pass
    info.name = data.get("persona_name") or info.name
    info.avatar = data.get("avatar_url") or info.avatar
    badge = data.get("favorite_badge")
    if isinstance(badge, dict):
        info.favorite_badge = badge.get("name")
    game = data.get("in_game")
    if isinstance(game, dict):
        info.in_game = game.get("name")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Community:
    def __init__(self, http: SteamHttp, steamid: Callable[[], int | None]):
        self.http = http
        self._steamid = steamid
        self.profile_base: str | None = None  # e.g. https://steamcommunity.com/id/name/
        self.profile_data: dict[str, Any] | None = None

    @property
    def steamid(self) -> int:
        sid = self._steamid()
        if not sid:
            raise SessionExpired("not logged in")
        return sid

    def _check_logged_in(self, resp: aiohttp.ClientResponse) -> None:
        path = resp.url.path
        if resp.status in (401, 403) or path.startswith("/login"):
            raise SessionExpired("Steam community session expired")

    # ------------------------------------------------------------ invites
    async def get_pending_invites(self) -> list[InviteBasic]:
        resp, body = await self.http.request("GET", f"{COMMUNITY}/profiles/{self.steamid}/friends/pending")
        self._check_logged_in(resp)
        if resp.status >= 400:
            raise SteamError(f"pending invites page returned HTTP {resp.status}")
        invites, profile_data = parse_pending_page(body.decode("utf-8", errors="replace"))
        url = str(resp.url)
        if "/friends/pending" in url:
            self.profile_base = url.split("/friends/pending")[0].rstrip("/") + "/"
        if profile_data:
            self.profile_data = profile_data
            if profile_data.get("url"):
                self.profile_base = profile_data["url"].rstrip("/") + "/"
        return invites

    async def friend_action(self, action: str, steamids: list[int]) -> dict[int, str | None]:
        """Accept / ignore / block invites in bulk. Returns {steamid: error or None}."""
        if action not in ("accept", "ignore", "block"):
            raise ValueError(f"unknown action {action}")
        results: dict[int, str | None] = {}
        base = self.profile_base or f"{COMMUNITY}/profiles/{self.steamid}/"
        for i in range(0, len(steamids), 30):
            chunk = steamids[i:i + 30]
            data = [
                ("sessionid", self.http.session_id),
                ("steamid", str(self.steamid)),
                ("ajax", "1"),
                ("action", action),
            ] + [("steamids[]", str(s)) for s in chunk]
            try:
                resp, body = await self.http.request(
                    "POST",
                    f"{base}friends/action",
                    data=data,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Origin": COMMUNITY,
                        "Referer": f"{base}friends/pending",
                    },
                    allow_redirects=False,
                )
                self._check_logged_in(resp)
                payload = json.loads(body or b"{}") if resp.status == 200 else {}
            except (ValueError, SteamError) as exc:
                if isinstance(exc, (SessionExpired, RateLimited)):
                    raise
                log.warning("Bulk %s failed (%s); falling back to single requests", action, exc)
                payload = {}
            if payload.get("success"):
                failed = {int(x) for x in payload.get("failed_invites") or [] if str(x).isdigit()}
                for s in chunk:
                    results[s] = "Steam refused this invite" if s in failed else None
                continue
            for s in chunk:
                results[s] = await self._single_action(action, s)
        return results

    async def _single_action(self, action: str, steamid: int) -> str | None:
        endpoints = {
            "accept": ("AddFriendAjax", {"accept_invite": "1"}),
            "ignore": ("IgnoreFriendInviteAjax", {}),
            "block": ("BlockUserAjax", {"block": "1"}),
        }
        path, extra = endpoints[action]
        data = {"sessionID": self.http.session_id, "steamid": str(steamid), **extra}
        try:
            resp, body = await self.http.request(
                "POST", f"{COMMUNITY}/actions/{path}", data=data,
                headers={"X-Requested-With": "XMLHttpRequest", "Origin": COMMUNITY, "Referer": f"{COMMUNITY}/"},
            )
            self._check_logged_in(resp)
            if resp.status != 200:
                return f"HTTP {resp.status}"
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                return None  # some of these endpoints reply with an empty 200
            if isinstance(payload, dict) and payload.get("success") in (False, 0):
                return payload.get("error_text") or "Steam refused the request"
            return None
        except SessionExpired:
            raise
        except SteamError as exc:
            return str(exc)

    # ------------------------------------------------------------ profiles
    async def get_miniprofile(self, steamid: int) -> dict[str, Any]:
        return await self.http.get_json(
            f"{COMMUNITY}/miniprofile/{to_account_id(steamid)}/json/",
            params={"origin": COMMUNITY + "/"},
            headers={"Accept": "application/json, text/plain, */*", "Referer": f"{COMMUNITY}/chat/"},
        ) or {}

    async def get_profile(self, steamid: int) -> ProfileInfo:
        info = ProfileInfo(steamid=steamid)
        try:
            resp, body = await self.http.request("GET", f"{COMMUNITY}/profiles/{steamid}/", params={"xml": "1"})
            if resp.status == 200:
                info = parse_profile_xml(steamid, body.decode("utf-8", errors="replace"))
            else:
                info.errors.append(f"profile xml HTTP {resp.status}")
        except RateLimited:
            raise
        except SteamError as exc:
            info.errors.append(str(exc))
        try:
            merge_miniprofile(info, await self.get_miniprofile(steamid))
        except RateLimited:
            raise
        except (SteamError, ValueError) as exc:
            info.errors.append(f"miniprofile: {exc}")
        if not info.profile_url:
            info.profile_url = f"{COMMUNITY}/profiles/{steamid}/"
        return info

    # ------------------------------------------------------------ chat
    async def get_client_js_token(self) -> dict[str, Any]:
        resp, body = await self.http.request(
            "GET", f"{COMMUNITY}/chat/clientjstoken", headers={"Referer": f"{COMMUNITY}/chat/"}
        )
        self._check_logged_in(resp)
        try:
            data = json.loads(body or b"{}")
        except ValueError as exc:
            raise SteamError("clientjstoken returned non-JSON") from exc
        if not data.get("logged_in"):
            raise SessionExpired("clientjstoken says we are not logged in")
        if not data.get("token") or not data.get("account_name"):
            raise SteamError("clientjstoken response is missing fields")
        return data

    async def upload_chat_image(
        self,
        friend_steamid: int,
        content: bytes,
        mime: str,
        width: int,
        height: int,
        spoiler: bool = False,
    ) -> str:
        """Upload an image to Steam chat; Steam posts it to the conversation itself."""
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(mime, "png")
        filename = f"{int(time.time() * 1000)}_image.{ext}"
        sha = hashlib.sha1(content).hexdigest()
        headers = {"Referer": f"{COMMUNITY}/chat/", "Origin": COMMUNITY}

        form = aiohttp.FormData()
        for k, v in {
            "sessionid": self.http.session_id, "l": "english", "file_size": str(len(content)),
            "file_name": filename, "file_sha": sha, "file_image_width": str(width),
            "file_image_height": str(height), "file_type": mime,
        }.items():
            form.add_field(k, v)
        resp, body = await self.http.request(
            "POST", f"{COMMUNITY}/chat/beginfileupload/", params={"l": "english"}, data=form, headers=headers
        )
        self._check_logged_in(resp)
        begin = json.loads(body or b"{}")
        if begin.get("success") != 1 or not isinstance(begin.get("result"), dict):
            raise SteamError(f"Steam refused the upload ({begin.get('message') or begin.get('success')})")
        result = begin["result"]
        upload_url = ("https" if result.get("use_https") else "http") + "://" + result["url_host"] + result["url_path"]
        put_headers = {h["name"]: h["value"] for h in result.get("request_headers", [])}
        put_resp, _ = await self.http.request("PUT", upload_url, data=content, headers=put_headers)
        if put_resp.status >= 400:
            raise SteamError(f"image upload failed with HTTP {put_resp.status}")

        form = aiohttp.FormData()
        for k, v in {
            "sessionid": self.http.session_id, "l": "english", "file_name": filename, "file_sha": sha,
            "success": "1", "ugcid": str(result["ugcid"]), "file_type": mime,
            "file_image_width": str(width), "file_image_height": str(height),
            "timestamp": str(begin.get("timestamp", "")), "hmac": str(begin.get("hmac", "")),
            "friend_steamid": str(friend_steamid), "spoiler": "1" if spoiler else "0",
        }.items():
            form.add_field(k, v)
        resp, body = await self.http.request(
            "POST", f"{COMMUNITY}/chat/commitfileupload/", data=form, headers=headers
        )
        commit = json.loads(body or b"{}")
        inner = commit.get("result") or {}
        if commit.get("success") != 1 or inner.get("success") != 1:
            raise SteamError("Steam did not accept the uploaded image")
        url = (inner.get("details") or {}).get("url")
        if not url:
            raise SteamError("Steam did not return the image URL")
        return url


def steamid_from_any(value: str | int) -> int:
    """Accept a SteamID64 or an account id."""
    v = int(value)
    return v if v > 0xFFFFFFFF else from_account_id(v)
