"""User settings persisted to ``settings.json``."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..storage import JsonStore

Tri = Literal["any", "yes", "no"]


class RuleConditions(BaseModel):
    level_min: int | None = None
    level_max: int | None = None
    value_min: float | None = None  # USD
    value_max: float | None = None  # USD
    inventory: Literal["any", "public", "private", "empty"] = "any"
    vac_banned: Tri = "any"
    trade_ban: Tri = "any"
    limited: Tri = "any"
    account_age_days_min: int | None = None
    account_age_days_max: int | None = None
    profile_private: Tri = "any"


class Rule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "New rule"
    enabled: bool = True
    action: Literal["accept", "ignore", "block"] = "accept"
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    message: str | None = None  # sent after an automatic accept


class Settings(BaseModel):
    price_provider: str = "csgotrader"
    currency: str = "USD"
    theme: Literal["system", "light", "dark"] = "system"
    accent: str | None = None  # hex seed colour; None = derive from avatar
    auto_analyze: bool = True
    invites_poll_seconds: int = 90
    inventory_interval_seconds: float = 4.0
    analysis_workers: int = 2
    inventory_cache_hours: float = 6
    broadcast_interval_seconds: float = 2.0
    automation_enabled: bool = False
    rules: list[Rule] = Field(default_factory=list)
    proxy: str | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return (v or "USD").upper()[:3]

    @field_validator("invites_poll_seconds")
    @classmethod
    def _poll_floor(cls, v: int) -> int:
        return max(20, int(v))

    @field_validator("inventory_interval_seconds")
    @classmethod
    def _inv_floor(cls, v: float) -> float:
        return max(1.0, float(v))

    @field_validator("broadcast_interval_seconds")
    @classmethod
    def _bc_floor(cls, v: float) -> float:
        return max(0.5, float(v))

    @field_validator("analysis_workers")
    @classmethod
    def _workers(cls, v: int) -> int:
        return min(4, max(1, int(v)))


class SettingsStore:
    def __init__(self, store: JsonStore):
        self.store = store
        raw = store.load({}) or {}
        try:
            self.value = Settings.model_validate(raw)
        except Exception:  # noqa: BLE001 - never refuse to start over a bad settings file
            self.value = Settings()

    def update(self, patch: dict) -> Settings:
        merged = {**self.value.model_dump(), **patch}
        self.value = Settings.model_validate(merged)
        self.store.save(self.value.model_dump())
        return self.value
