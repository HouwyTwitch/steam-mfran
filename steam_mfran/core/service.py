"""Application service: invite tracking, analysis queue, automation, broadcasts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from .. import __version__
from ..steam.enums import EFriendRelationship, EResult
from ..steam.http import EResultError, RateLimited, SessionExpired, SteamError
from ..steam.inventory import Inventory
from ..steam.pricing import PROVIDERS, summarize
from ..storage import JsonStore
from .events import EventHub
from .rules import first_match
from .session import friendly
from .settings import SettingsStore

log = logging.getLogger(__name__)

STATUS_FOR_ACTION = {"accept": "accepted", "ignore": "ignored", "block": "blocked"}
RETRYABLE_SEND = {EResult.RateLimitExceeded, EResult.Busy, EResult.ServiceUnavailable, EResult.Timeout}


class AnalysisQueue:
    def __init__(self) -> None:
        self._dq: deque[int] = deque()
        self._members: set[int] = set()
        self._event = asyncio.Event()

    def push(self, sid: int, front: bool = False) -> None:
        if sid in self._members:
            return
        self._members.add(sid)
        (self._dq.appendleft if front else self._dq.append)(sid)
        self._event.set()

    async def pop(self) -> int:
        while not self._dq:
            self._event.clear()
            await self._event.wait()
        sid = self._dq.popleft()
        self._members.discard(sid)
        return sid

    def __contains__(self, sid: int) -> bool:
        return sid in self._members

    def __len__(self) -> int:
        return len(self._dq)


class AppService:
    def __init__(self, backend: Any, settings: SettingsStore, data_dir: Path, hub: EventHub):
        self.backend = backend
        self.settings = settings
        self.hub = hub
        self.records: dict[int, dict[str, Any]] = {}
        self._cache_store = JsonStore(data_dir / "cache" / "analysis.json")
        self._cache: dict[str, dict[str, Any]] = self._cache_store.load({}) or {}
        self._history_store = JsonStore(data_dir / "history.json")
        self.history: list[dict[str, Any]] = self._history_store.load([]) or []
        self.queue = AnalysisQueue()
        self._details: OrderedDict[int, Inventory] = OrderedDict()
        self.broadcasts: dict[str, dict[str, Any]] = {}
        self._tasks: list[asyncio.Task] = []
        self._refresh_lock = asyncio.Lock()
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._save_handle: asyncio.TimerHandle | None = None
        self._rate_backoff = 60.0
        self.last_refresh: float | None = None
        self.refresh_error: str | None = None
        self.currency: dict[str, Any] = {"code": "USD", "rate": 1.0}

    def emit(self, type_: str, data: Any = None) -> None:
        self.hub.emit(type_, data)

    @property
    def authenticated(self) -> bool:
        return self.backend.auth_state()["state"] == "authenticated"

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.backend.on_auth_change = self._on_auth_change
        self.backend.chat.on_relationships = self._on_relationships
        await self.backend.start()
        s = self.settings.value
        self._tasks = [asyncio.create_task(self._poll_loop(), name="invites-poll")]
        self._tasks += [
            asyncio.create_task(self._worker(), name=f"analysis-{i}") for i in range(s.analysis_workers)
        ]
        self._tasks.append(asyncio.create_task(self._warm_prices(), name="prices-warmup"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for job in self.broadcasts.values():
            task = job.pop("_task", None)
            if task:
                task.cancel()
        self._flush_cache()
        await self.backend.stop()

    async def _warm_prices(self) -> None:
        try:
            await self.backend.prices.ensure_loaded()
            await self.update_currency()
        except Exception as exc:  # noqa: BLE001
            log.warning("Price warm-up failed: %s", exc)
        self.emit("prices", self.backend.prices.status())

    async def update_currency(self) -> None:
        code = self.settings.value.currency
        rate = await self.backend.prices.usd_rate(code)
        self.currency = {"code": code if rate else "USD", "rate": rate or 1.0, "requested": code}
        self.emit("currency", self.currency)

    def _on_auth_change(self) -> None:
        if self.authenticated:
            self.schedule_refresh(0.5)

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "demo": bool(getattr(self.backend, "demo", False)),
            "auth": self.backend.auth_state(),
            "settings": self.settings.value.model_dump(),
            "chat": self.backend.chat.status(),
            "prices": self.backend.prices.status(),
            "providers": [p.__dict__ for p in PROVIDERS.values()],
            "currency": self.currency,
            "invites": self.invites_meta() | {"items": [self.public(r) for r in self.records.values()]},
            "history": self.history[-200:],
            "broadcasts": [self._public_job(j) for j in self.broadcasts.values()],
        }

    def invites_meta(self) -> dict[str, Any]:
        return {"last_refresh": self.last_refresh, "error": self.refresh_error, "queue": len(self.queue)}

    @staticmethod
    def public(rec: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in rec.items() if not k.startswith("_")}

    def _emit_record(self, rec: dict[str, Any]) -> None:
        self.emit("invite", self.public(rec))

    # ------------------------------------------------------------ invites
    def _new_record(self, sid: int) -> dict[str, Any]:
        cached = self._cache.get(str(sid), {})
        first_seen = cached.get("first_seen") or time.time()
        cached["first_seen"] = first_seen
        self._cache[str(sid)] = cached
        rec = {
            "steamid": str(sid),
            "name": "",
            "avatar": "",
            "level": None,
            "profile_url": f"https://steamcommunity.com/profiles/{sid}/",
            "first_seen": first_seen,
            "status": "pending",
            "profile": cached.get("profile"),
            "inventory": cached.get("inventory"),
            "analysis": {"state": "idle"},
            "rule": None,
        }
        self._apply_profile(rec)
        if rec["profile"] and rec["inventory"]:
            rec["analysis"] = {"state": "done", "cached": True}
            rule = first_match(rec, self.settings.value.rules)
            rec["rule"] = self._rule_ref(rule)
        return rec

    @staticmethod
    def _apply_profile(rec: dict[str, Any]) -> None:
        p = rec.get("profile") or {}
        for key in ("name", "avatar", "level"):
            if p.get(key) not in (None, ""):
                rec[key] = p[key]
        if p.get("profile_url"):
            rec["profile_url"] = p["profile_url"]

    def schedule_refresh(self, delay: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        if self._refresh_handle:
            self._refresh_handle.cancel()
        self._refresh_handle = loop.call_later(delay, lambda: asyncio.ensure_future(self.refresh_invites()))

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.value.invites_poll_seconds)
            if self.authenticated:
                await self.refresh_invites()

    async def refresh_invites(self) -> dict[str, Any]:
        if not self.authenticated:
            return self.invites_meta()
        async with self._refresh_lock:
            try:
                basics = await self.backend.fetch_invites()
            except Exception as exc:  # noqa: BLE001
                self.refresh_error = friendly(exc)
                log.warning("Fetching friend requests failed: %s", exc)
                self.emit("invites:meta", self.invites_meta())
                return self.invites_meta()
            incoming = {b.steamid: b for b in basics if b.incoming}
            auto = self.settings.value.auto_analyze
            for sid, basic in incoming.items():
                rec = self.records.get(sid)
                is_new = rec is None
                if rec is None:
                    rec = self.records[sid] = self._new_record(sid)
                rec["status"] = "pending"
                for key in ("name", "avatar", "profile_url"):
                    if getattr(basic, key) and not (rec.get("profile") or {}).get(key):
                        rec[key] = getattr(basic, key)
                if basic.level is not None and rec.get("level") is None:
                    rec["level"] = basic.level
                self._emit_record(rec)
                if is_new and auto and rec["analysis"].get("state") != "done":
                    self.enqueue(sid)
            for sid, rec in self.records.items():
                if rec["status"] == "pending" and sid not in incoming:
                    rec["status"] = "gone"
                    self._emit_record(rec)
            self.last_refresh = time.time()
            self.refresh_error = None
            self._save_cache_soon()
            self.emit("invites:meta", self.invites_meta())
            return self.invites_meta()

    def _on_relationships(self, changes: dict[int, int], full: bool) -> None:
        refresh = False
        for sid, rel in changes.items():
            rec = self.records.get(sid)
            if rel == EFriendRelationship.RequestRecipient:
                if rec is None or rec["status"] != "pending":
                    refresh = True
            elif rec is not None and rec["status"] == "pending":
                rec["status"] = "accepted" if rel == EFriendRelationship.Friend else (
                    "blocked" if rel == EFriendRelationship.Blocked else "gone")
                self._emit_record(rec)
        if refresh or full:
            self.schedule_refresh(1.5)

    # ------------------------------------------------------------ analysis
    def enqueue(self, sid: int, front: bool = False) -> None:
        rec = self.records.get(sid)
        if not rec:
            return
        rec["analysis"] = {"state": "queued"}
        self._emit_record(rec)
        self.queue.push(sid, front=front)
        self.emit("invites:meta", self.invites_meta())

    def analyze(self, steamids: list[int], force: bool = False) -> None:
        for sid in steamids:
            rec = self.records.get(sid)
            if not rec:
                continue
            if force:
                rec["_force"] = True
            self.enqueue(sid, front=True)

    async def _worker(self) -> None:
        while True:
            sid = await self.queue.pop()
            try:
                await self._analyze(sid)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Analysis of %s crashed", sid)

    def _set_analysis(self, rec: dict[str, Any], state: str, **extra: Any) -> None:
        rec["analysis"] = {"state": state, **extra}
        self._emit_record(rec)

    async def _analyze(self, sid: int) -> None:
        rec = self.records.get(sid)
        if not rec or rec["status"] != "pending":
            return
        force = rec.pop("_force", False)
        cache = self._cache.setdefault(str(sid), {"first_seen": rec["first_seen"]})
        now = time.time()
        try:
            self._set_analysis(rec, "running", step="profile")
            if force or not cache.get("profile") or now - cache.get("profile_at", 0) > 24 * 3600:
                profile = await self.backend.fetch_profile(sid)
                cache["profile"] = profile.to_dict()
                cache["profile_at"] = time.time()
            rec["profile"] = cache["profile"]
            self._apply_profile(rec)

            self._set_analysis(rec, "running", step="inventory")
            ttl = self.settings.value.inventory_cache_hours * 3600
            if force or not cache.get("inventory") or now - cache.get("inventory_at", 0) > ttl:
                inv = await self.backend.fetch_inventory(sid)
                self._remember_details(inv)
                cache["inventory"] = summarize(inv)
                cache["inventory_at"] = time.time()
            rec["inventory"] = cache["inventory"]
            self._rate_backoff = 60.0
            self._save_cache_soon()
            rule = first_match(rec, self.settings.value.rules)
            rec["rule"] = self._rule_ref(rule)
            self._set_analysis(rec, "done")
            if rule and self.settings.value.automation_enabled:
                await self.act(rule.action, [sid], message=rule.message, source=f"rule: {rule.name}")
        except RateLimited as exc:
            wait = exc.retry_after or self._rate_backoff
            self._rate_backoff = min(self._rate_backoff * 2, 900)
            if hasattr(self.backend, "inventory_limiter"):
                self.backend.inventory_limiter.penalize(wait)
            self._set_analysis(rec, "waiting", error="Steam rate limit reached", retry_at=time.time() + wait)
            if force:
                rec["_force"] = True
            asyncio.get_running_loop().call_later(wait, lambda: self.queue.push(sid, front=True))
        except SessionExpired as exc:
            self._set_analysis(rec, "error", error="Steam session expired")
            log.warning("Analysis stopped: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("Analysis of %s failed: %s", sid, exc)
            self._set_analysis(rec, "error", error=friendly(exc))
        finally:
            self.emit("invites:meta", self.invites_meta())

    def _remember_details(self, inv: Inventory) -> None:
        self._details[inv.steamid] = inv
        self._details.move_to_end(inv.steamid)
        while len(self._details) > 40:
            self._details.popitem(last=False)

    async def inventory_details(self, sid: int, refresh: bool = False) -> dict[str, Any]:
        inv = self._details.get(sid)
        if inv is None or refresh:
            inv = await self.backend.fetch_inventory(sid)
            self._remember_details(inv)
            if sid in self.records and inv.state != "error":
                summary = summarize(inv)
                cache = self._cache.setdefault(str(sid), {})
                cache["inventory"], cache["inventory_at"] = summary, time.time()
                self.records[sid]["inventory"] = summary
                self._emit_record(self.records[sid])
                self._save_cache_soon()
        items = sorted(inv.items, key=lambda i: ((i.price or 0) * i.count, i.count), reverse=True)
        return {
            "steamid": str(sid),
            "state": inv.state,
            "error": inv.error,
            "total_count": inv.total_count,
            "fetched_at": inv.fetched_at,
            "items": [i.to_dict() for i in items],
        }

    # ------------------------------------------------------------ automation
    @staticmethod
    def _rule_ref(rule: Any) -> dict[str, Any] | None:
        return {"id": rule.id, "name": rule.name, "action": rule.action} if rule else None

    def reevaluate_rules(self) -> None:
        rules = self.settings.value.rules
        for rec in self.records.values():
            if rec["analysis"].get("state") == "done":
                new = self._rule_ref(first_match(rec, rules))
                if new != rec.get("rule"):
                    rec["rule"] = new
                    self._emit_record(rec)

    async def run_automation_now(self) -> dict[str, Any]:
        """Apply matching rules to every analysed pending invite (manual trigger)."""
        groups: dict[tuple[str, str | None], list[int]] = {}
        names: dict[tuple[str, str | None], str] = {}
        for sid, rec in self.records.items():
            if rec["status"] != "pending" or rec["analysis"].get("state") != "done":
                continue
            rule = first_match(rec, self.settings.value.rules)
            if rule:
                key = (rule.action, rule.message)
                groups.setdefault(key, []).append(sid)
                names[key] = rule.name
        done = 0
        for (action, message), sids in groups.items():
            res = await self.act(action, sids, message=message, source=f"rule: {names[(action, message)]}")
            done += sum(1 for e in res["results"].values() if e is None)
        return {"processed": done}

    # ------------------------------------------------------------ actions
    async def act(self, action: str, steamids: list[int], message: str | None = None,
                  source: str = "manual") -> dict[str, Any]:
        if action not in STATUS_FOR_ACTION:
            raise ValueError("action must be accept, ignore or block")
        targets = [s for s in dict.fromkeys(steamids) if s in self.records and self.records[s]["status"] == "pending"]
        if not targets:
            return {"results": {}, "broadcast": None}
        for sid in targets:
            self.records[sid]["busy"] = action
            self._emit_record(self.records[sid])
        try:
            results = await self.backend.invite_action(action, targets)
        except Exception as exc:  # noqa: BLE001
            results = {sid: friendly(exc) for sid in targets}
        accepted: list[int] = []
        now = time.time()
        for sid in targets:
            rec = self.records[sid]
            rec.pop("busy", None)
            err = results.get(sid, "No response from Steam")
            if err is None:
                rec["status"] = STATUS_FOR_ACTION[action]
                rec["acted_at"] = now
                rec.pop("last_error", None)
                if action == "accept":
                    accepted.append(sid)
            else:
                rec["last_error"] = err
            self._emit_record(rec)
            entry = {
                "ts": now, "steamid": str(sid), "name": rec.get("name"), "action": action,
                "ok": err is None, "error": err, "source": source, "level": rec.get("level"),
                "value": (rec.get("inventory") or {}).get("value"),
            }
            self.history.append(entry)
            self.emit("history", entry)
        self.history = self.history[-1000:]
        self._history_store.save(self.history)
        job = None
        if accepted and message and message.strip():
            job = self.start_broadcast(accepted, message.strip(), delay=3.0, source=source)
        return {"results": {str(k): v for k, v in results.items()}, "broadcast": job}

    def clear_handled(self) -> None:
        for sid in [s for s, r in self.records.items() if r["status"] != "pending"]:
            del self.records[sid]
        self.emit("invites:reset", [self.public(r) for r in self.records.values()])

    # ------------------------------------------------------------ broadcast
    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def start_broadcast(self, steamids: list[int], text: str, delay: float = 0.0,
                        source: str = "manual") -> dict[str, Any]:
        steamids = list(dict.fromkeys(steamids))
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10],
            "text": text,
            "source": source,
            "created_at": time.time(),
            "state": "running",
            "total": len(steamids),
            "sent": 0,
            "failed": 0,
            "results": {str(s): {"state": "pending", "name": self._display_name(s)} for s in steamids},
        }
        self.broadcasts[job["id"]] = job
        job["_task"] = asyncio.create_task(self._run_broadcast(job, steamids, text, delay))
        self.emit("broadcast", self._public_job(job))
        return self._public_job(job)

    def _display_name(self, sid: int) -> str:
        persona = self.backend.chat.personas.get(sid) or {}
        rec = self.records.get(sid) or {}
        return persona.get("name") or rec.get("name") or str(sid)

    async def _run_broadcast(self, job: dict[str, Any], steamids: list[int], text: str, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        for i, sid in enumerate(steamids):
            if job["state"] == "cancelled":
                break
            res = job["results"][str(sid)]
            res["state"] = "sending"
            self.emit("broadcast", self._public_job(job))
            for attempt in range(3):
                try:
                    await self.backend.chat.send(sid, text)
                    res["state"] = "sent"
                    job["sent"] += 1
                    break
                except EResultError as exc:
                    if exc.eresult in RETRYABLE_SEND and attempt < 2:
                        await asyncio.sleep(15 * (attempt + 1))
                        continue
                    res.update(state="failed", error=friendly(exc))
                except (SteamError, asyncio.TimeoutError, OSError) as exc:
                    if attempt < 1:
                        await asyncio.sleep(5)
                        continue
                    res.update(state="failed", error=friendly(exc) or "timeout")
                except Exception as exc:  # noqa: BLE001
                    res.update(state="failed", error=str(exc))
                    break
                if res["state"] == "failed":
                    break
            if res["state"] == "failed":
                job["failed"] += 1
            self.emit("broadcast", self._public_job(job))
            if i < len(steamids) - 1:
                await asyncio.sleep(self.settings.value.broadcast_interval_seconds)
        if job["state"] != "cancelled":
            job["state"] = "done"
        job["finished_at"] = time.time()
        self.emit("broadcast", self._public_job(job))

    def cancel_broadcast(self, job_id: str) -> None:
        job = self.broadcasts.get(job_id)
        if job and job["state"] == "running":
            job["state"] = "cancelled"
            for r in job["results"].values():
                if r["state"] == "pending":
                    r["state"] = "skipped"
            self.emit("broadcast", self._public_job(job))

    # ------------------------------------------------------------ chat
    async def conversations(self) -> list[dict[str, Any]]:
        chat = self.backend.chat
        sessions = await chat.sessions()
        missing = []
        for s in sessions:
            sid = int(s["partner"])
            persona = chat.personas.get(sid)
            if not persona:
                missing.append(sid)
            rec = self.records.get(sid) or {}
            s["persona"] = persona or {"steamid": s["partner"], "name": rec.get("name") or "", "avatar": rec.get("avatar") or ""}
        if missing:
            asyncio.ensure_future(chat.request_personas(missing))
        sessions.sort(key=lambda s: s.get("last_message", 0), reverse=True)
        return sessions

    def friends(self) -> list[dict[str, Any]]:
        friends = self.backend.chat.friend_list()
        friends.sort(key=lambda f: (f.get("state", 0) == 0, (f.get("name") or "").lower()))
        return friends

    # ------------------------------------------------------------ settings
    async def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        old = self.settings.value
        new = self.settings.update(patch)
        if new.price_provider != old.price_provider:
            self.backend.prices.set_provider(new.price_provider)
            asyncio.ensure_future(self._warm_prices())
        if new.currency != old.currency:
            asyncio.ensure_future(self.update_currency())
        if new.inventory_interval_seconds != old.inventory_interval_seconds:
            self.backend.set_inventory_interval(new.inventory_interval_seconds)
        if new.rules != old.rules:
            self.reevaluate_rules()
        self.emit("settings", new.model_dump())
        return new.model_dump()

    # ------------------------------------------------------------ cache
    def _save_cache_soon(self) -> None:
        if self._save_handle:
            return
        self._save_handle = asyncio.get_running_loop().call_later(5, self._flush_cache)

    def _flush_cache(self) -> None:
        self._save_handle = None
        # keep the cache bounded: drop entries not touched for 30 days
        cutoff = time.time() - 30 * 86400
        self._cache = {
            k: v for k, v in self._cache.items()
            if max(v.get("first_seen", 0), v.get("inventory_at", 0), v.get("profile_at", 0)) > cutoff
        }
        try:
            self._cache_store.save(self._cache)
        except OSError as exc:
            log.warning("Could not save analysis cache: %s", exc)
