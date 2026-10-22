from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest
from multidict import CIMultiDict
from yarl import URL

from steam_mfran.steam import proto
from steam_mfran.steam.http import RateLimited, SteamHttp


@dataclass
class FakeResponse:
    status: int
    headers: CIMultiDict
    url: URL


@dataclass
class Call:
    method: str
    url: str
    kwargs: dict[str, Any]

    @property
    def form(self) -> dict[str, Any]:
        data = self.kwargs.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            out: dict[str, Any] = {}
            for k, v in data:
                out.setdefault(k, []).append(v) if k.endswith("[]") else out.__setitem__(k, v)
            return out
        return {}

    def proto(self, schema: str) -> dict[str, Any]:
        raw = self.form.get("input_protobuf_encoded") or (self.kwargs.get("params") or {}).get("input_protobuf_encoded")
        return proto.decode(schema, base64.b64decode(raw))


Handler = Callable[[Call], Any]


@dataclass
class Reply:
    status: int = 200
    body: bytes | str | dict | list | None = b""
    headers: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    cookies: dict[str, dict[str, str]] = field(default_factory=dict)  # domain -> {name: value}


class FakeHttp(SteamHttp):
    """SteamHttp whose network layer is replaced by registered handlers."""

    def __init__(self) -> None:
        super().__init__()
        self.routes: list[tuple[str, str, Handler]] = []
        self.calls: list[Call] = []

    def route(self, method: str, url_prefix: str, handler: Handler) -> None:
        self.routes.append((method, url_prefix, handler))

    async def request(self, method, url, *, retries=2, allow_redirects=True, **kwargs):  # type: ignore[override]
        call = Call(method, url, kwargs)
        self.calls.append(call)
        for m, prefix, handler in self.routes:
            if m == method and url.startswith(prefix):
                reply = handler(call)
                if not isinstance(reply, Reply):
                    reply = Reply(body=reply)
                break
        else:
            raise AssertionError(f"unexpected request {method} {url}")
        body = reply.body
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            reply.headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            body = body.encode()
        for domain, cookies in reply.cookies.items():
            for name, value in cookies.items():
                self.set_cookie(domain, name, value)
        if reply.status == 429:
            raise RateLimited(None)
        return FakeResponse(reply.status, CIMultiDict(reply.headers), URL(reply.url or url)), body or b""


@pytest.fixture
async def fake_http():
    http = FakeHttp()
    yield http
    await http.close()


def proto_reply(schema: str, values: dict[str, Any], eresult: int = 1) -> Reply:
    return Reply(
        body=proto.encode(schema, values),
        headers={"Content-Type": "application/octet-stream", "x-eresult": str(eresult)},
    )


def make_jwt(payload: dict[str, Any]) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'typ': 'JWT', 'alg': 'EdDSA'})}.{b64(payload)}.c2lnbmF0dXJl"
