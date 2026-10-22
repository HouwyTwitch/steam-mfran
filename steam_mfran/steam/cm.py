"""Steam Connection Manager (CM) client over WebSocket.

This is the same transport steamcommunity.com/chat uses: we fetch a one-time
web logon token from ``/chat/clientjstoken`` (authenticated by our web
cookies) and log on as a *web* client.  Once logged on we can call unified
service methods (``FriendMessages.SendMessage#1`` …) and receive push
notifications such as incoming chat messages and friend list changes.

Packet layout (protobuf messages)::

    uint32 emsg | 0x80000000
    uint32 header_length
    CMsgProtoBufHeader header
    body (message specific protobuf)

The server may batch packets into ``EMsg.Multi`` (optionally gzipped), where
each sub-packet is prefixed with its uint32 length.
"""

from __future__ import annotations

import asyncio
import gzip
import itertools
import logging
import random
import struct
from typing import Any, Awaitable, Callable

import aiohttp

from . import proto
from .enums import OS_TYPE_WEB, PROTOCOL_VERSION, EMsg, EResult
from .http import WEBAPI, EResultError, SteamError, SteamHttp

log = logging.getLogger(__name__)

PROTO_MASK = 0x80000000
FALLBACK_SERVERS = [
    "cmp1-fra1.steamserver.net:27020",
    "cmp2-fra1.steamserver.net:27019",
    "cmp1-sto1.steamserver.net:27020",
    "cmp2-sto2.steamserver.net:443",
]
# Log-on failures that mean "get a fresh web logon token / web session"
AUTH_FAILURES = {
    EResult.InvalidPassword, EResult.AccessDenied, EResult.Expired, EResult.InvalidParam,
    EResult.NotLoggedOn, EResult.Revoked, EResult.InvalidSignature,
}


class CMNotConnected(SteamError):
    pass


class CMAuthError(SteamError):
    def __init__(self, eresult: int):
        self.eresult = eresult
        super().__init__(f"CM rejected the web logon ({EResult.name_of(eresult)})")


def build_packet(emsg: int, header: dict[str, Any], body: bytes) -> bytes:
    hdr = proto.encode("CMsgProtoBufHeader", header)
    return struct.pack("<II", int(emsg) | PROTO_MASK, len(hdr)) + hdr + body


def parse_packet(data: bytes) -> tuple[int, dict[str, Any], bytes]:
    if len(data) < 8:
        raise SteamError("CM packet too short")
    raw_emsg = struct.unpack_from("<I", data, 0)[0]
    emsg = raw_emsg & ~PROTO_MASK
    if raw_emsg & PROTO_MASK:
        hlen = struct.unpack_from("<I", data, 4)[0]
        header = proto.decode("CMsgProtoBufHeader", data[8:8 + hlen])
        return emsg, header, data[8 + hlen:]
    # Legacy "extended" header: emsg, hdr size, hdr version, target job, source job,
    # canary, steamid, session id = 36 bytes.
    if len(data) < 36:
        return emsg, {}, b""
    target, source = struct.unpack_from("<QQ", data, 7)
    steamid, session = struct.unpack_from("<Qi", data, 24)
    return emsg, {"jobid_target": target, "jobid_source": source, "steamid": steamid,
                  "client_sessionid": session}, data[36:]


def iter_multi(body: bytes) -> list[bytes]:
    multi = proto.decode("CMsgMulti", body)
    payload = multi.get("message_body") or b""
    if multi.get("size_unzipped"):
        payload = gzip.decompress(payload)
    out = []
    pos = 0
    while pos + 4 <= len(payload):
        size = struct.unpack_from("<I", payload, pos)[0]
        out.append(payload[pos + 4:pos + 4 + size])
        pos += 4 + size
    return out


Credentials = Callable[[], Awaitable[dict[str, Any]]]


class CMClient:
    """Keeps a logged-on CM websocket alive and multiplexes service calls."""

    def __init__(self, http: SteamHttp, credentials: Credentials):
        self.http = http
        self._credentials = credentials
        self.steamid = 0
        self.session_id = 0
        self.state = "offline"  # offline | connecting | online | error
        self.last_error: str | None = None
        self.on_state: Callable[[str], None] | None = None
        self.on_auth_failure: Callable[[], Awaitable[None]] | None = None
        self.notification_handlers: dict[str, Callable[[bytes], None]] = {}
        self.emsg_handlers: dict[int, Callable[[bytes], None]] = {}
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._jobs: dict[int, asyncio.Future] = {}
        self._job_ids = itertools.count(random.randint(1, 1 << 20))
        self._logged_on = asyncio.Event()
        self._logon_future: asyncio.Future | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.scheme = "wss"
        self.servers_override: list[str] | None = None

    # ------------------------------------------------------------ lifecycle
    @property
    def online(self) -> bool:
        return self._logged_on.is_set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="steam-cm")

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            try:
                await self._send(EMsg.ClientLogOff, {}, b"")
            except Exception:  # noqa: BLE001
                pass
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._set_state("offline")

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        if error is not None or state == "online":
            self.last_error = error
        if self.on_state:
            try:
                self.on_state(state)
            except Exception:  # noqa: BLE001
                log.exception("CM state callback failed")

    async def _run(self) -> None:
        backoff = 3.0
        while not self._stopping:
            try:
                self._set_state("connecting")
                await self._session_once()
                backoff = 3.0
            except asyncio.CancelledError:
                raise
            except CMAuthError as exc:
                log.warning("%s", exc)
                self._set_state("error", str(exc))
                if self.on_auth_failure:
                    try:
                        await self.on_auth_failure()
                    except Exception as inner:  # noqa: BLE001
                        log.warning("Session refresh after CM auth failure failed: %s", inner)
                        backoff = max(backoff, 60.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("CM connection error: %s", exc)
                self._set_state("error", str(exc))
            finally:
                self._teardown()
            if self._stopping:
                break
            await asyncio.sleep(backoff + random.random() * 2)
            backoff = min(backoff * 2, 120.0)

    def _teardown(self) -> None:
        self._logged_on.clear()
        for fut in self._jobs.values():
            if not fut.done():
                fut.set_exception(CMNotConnected("CM connection lost"))
        self._jobs.clear()
        if self.state == "online":
            self._set_state("offline")

    async def _servers(self) -> list[str]:
        if self.servers_override:
            return list(self.servers_override)
        try:
            data = await self.http.get_json(
                f"{WEBAPI}/ISteamDirectory/GetCMListForConnect/v1/",
                params={"cellid": "0", "cmtype": "websockets"},
            )
            servers = [
                s["endpoint"] for s in (data or {}).get("response", {}).get("serverlist", [])
                if s.get("type") == "websockets" and s.get("endpoint")
            ]
            if servers:
                return servers
        except Exception as exc:  # noqa: BLE001
            log.debug("CM list fetch failed: %s", exc)
        return list(FALLBACK_SERVERS)

    async def _session_once(self) -> None:
        servers = await self._servers()
        creds = await self._credentials()
        ws = None
        last_exc: Exception | None = None
        for endpoint in servers[:6]:
            try:
                ws = await self.http.session.ws_connect(
                    f"{self.scheme}://{endpoint}/cmsocket/",
                    heartbeat=30,
                    max_msg_size=0,
                    proxy=self.http.proxy,
                )
                log.info("Connected to CM %s", endpoint)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.debug("CM %s unreachable: %s", endpoint, exc)
        if ws is None:
            raise SteamError(f"could not reach any Steam CM server ({last_exc})")
        self._ws = ws
        self.steamid = int(creds["steamid"])
        self.session_id = 0

        loop = asyncio.get_running_loop()
        self._logon_future = loop.create_future()
        reader = asyncio.create_task(self._reader(ws))
        heartbeat: asyncio.Task | None = None
        try:
            await self._send(EMsg.ClientLogon, {}, proto.encode("CMsgClientLogon", {
                "protocol_version": PROTOCOL_VERSION,
                "client_os_type": OS_TYPE_WEB,
                "ui_mode": 4,
                "chat_mode": 2,
                "account_name": creds["account_name"],
                "web_logon_nonce": creds["token"],
            }))
            done, _ = await asyncio.wait({self._logon_future, reader}, timeout=20,
                                         return_when=asyncio.FIRST_COMPLETED)
            if self._logon_future not in done:
                raise SteamError("CM did not answer the logon request")
            resp = self._logon_future.result()
            eresult = int(resp.get("eresult", EResult.Fail))
            if eresult != EResult.OK:
                if eresult in AUTH_FAILURES:
                    raise CMAuthError(eresult)
                raise SteamError(f"CM logon failed: {EResult.name_of(eresult)}")
            interval = resp.get("heartbeat_seconds") or resp.get("legacy_out_of_game_heartbeat_seconds") or 9
            heartbeat = asyncio.create_task(self._heartbeat(max(5, int(interval))))
            self._logged_on.set()
            self._set_state("online")
            log.info("Logged on to Steam chat as %s", self.steamid)
            await reader
        finally:
            if heartbeat:
                heartbeat.cancel()
            if not reader.done():
                reader.cancel()
            if not ws.closed:
                await ws.close()
            self._ws = None

    async def _heartbeat(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._send(EMsg.ClientHeartBeat, {}, b"")

    async def _reader(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                try:
                    self._dispatch(msg.data)
                except Exception:  # noqa: BLE001
                    log.exception("Failed to handle CM packet")
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        log.info("CM websocket closed")

    # ------------------------------------------------------------ io
    async def _send(self, emsg: int, header: dict[str, Any], body: bytes) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise CMNotConnected("not connected to Steam CM")
        hdr = {"steamid": self.steamid, "client_sessionid": self.session_id, **header}
        await ws.send_bytes(build_packet(emsg, hdr, body))

    def _dispatch(self, data: bytes) -> None:
        emsg, header, body = parse_packet(data)
        if emsg == EMsg.Multi:
            for sub in iter_multi(body):
                self._dispatch(sub)
            return
        if emsg == EMsg.ClientLogOnResponse:
            if header.get("client_sessionid"):
                self.session_id = header["client_sessionid"]
            if header.get("steamid"):
                self.steamid = header["steamid"]
            if self._logon_future and not self._logon_future.done():
                self._logon_future.set_result(proto.decode("CMsgClientLogonResponse", body))
            return
        if emsg == EMsg.ClientLoggedOff:
            eresult = proto.decode("CMsgClientLoggedOff", body).get("eresult")
            log.warning("Steam logged our chat session off: %s", EResult.name_of(eresult or 0))
            if self._ws is not None:
                asyncio.ensure_future(self._ws.close())
            return
        target = header.get("jobid_target")
        if target is not None and target in self._jobs:
            fut = self._jobs.pop(target)
            if not fut.done():
                fut.set_result((header, body))
            return
        if emsg == EMsg.ServiceMethod:
            name = header.get("target_job_name", "")
            handler = self.notification_handlers.get(name)
            if handler:
                handler(body)
            else:
                log.debug("Unhandled CM notification %s", name)
            return
        handler = self.emsg_handlers.get(emsg)
        if handler:
            handler(body)

    # ------------------------------------------------------------ public api
    async def wait_online(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._logged_on.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def call(
        self,
        method: str,
        request_schema: str,
        request: dict[str, Any],
        response_schema: str,
        timeout: float = 15,
    ) -> dict[str, Any]:
        if not self.online:
            raise CMNotConnected("Steam chat is not connected")
        job = next(self._job_ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._jobs[job] = fut
        try:
            await self._send(
                EMsg.ServiceMethodCallFromClient,
                {"target_job_name": method, "jobid_source": job},
                proto.encode(request_schema, request),
            )
            header, body = await asyncio.wait_for(fut, timeout)
        finally:
            self._jobs.pop(job, None)
        eresult = header.get("eresult", EResult.OK)
        if eresult != EResult.OK:
            raise EResultError(eresult, header.get("error_message") or None)
        return proto.decode(response_schema, body)

    async def notify(self, method: str, request_schema: str, request: dict[str, Any]) -> None:
        await self._send(
            EMsg.ServiceMethodCallFromClient,
            {"target_job_name": method, "jobid_source": proto.JOBID_NONE},
            proto.encode(request_schema, request),
        )

    async def send_message(self, emsg: int, schema: str, body: dict[str, Any]) -> None:
        await self._send(emsg, {}, proto.encode(schema, body))
