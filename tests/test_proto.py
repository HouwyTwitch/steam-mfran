import pytest

from steam_mfran.steam import proto


def test_varint_roundtrip():
    for value in (0, 1, 127, 128, 300, 2**31 - 1, 2**32, 2**63, 2**64 - 1):
        encoded = proto.encode_varint(value)
        assert proto.decode_varint(encoded, 0) == (value, len(encoded))


def test_negative_int32_is_ten_bytes_and_roundtrips():
    data = proto.encode("CMsgProtoBufHeader", {"client_sessionid": -5, "eresult": -1})
    decoded = proto.decode("CMsgProtoBufHeader", data)
    assert decoded["client_sessionid"] == -5
    assert decoded["eresult"] == -1
    # field key (1 byte) + 10 byte varint
    assert len(proto.encode("CMsgProtoBufHeader", {"client_sessionid": -5})) == 11


def test_message_roundtrip_with_nested_and_repeated():
    msg = {
        "bincremental": True,
        "friends": [
            {"ulfriendid": 76561198973784183, "efriendrelationship": 2},
            {"ulfriendid": 76561198000000001, "efriendrelationship": 3},
        ],
        "max_friend_count": 250,
    }
    assert proto.decode("CMsgClientFriendsList", proto.encode("CMsgClientFriendsList", msg)) == msg


def test_packed_repeated_scalars_are_accepted():
    # field 2 (friends, fixed64) packed: key = (2 << 3) | 2
    payload = (1).to_bytes(8, "little") + (2).to_bytes(8, "little")
    data = bytes([0x12, len(payload)]) + payload
    assert proto.decode("CMsgClientRequestFriendData", data)["friends"] == [1, 2]


def test_unknown_fields_are_skipped():
    known = proto.encode("CMsgClientLoggedOff", {"eresult": 6})
    unknown = bytes([0x50]) + proto.encode_varint(99) + bytes([0x5A, 3]) + b"abc"
    assert proto.decode("CMsgClientLoggedOff", unknown + known) == {"eresult": 6}


def test_unknown_field_name_raises():
    with pytest.raises(proto.ProtoError):
        proto.encode("CMsgClientLoggedOff", {"nope": 1})


def test_strings_are_utf8():
    data = proto.encode("CFriendMessages_SendMessage_Request", {"message": "привет 👋"})
    assert proto.decode("CFriendMessages_SendMessage_Request", data)["message"] == "привет 👋"
