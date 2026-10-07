"""Minimal, dependency-free protobuf (proto2) codec.

Steam talks protobuf both over the CM websocket and through
``input_protobuf_encoded`` WebAPI calls.  We only need a handful of messages,
so instead of shipping generated ``*_pb2`` modules (and fighting protobuf
runtime version mismatches) we describe the messages we use as small schemas
and encode/decode them here.

Field numbers and types are copied from SteamDatabase/Protobufs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterator

# Wire types
VARINT, I64, LEN, I32 = 0, 1, 2, 5

_VARINT_KINDS = {"int32", "int64", "uint32", "uint64", "bool", "enum", "sint32", "sint64"}
_I64_KINDS = {"fixed64", "sfixed64", "double"}
_I32_KINDS = {"fixed32", "sfixed32", "float"}
_LEN_KINDS = {"string", "bytes", "message"}


class ProtoError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    num: int
    name: str
    kind: str
    message: str | None = None  # schema name for kind == "message"
    repeated: bool = False

    @property
    def wire_type(self) -> int:
        if self.kind in _VARINT_KINDS:
            return VARINT
        if self.kind in _I64_KINDS:
            return I64
        if self.kind in _I32_KINDS:
            return I32
        if self.kind in _LEN_KINDS:
            return LEN
        raise ProtoError(f"unknown field kind {self.kind!r}")


class Schema:
    def __init__(self, name: str, fields: list[Field]):
        self.name = name
        self.by_name = {f.name: f for f in fields}
        self.by_num = {f.num: f for f in fields}


SCHEMAS: dict[str, Schema] = {}


def message(name: str, *fields: tuple) -> Schema:
    """Register a schema. Each field is ``(num, name, kind[, message][, "repeated"])``."""
    parsed: list[Field] = []
    for spec in fields:
        num, fname, kind, *rest = spec
        repeated = "repeated" in rest
        sub = next((r for r in rest if r != "repeated"), None)
        if kind not in _VARINT_KINDS | _I64_KINDS | _I32_KINDS | _LEN_KINDS:
            # bare message name used as the kind
            sub, kind = kind, "message"
        parsed.append(Field(num, fname, kind, sub, repeated))
    schema = Schema(name, parsed)
    SCHEMAS[name] = schema
    return schema


# ---------------------------------------------------------------------------
# Low level helpers
# ---------------------------------------------------------------------------

def encode_varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1  # two's complement, 10 bytes
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf: bytes | memoryview, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtoError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift >= 70:
            raise ProtoError("varint too long")


def _zigzag_encode(v: int) -> int:
    return (v << 1) ^ (v >> 63)


def _zigzag_decode(v: int) -> int:
    return (v >> 1) ^ -(v & 1)


def _to_signed(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def iter_fields(buf: bytes | memoryview) -> Iterator[tuple[int, int, Any]]:
    """Yield ``(field_number, wire_type, raw_value)`` for every field in ``buf``."""
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = decode_varint(buf, pos)
        num, wt = key >> 3, key & 7
        if wt == VARINT:
            val, pos = decode_varint(buf, pos)
        elif wt == I64:
            val = bytes(buf[pos:pos + 8])
            pos += 8
        elif wt == LEN:
            ln, pos = decode_varint(buf, pos)
            val = bytes(buf[pos:pos + ln])
            pos += ln
        elif wt == I32:
            val = bytes(buf[pos:pos + 4])
            pos += 4
        else:
            raise ProtoError(f"unsupported wire type {wt}")
        if pos > n:
            raise ProtoError("truncated message")
        yield num, wt, val


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

def _encode_scalar(field: Field, value: Any) -> bytes:
    k = field.kind
    if k in ("int32", "int64", "uint32", "uint64", "enum"):
        return encode_varint(int(value))
    if k == "bool":
        return encode_varint(1 if value else 0)
    if k in ("sint32", "sint64"):
        return encode_varint(_zigzag_encode(int(value)))
    if k == "fixed64":
        return struct.pack("<Q", int(value) & 0xFFFFFFFFFFFFFFFF)
    if k == "sfixed64":
        return struct.pack("<q", int(value))
    if k == "double":
        return struct.pack("<d", float(value))
    if k == "fixed32":
        return struct.pack("<I", int(value) & 0xFFFFFFFF)
    if k == "sfixed32":
        return struct.pack("<i", int(value))
    if k == "float":
        return struct.pack("<f", float(value))
    if k == "string":
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return encode_varint(len(data)) + data
    if k == "bytes":
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return encode_varint(len(data)) + data
    if k == "message":
        data = encode(field.message, value) if isinstance(value, dict) else bytes(value)
        return encode_varint(len(data)) + data
    raise ProtoError(f"cannot encode kind {k}")


def encode(schema_name: str, values: dict[str, Any] | None) -> bytes:
    """Encode ``values`` (a dict keyed by field name) using a registered schema.

    ``None`` values are skipped; unknown keys raise so typos surface early.
    """
    schema = SCHEMAS[schema_name]
    out = bytearray()
    for name, value in (values or {}).items():
        if value is None:
            continue
        field = schema.by_name.get(name)
        if field is None:
            raise ProtoError(f"{schema_name} has no field {name!r}")
        items = value if field.repeated else [value]
        for item in items:
            out += encode_varint((field.num << 3) | field.wire_type)
            out += _encode_scalar(field, item)
    return bytes(out)


def _decode_value(field: Field, wt: int, raw: Any) -> Any:
    k = field.kind
    if wt == VARINT:
        if k == "bool":
            return bool(raw)
        if k in ("int32", "enum"):
            return _to_signed(raw, 32) if raw >> 31 else raw
        if k == "int64":
            return _to_signed(raw, 64)
        if k in ("sint32", "sint64"):
            return _zigzag_decode(raw)
        if k == "uint32":
            return raw & 0xFFFFFFFF
        return raw
    if wt == I64:
        if k == "sfixed64":
            return struct.unpack("<q", raw)[0]
        if k == "double":
            return struct.unpack("<d", raw)[0]
        return struct.unpack("<Q", raw)[0]
    if wt == I32:
        if k == "sfixed32":
            return struct.unpack("<i", raw)[0]
        if k == "float":
            return struct.unpack("<f", raw)[0]
        return struct.unpack("<I", raw)[0]
    # LEN
    if k == "string":
        return raw.decode("utf-8", errors="replace")
    if k == "bytes":
        return raw
    if k == "message":
        return decode(field.message, raw)
    raise ProtoError(f"cannot decode kind {k}")


def _decode_packed(field: Field, raw: bytes) -> list[Any]:
    """Decode a packed repeated scalar field."""
    out: list[Any] = []
    pos = 0
    wt = field.wire_type
    while pos < len(raw):
        if wt == VARINT:
            v, pos = decode_varint(raw, pos)
        elif wt == I64:
            v, pos = raw[pos:pos + 8], pos + 8
        else:
            v, pos = raw[pos:pos + 4], pos + 4
        out.append(_decode_value(field, wt, v))
    return out


def decode(schema_name: str, data: bytes | memoryview | None) -> dict[str, Any]:
    """Decode bytes into a dict keyed by field name. Unknown fields are ignored.

    Repeated fields always decode to lists (empty when absent).
    """
    schema = SCHEMAS[schema_name]
    out: dict[str, Any] = {f.name: [] for f in schema.by_num.values() if f.repeated}
    if not data:
        return out
    for num, wt, raw in iter_fields(data):
        field = schema.by_num.get(num)
        if field is None:
            continue
        if field.repeated:
            if wt == LEN and field.wire_type != LEN:
                out[field.name].extend(_decode_packed(field, raw))
            else:
                out[field.name].append(_decode_value(field, wt, raw))
        else:
            if wt != field.wire_type:
                continue  # schema mismatch; be lenient
            out[field.name] = _decode_value(field, wt, raw)
    return out


# ---------------------------------------------------------------------------
# Schemas we use (subset of SteamDatabase/Protobufs)
# ---------------------------------------------------------------------------

JOBID_NONE = 0xFFFFFFFFFFFFFFFF

# steammessages_base.proto
message(
    "CMsgProtoBufHeader",
    (1, "steamid", "fixed64"),
    (2, "client_sessionid", "int32"),
    (3, "routing_appid", "uint32"),
    (10, "jobid_source", "fixed64"),
    (11, "jobid_target", "fixed64"),
    (12, "target_job_name", "string"),
    (13, "eresult", "int32"),
    (14, "error_message", "string"),
    (24, "seq_num", "int32"),
)
message(
    "CMsgMulti",
    (1, "size_unzipped", "uint32"),
    (2, "message_body", "bytes"),
)

# steammessages_clientserver_login.proto
message("CMsgClientHeartBeat", (1, "send_reply", "bool"))
message(
    "CMsgClientLogon",
    (1, "protocol_version", "uint32"),
    (3, "cell_id", "uint32"),
    (6, "client_language", "string"),
    (7, "client_os_type", "uint32"),
    (8, "should_remember_password", "bool"),
    (32, "ui_mode", "uint32"),
    (33, "chat_mode", "uint32"),
    (50, "account_name", "string"),
    (96, "machine_name", "string"),
    (102, "supports_rate_limit_response", "bool"),
    (103, "web_logon_nonce", "string"),
    (108, "access_token", "string"),
)
message(
    "CMsgClientLogonResponse",
    (1, "eresult", "int32"),
    (2, "legacy_out_of_game_heartbeat_seconds", "int32"),
    (3, "heartbeat_seconds", "int32"),
    (5, "rtime32_server_time", "fixed32"),
    (6, "account_flags", "uint32"),
    (7, "cell_id", "uint32"),
    (14, "vanity_url", "string"),
    (16, "user_country", "string"),
    (20, "client_supplied_steamid", "fixed64"),
)
message("CMsgClientLoggedOff", (1, "eresult", "int32"))

# steammessages_clientserver_friends.proto
message(
    "CMsgClientFriendsList.Friend",
    (1, "ulfriendid", "fixed64"),
    (2, "efriendrelationship", "uint32"),
)
message(
    "CMsgClientFriendsList",
    (1, "bincremental", "bool"),
    (2, "friends", "CMsgClientFriendsList.Friend", "repeated"),
    (3, "max_friend_count", "uint32"),
    (4, "active_friend_count", "uint32"),
    (5, "friends_limit_hit", "bool"),
)
message(
    "CMsgClientRequestFriendData",
    (1, "persona_state_requested", "uint32"),
    (2, "friends", "fixed64", "repeated"),
)
message(
    "CMsgClientChangeStatus",
    (1, "persona_state", "uint32"),
    (2, "player_name", "string"),
    (8, "is_client_idle", "bool"),
)
message(
    "CMsgClientPersonaState.Friend",
    (1, "friendid", "fixed64"),
    (2, "persona_state", "uint32"),
    (3, "game_played_app_id", "uint32"),
    (6, "persona_state_flags", "uint32"),
    (15, "player_name", "string"),
    (31, "avatar_hash", "bytes"),
    (45, "last_logoff", "uint32"),
    (46, "last_logon", "uint32"),
    (47, "last_seen_online", "uint32"),
    (55, "game_name", "string"),
    (56, "gameid", "fixed64"),
)
message(
    "CMsgClientPersonaState",
    (1, "status_flags", "uint32"),
    (2, "friends", "CMsgClientPersonaState.Friend", "repeated"),
)

# steammessages_friendmessages.steamclient.proto
message(
    "CFriendMessages_GetRecentMessages_Request",
    (1, "steamid1", "fixed64"),
    (2, "steamid2", "fixed64"),
    (3, "count", "uint32"),
    (4, "most_recent_conversation", "bool"),
    (5, "rtime32_start_time", "fixed32"),
    (6, "bbcode_format", "bool"),
    (7, "start_ordinal", "uint32"),
    (8, "time_last", "uint32"),
    (9, "ordinal_last", "uint32"),
)
message(
    "CFriendMessages_GetRecentMessages_Response.FriendMessage",
    (1, "accountid", "uint32"),
    (2, "timestamp", "uint32"),
    (3, "message", "string"),
    (4, "ordinal", "uint32"),
)
message(
    "CFriendMessages_GetRecentMessages_Response",
    (1, "messages", "CFriendMessages_GetRecentMessages_Response.FriendMessage", "repeated"),
    (4, "more_available", "bool"),
)
message(
    "CFriendsMessages_GetActiveMessageSessions_Request",
    (1, "lastmessage_since", "uint32"),
    (2, "only_sessions_with_messages", "bool"),
)
message(
    "CFriendsMessages_GetActiveMessageSessions_Response.FriendMessageSession",
    (1, "accountid_friend", "uint32"),
    (2, "last_message", "uint32"),
    (3, "last_view", "uint32"),
    (4, "unread_message_count", "uint32"),
)
message(
    "CFriendsMessages_GetActiveMessageSessions_Response",
    (1, "message_sessions",
     "CFriendsMessages_GetActiveMessageSessions_Response.FriendMessageSession", "repeated"),
    (2, "timestamp", "uint32"),
)
message(
    "CFriendMessages_SendMessage_Request",
    (1, "steamid", "fixed64"),
    (2, "chat_entry_type", "int32"),
    (3, "message", "string"),
    (4, "contains_bbcode", "bool"),
    (5, "echo_to_sender", "bool"),
    (6, "low_priority", "bool"),
    (8, "client_message_id", "string"),
)
message(
    "CFriendMessages_SendMessage_Response",
    (1, "modified_message", "string"),
    (2, "server_timestamp", "uint32"),
    (3, "ordinal", "uint32"),
    (4, "message_without_bb_code", "string"),
)
message(
    "CFriendMessages_AckMessage_Notification",
    (1, "steamid_partner", "fixed64"),
    (2, "timestamp", "uint32"),
)
message(
    "CFriendMessages_IncomingMessage_Notification",
    (1, "steamid_friend", "fixed64"),
    (2, "chat_entry_type", "int32"),
    (3, "from_limited_account", "bool"),
    (4, "message", "string"),
    (5, "rtime32_server_timestamp", "fixed32"),
    (6, "ordinal", "uint32"),
    (7, "local_echo", "bool"),
    (8, "message_no_bbcode", "string"),
    (9, "low_priority", "bool"),
)
message("NoResponse")

# steammessages_auth.steamclient.proto
message(
    "CAuthentication_GetPasswordRSAPublicKey_Request",
    (1, "account_name", "string"),
)
message(
    "CAuthentication_GetPasswordRSAPublicKey_Response",
    (1, "publickey_mod", "string"),
    (2, "publickey_exp", "string"),
    (3, "timestamp", "uint64"),
)
message(
    "CAuthentication_DeviceDetails",
    (1, "device_friendly_name", "string"),
    (2, "platform_type", "enum"),
    (3, "os_type", "int32"),
    (4, "gaming_device_type", "uint32"),
)
message(
    "CAuthentication_BeginAuthSessionViaCredentials_Request",
    (1, "device_friendly_name", "string"),
    (2, "account_name", "string"),
    (3, "encrypted_password", "string"),
    (4, "encryption_timestamp", "uint64"),
    (5, "remember_login", "bool"),
    (6, "platform_type", "enum"),
    (7, "persistence", "enum"),
    (8, "website_id", "string"),
    (9, "device_details", "CAuthentication_DeviceDetails"),
    (10, "guard_data", "string"),
    (11, "language", "uint32"),
    (12, "qos_level", "int32"),
)
message(
    "CAuthentication_AllowedConfirmation",
    (1, "confirmation_type", "enum"),
    (2, "associated_message", "string"),
)
message(
    "CAuthentication_BeginAuthSessionViaCredentials_Response",
    (1, "client_id", "uint64"),
    (2, "request_id", "bytes"),
    (3, "interval", "float"),
    (4, "allowed_confirmations", "CAuthentication_AllowedConfirmation", "repeated"),
    (5, "steamid", "uint64"),
    (6, "weak_token", "string"),
    (7, "agreement_session_url", "string"),
    (8, "extended_error_message", "string"),
)
message(
    "CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request",
    (1, "client_id", "uint64"),
    (2, "steamid", "fixed64"),
    (3, "code", "string"),
    (4, "code_type", "enum"),
)
message("CAuthentication_UpdateAuthSessionWithSteamGuardCode_Response")
message(
    "CAuthentication_PollAuthSessionStatus_Request",
    (1, "client_id", "uint64"),
    (2, "request_id", "bytes"),
)
message(
    "CAuthentication_PollAuthSessionStatus_Response",
    (1, "new_client_id", "uint64"),
    (2, "new_challenge_url", "string"),
    (3, "refresh_token", "string"),
    (4, "access_token", "string"),
    (5, "had_remote_interaction", "bool"),
    (6, "account_name", "string"),
    (7, "new_guard_data", "string"),
    (8, "agreement_session_url", "string"),
)
