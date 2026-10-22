import time

from steam_mfran.core.rules import first_match, matches
from steam_mfran.core.settings import Rule, RuleConditions

NOW = time.time()


def rec(level=10, value=100.0, inv="ok", vac=False, trade="None", limited=False, privacy="public", age_days=400):
    return {
        "level": level,
        "profile": {"level": level, "vac_banned": vac, "trade_ban": trade, "limited": limited, "privacy": privacy,
                    "member_since": NOW - age_days * 86400},
        "inventory": {"state": inv, "value": value},
    }


def test_level_and_value_ranges():
    c = RuleConditions(level_min=5, value_min=50)
    assert matches(rec(), c)
    assert not matches(rec(level=4), c)
    assert not matches(rec(value=49.9), c)
    assert not matches(rec(inv="private"), c)  # unknown value never satisfies a filter


def test_empty_inventory_counts_as_zero_value():
    assert matches(rec(inv="empty", value=None), RuleConditions(value_max=1))
    assert matches(rec(inv="empty"), RuleConditions(inventory="public"))
    assert not matches(rec(inv="ok"), RuleConditions(inventory="empty"))


def test_flags():
    assert matches(rec(vac=True), RuleConditions(vac_banned="yes"))
    assert not matches(rec(vac=False), RuleConditions(vac_banned="yes"))
    assert matches(rec(trade="Probation"), RuleConditions(trade_ban="yes"))
    assert matches(rec(limited=True), RuleConditions(limited="yes"))
    assert matches(rec(privacy="friendsonly"), RuleConditions(profile_private="yes"))


def test_account_age():
    assert matches(rec(age_days=10), RuleConditions(account_age_days_max=30))
    assert not matches(rec(age_days=10), RuleConditions(account_age_days_min=30))


def test_first_match_respects_order_and_enabled():
    rules = [
        Rule(name="off", enabled=False, action="block", conditions=RuleConditions()),
        Rule(name="bots", action="ignore", conditions=RuleConditions(level_max=0)),
        Rule(name="rich", action="accept", conditions=RuleConditions(value_min=500)),
    ]
    assert first_match(rec(level=0), rules).name == "bots"
    assert first_match(rec(value=900), rules).name == "rich"
    assert first_match(rec(value=10), rules) is None
