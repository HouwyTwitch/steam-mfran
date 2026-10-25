"""FastAPI app: REST API, event websocket and the static web UI."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core.backend import LiveBackend
from .core.demo import DemoBackend
from .core.events import EventHub
from .core.service import AppService
from .core.session import LoginError, friendly
from .core.settings import SettingsStore
from .imageinfo import image_info
from .steam.community import steamid_from_any
from .steam.http import RateLimited, SessionExpired, SteamError
from .storage import JsonStore

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_IMAGE = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class ActionBody(BaseModel):
    action: Literal["accept", "ignore", "block"]
    steamids: list[str] = Field(min_length=1, max_length=500)
    message: str | None = Field(default=None, max_length=4000)


class AnalyzeBody(BaseModel):
    steamids: list[str] = Field(min_length=1, max_length=500)
    force: bool = False


class SendBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    raw: bool = False


class AckBody(BaseModel):
    timestamp: int


class HideBody(BaseModel):
    last_message: int | None = None


class BroadcastBody(BaseModel):
    steamids: list[str] = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=4000)


def _sids(values: list[str]) -> list[int]:
    try:
        return [steamid_from_any(v) for v in values]
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "invalid steamid") from exc


def _sid(value: str) -> int:
    return _sids([value])[0]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    data_dir: Path,
    demo: bool = False,
    proxy: str | None = None,
    token: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)
    hub = EventHub()
    settings = SettingsStore(JsonStore(data_dir / "settings.json"))
    s = settings.value
    backend_cls = DemoBackend if demo else LiveBackend
    backend = backend_cls(
        data_dir, hub.emit, proxy=proxy or s.proxy, price_provider=s.price_provider,
        inventory_interval=s.inventory_interval_seconds,
    )
    service = AppService(backend, settings, data_dir, hub)
    hosts = LOOPBACK | (allowed_hosts or set())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Steam MFRAN", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service

    # ----------------------------------------------------------------- guards
    def host_ok(host_header: str | None) -> bool:
        if not host_header:
            return False
        host = host_header.rsplit(":", 1)[0] if not host_header.endswith("]") else host_header
        return host in hosts or "*" in hosts

    def token_ok(request_token: str | None) -> bool:
        return not token or bool(request_token and hmac.compare_digest(request_token, token))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # DNS-rebinding protection: only answer to hostnames we expect.
        if not host_ok(request.headers.get("host")):
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
        if token and request.url.path.startswith("/api"):
            supplied = request.cookies.get("mfran_token") or request.headers.get("x-mfran-token")
            if not token_ok(supplied):
                return JSONResponse({"detail": "access token required"}, status_code=401)
        response = await call_next(request)
        if token and request.query_params.get("token") and token_ok(request.query_params.get("token")):
            response.set_cookie("mfran_token", token, httponly=True, samesite="strict")
        return response

    @app.exception_handler(LoginError)
    async def _login_error(_: Request, exc: LoginError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(SessionExpired)
    async def _expired(_: Request, exc: SessionExpired):
        return JSONResponse({"detail": "Steam session expired. Please log in again."}, status_code=401)

    @app.exception_handler(RateLimited)
    async def _limited(_: Request, exc: RateLimited):
        return JSONResponse({"detail": "Steam is rate limiting requests. Try again in a minute."}, status_code=429)

    @app.exception_handler(SteamError)
    async def _steam(_: Request, exc: SteamError):
        return JSONResponse({"detail": friendly(exc)}, status_code=502)

    def require_auth() -> None:
        if not service.authenticated:
            raise HTTPException(401, "Not logged in to Steam")

    # ----------------------------------------------------------------- state & auth
    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return service.snapshot()

    @app.post("/api/auth/login")
    async def login(body: LoginBody) -> dict[str, Any]:
        await backend.login(body.username, body.password)
        return backend.auth_state()

    @app.post("/api/auth/code")
    async def code(body: CodeBody) -> dict[str, Any]:
        await backend.submit_code(body.code)
        return backend.auth_state()

    @app.post("/api/auth/cancel")
    async def cancel() -> dict[str, Any]:
        await backend.cancel_login()
        return backend.auth_state()

    @app.post("/api/auth/retry")
    async def retry() -> dict[str, Any]:
        await backend.retry()
        return backend.auth_state()

    @app.post("/api/auth/logout")
    async def logout() -> dict[str, Any]:
        await backend.logout()
        return backend.auth_state()

    # ----------------------------------------------------------------- invites
    @app.post("/api/invites/refresh")
    async def refresh() -> dict[str, Any]:
        require_auth()
        return await service.refresh_invites()

    @app.post("/api/invites/action")
    async def action(body: ActionBody) -> dict[str, Any]:
        require_auth()
        return await service.act(body.action, _sids(body.steamids), message=body.message)

    @app.post("/api/invites/analyze")
    async def analyze(body: AnalyzeBody) -> dict[str, Any]:
        require_auth()
        service.analyze(_sids(body.steamids), force=body.force)
        return service.invites_meta()

    @app.post("/api/invites/clear")
    async def clear() -> dict[str, Any]:
        service.clear_handled()
        return {"ok": True}

    @app.get("/api/invites/{steamid}/inventory")
    async def inventory(steamid: str, refresh: bool = False) -> dict[str, Any]:
        require_auth()
        return await service.inventory_details(_sid(steamid), refresh=refresh)

    # ----------------------------------------------------------------- chat
    @app.get("/api/chat/conversations")
    async def conversations() -> list[dict[str, Any]]:
        require_auth()
        return await service.conversations()

    @app.get("/api/chat/friends")
    async def friends() -> list[dict[str, Any]]:
        require_auth()
        return service.friends()

    @app.get("/api/chat/{steamid}/messages")
    async def messages(steamid: str, before_ts: int | None = None, before_ordinal: int | None = None) -> dict:
        require_auth()
        return await backend.chat.history(_sid(steamid), before_ts, before_ordinal)

    @app.post("/api/chat/{steamid}/send")
    async def send(steamid: str, body: SendBody) -> dict[str, Any]:
        require_auth()
        return await backend.chat.send(_sid(steamid), body.text, raw=body.raw)

    @app.post("/api/chat/{steamid}/typing")
    async def typing(steamid: str) -> dict[str, Any]:
        require_auth()
        with contextlib.suppress(SteamError, asyncio.TimeoutError):
            await backend.chat.typing(_sid(steamid))
        return {"ok": True}

    @app.post("/api/chat/{steamid}/ack")
    async def ack(steamid: str, body: AckBody) -> dict[str, Any]:
        require_auth()
        with contextlib.suppress(SteamError, asyncio.TimeoutError):
            await backend.chat.ack(_sid(steamid), body.timestamp)
        return {"ok": True}

    @app.post("/api/chat/{steamid}/hide")
    async def hide_chat(steamid: str, body: HideBody) -> dict[str, Any]:
        require_auth()
        await service.hide_chat(_sid(steamid), body.last_message)
        return {"ok": True}

    @app.delete("/api/chat/{steamid}/hide")
    async def unhide_chat(steamid: str) -> dict[str, Any]:
        require_auth()
        service.unhide_chat(_sid(steamid))
        return {"ok": True}

    @app.post("/api/friends/{steamid}/remove")
    async def remove_friend(steamid: str) -> dict[str, Any]:
        require_auth()
        await service.remove_friend(_sid(steamid))
        return {"ok": True}

    @app.post("/api/chat/{steamid}/image")
    async def image(steamid: str, file: UploadFile = File(...), spoiler: bool = Form(False)) -> dict[str, Any]:
        require_auth()
        content = await file.read(MAX_IMAGE + 1)
        if len(content) > MAX_IMAGE:
            raise HTTPException(413, "Images must be smaller than 10 MB")
        info = image_info(content)
        if not info:
            raise HTTPException(400, "Only PNG, JPEG, GIF and WebP images can be sent")
        mime, width, height = info
        url = await backend.chat.send_image(_sid(steamid), content, mime, width, height, spoiler)
        return {"url": url}

    # ----------------------------------------------------------------- broadcast
    @app.post("/api/broadcast")
    async def broadcast(body: BroadcastBody) -> dict[str, Any]:
        require_auth()
        return service.start_broadcast(_sids(body.steamids), body.text)

    @app.post("/api/broadcast/{job_id}/cancel")
    async def cancel_broadcast(job_id: str) -> dict[str, Any]:
        service.cancel_broadcast(job_id)
        return {"ok": True}

    # ----------------------------------------------------------------- settings
    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings.value.model_dump()

    @app.put("/api/settings")
    async def put_settings(patch: dict[str, Any]) -> dict[str, Any]:
        try:
            return await service.update_settings(patch)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/automation/run")
    async def automation_run() -> dict[str, Any]:
        require_auth()
        return await service.run_automation_now()

    @app.post("/api/prices/refresh")
    async def prices_refresh() -> dict[str, Any]:
        await backend.prices.ensure_loaded(force=True)
        status = backend.prices.status()
        hub.emit("prices", status)
        return status

    # ----------------------------------------------------------------- events
    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not host_ok(ws.headers.get("host")) or (origin and origin.split("://", 1)[-1] != ws.headers.get("host")):
            await ws.close(code=4403)
            return
        if token and not token_ok(ws.cookies.get("mfran_token") or ws.query_params.get("token")):
            await ws.close(code=4401)
            return
        await ws.accept()
        queue = hub.subscribe()
        try:
            await ws.send_json({"type": "snapshot", "data": service.snapshot()})
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(queue)

    # ----------------------------------------------------------------- static UI
    @app.get("/")
    async def index() -> Response:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app
