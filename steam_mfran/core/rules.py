"""Automation rules: decide what to do with an analysed friend request."""

from __future__ import annotations

import time
from typing import Any

from .settings import Rule, RuleConditions


def _tri(expected: str, actual: bool | None) -> bool:
    if expected == "any":
        return True
    if actual is None:
        return False
    return actual == (expected == "yes")


def matches(record: dict[str, Any], cond: RuleConditions, now: float | None = None) -> bool:
    """True when every condition holds. Unknown data never satisfies a filter."""
    profile = record.get("profile") or {}
    inv = record.get("inventory") or {}
    level = profile.get("level", record.get("level"))

    if cond.level_min is not None and (level is None or level < cond.level_min):
        return False
    if cond.level_max is not None and (level is None or level > cond.level_max):
        return False

    inv_state = inv.get("state")
    if cond.inventory != "any":
        wanted = {"public": ("ok", "empty"), "private": ("private",), "empty": ("empty",)}[cond.inventory]
        if inv_state not in wanted:
            return False
    value = inv.get("value") if inv_state in ("ok", "empty") else None
    if inv_state == "empty":
        value = 0.0
    if cond.value_min is not None and (value is None or value < cond.value_min):
        return False
    if cond.value_max is not None and (value is None or value > cond.value_max):
        return False

    if not _tri(cond.vac_banned, profile.get("vac_banned")):
        return False
    trade_ban = profile.get("trade_ban")
    if not _tri(cond.trade_ban, None if trade_ban is None else trade_ban.lower() != "none"):
        return False
    if not _tri(cond.limited, profile.get("limited")):
        return False
    privacy = profile.get("privacy")
    if not _tri(cond.profile_private, None if privacy is None else privacy != "public"):
        return False

    if cond.account_age_days_min is not None or cond.account_age_days_max is not None:
        since = profile.get("member_since")
        if not since:
            return False
        age = ((now or time.time()) - since) / 86400
        if cond.account_age_days_min is not None and age < cond.account_age_days_min:
            return False
        if cond.account_age_days_max is not None and age > cond.account_age_days_max:
            return False
    return True


def first_match(record: dict[str, Any], rules: list[Rule]) -> Rule | None:
    for rule in rules:
        if rule.enabled and matches(record, rule.conditions):
            return rule
    return None
