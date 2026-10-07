"""SteamID helpers (individual accounts in the public universe only)."""

from __future__ import annotations

import base64
import json
from typing import Any

STEAMID64_BASE = 76561197960265728
DEFAULT_AVATAR_HASH = "fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb"
AVATAR_CDN = "https://avatars.fastly.steamstatic.com"


def to_account_id(steamid64: int | str) -> int:
    return int(steamid64) & 0xFFFFFFFF


def from_account_id(account_id: int | str) -> int:
    return STEAMID64_BASE + int(account_id)


def is_individual(steamid64: int | str) -> bool:
    """True for regular user accounts (EAccountType.Individual)."""
    return (int(steamid64) >> 52) & 0xF == 1


def avatar_url(avatar_hash: bytes | str | None, size: str = "full") -> str:
    if isinstance(avatar_hash, (bytes, bytearray)):
        avatar_hash = avatar_hash.hex()
    if not avatar_hash or set(avatar_hash) == {"0"}:
        avatar_hash = DEFAULT_AVATAR_HASH
    suffix = {"full": "_full", "medium": "_medium", "small": ""}[size]
    return f"{AVATAR_CDN}/{avatar_hash}{suffix}.jpg"


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying it (Steam tokens are opaque to us)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))
