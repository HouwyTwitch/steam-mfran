from pathlib import Path
from urllib.parse import urlencode

import pytest

from steam_mfran.steam.community import (
    Community,
    merge_miniprofile,
    parse_member_since,
    parse_pending_page,
    parse_profile_xml,
)
from steam_mfran.steam.http import COMMUNITY, SessionExpired, SteamError

from .conftest import Reply

FIX = Path(__file__).parent / "fixtures"
ME = 76561198191298143


def test_parse_pending_page_splits_received_and_sent():
    invites, profile = parse_pending_page((FIX / "pending.html").read_text())
    by_id = {i.steamid: i for i in invites}
    assert profile["url"] == "https://steamcommunity.com/id/Houwy/"

    budi = by_id[76561198973784183]
    assert budi.incoming and budi.name == "KING BUDI" and budi.level == 27
    assert budi.avatar.endswith("_full.jpg")
    assert budi.profile_url == "https://steamcommunity.com/id/KINGBUDI"

    fresh = by_id[76561198000012345]
    assert fresh.incoming and fresh.level == 0 and fresh.name == "Ψ fresh acc Ψ"

    assert by_id[76561198055555555].incoming is False


def test_parse_pending_page_falls_back_to_inline_js():
    html = "<div>FriendAccept( '76561198973784183', 'accept' ) FriendAccept( '76561198055555555', 'cancel' )</div>"
    invites, _ = parse_pending_page(html)
    assert {(i.steamid, i.incoming) for i in invites} == {(76561198973784183, True), (76561198055555555, False)}


def test_logged_out_page_raises():
    with pytest.raises(SessionExpired):
        parse_pending_page('<script>g_steamID = false;</script>')


def test_parse_profile_xml():
    info = parse_profile_xml(76561198973784183, (FIX / "profile.xml").read_text())
    assert info.name == "KING BUDI"
    assert info.vac_banned is True and info.limited is False and info.trade_ban == "None"
    assert info.privacy == "public" and info.location == "Jakarta, Indonesia"
    assert info.profile_url == "https://steamcommunity.com/id/KINGBUDI/"
    assert info.member_since == parse_member_since("October 23, 2014")


def test_parse_profile_xml_error_document():
    info = parse_profile_xml(1, "<response><error><![CDATA[The specified profile could not be found.]]></error></response>")
    assert info.errors == ["The specified profile could not be found."]


def test_member_since_formats():
    assert parse_member_since("23 October, 2014") == parse_member_since("October 23, 2014")
    assert parse_member_since("garbage") is None


def test_merge_miniprofile():
    info = parse_profile_xml(1, (FIX / "profile.xml").read_text())
    merge_miniprofile(info, {"level": 31, "persona_name": "Budi!", "avatar_url": "https://x/a_full.jpg",
                             "favorite_badge": {"name": "Pillar of Community"}, "in_game": {"name": "Counter-Strike 2"}})
    assert (info.level, info.name, info.favorite_badge, info.in_game) == (31, "Budi!", "Pillar of Community", "Counter-Strike 2")


async def test_pending_invites_uses_vanity_profile_url(fake_http):
    fake_http.route("GET", f"{COMMUNITY}/profiles/{ME}/friends/pending",
                    lambda c: Reply(body=(FIX / "pending.html").read_text(),
                                    url=f"{COMMUNITY}/id/Houwy/friends/pending"))
    community = Community(fake_http, lambda: ME)
    invites = await community.get_pending_invites()
    assert len([i for i in invites if i.incoming]) == 2
    assert community.profile_base == "https://steamcommunity.com/id/Houwy/"


async def test_pending_redirect_to_login_means_expired(fake_http):
    fake_http.route("GET", f"{COMMUNITY}/profiles/{ME}/friends/pending",
                    lambda c: Reply(body="<html>login</html>", url=f"{COMMUNITY}/login/home/?goto=friends"))
    with pytest.raises(SessionExpired):
        await Community(fake_http, lambda: ME).get_pending_invites()


async def test_bulk_friend_action_matches_browser_request(fake_http):
    """The body must match what steamcommunity.com sends (114 bytes for one invite)."""
    seen = {}

    def action(call):
        seen["call"] = call
        return Reply(body={"success": 1})
    fake_http.route("POST", f"{COMMUNITY}/id/Houwy/friends/action", action)
    community = Community(fake_http, lambda: ME)
    community.profile_base = "https://steamcommunity.com/id/Houwy/"
    result = await community.friend_action("accept", [76561198973784183])
    assert result == {76561198973784183: None}

    call = seen["call"]
    body = urlencode(call.kwargs["data"])
    assert len(body) == 114
    assert body.startswith(f"sessionid={fake_http.session_id}&steamid={ME}&ajax=1&action=accept&steamids%5B%5D=")
    assert call.kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


async def test_remove_friend_matches_browser_request(fake_http):
    friend = 76561198000004242
    calls = []
    fake_http.route("POST", f"{COMMUNITY}/actions/RemoveFriendAjax", lambda c: calls.append(c) or Reply(body="true"))
    await Community(fake_http, lambda: ME).remove_friend(friend)

    (call,) = calls
    assert urlencode(call.kwargs["data"]) == f"sessionID={fake_http.session_id}&steamid={friend}"
    headers = call.kwargs["headers"]
    assert headers["X-Requested-With"] == "XMLHttpRequest" and headers["Origin"] == COMMUNITY
    assert headers["Referer"] == f"{COMMUNITY}/profiles/{friend}"


@pytest.mark.parametrize("reply,error", [
    (Reply(body="false"), SteamError),
    (Reply(body={"success": 0, "error_text": "Not friends"}), SteamError),
    (Reply(status=500, body=""), SteamError),
    (Reply(status=403, body=""), SessionExpired),
])
async def test_remove_friend_failures(fake_http, reply, error):
    fake_http.route("POST", f"{COMMUNITY}/actions/RemoveFriendAjax", lambda c: reply)
    with pytest.raises(error):
        await Community(fake_http, lambda: ME).remove_friend(76561198000004242)


async def test_bulk_failure_falls_back_to_single_endpoints(fake_http):
    fake_http.route("POST", f"{COMMUNITY}/profiles/{ME}/friends/action", lambda c: Reply(status=500, body="oops"))
    singles = []

    def ignore(call):
        singles.append(call.form["steamid"])
        return Reply(body={"success": True})
    fake_http.route("POST", f"{COMMUNITY}/actions/IgnoreFriendInviteAjax", ignore)
    result = await Community(fake_http, lambda: ME).friend_action("ignore", [1, 2])
    assert result == {1: None, 2: None} and singles == ["1", "2"]


async def test_client_js_token(fake_http):
    fake_http.route("GET", f"{COMMUNITY}/chat/clientjstoken", lambda c: Reply(
        body={"logged_in": True, "steamid": str(ME), "accountid": 1, "account_name": "houwy", "token": "tok"}))
    data = await Community(fake_http, lambda: ME).get_client_js_token()
    assert data["token"] == "tok"

    fake_http.routes.clear()
    fake_http.route("GET", f"{COMMUNITY}/chat/clientjstoken", lambda c: Reply(body={"logged_in": False}))
    with pytest.raises(SessionExpired):
        await Community(fake_http, lambda: ME).get_client_js_token()


async def test_upload_chat_image_flow(fake_http):
    steps = []
    fake_http.route("POST", f"{COMMUNITY}/chat/beginfileupload/", lambda c: steps.append("begin") or Reply(body={
        "success": 1, "timestamp": 111, "hmac": "h",
        "result": {"ugcid": "42", "url_host": "upload.example", "url_path": "/put/42", "use_https": True,
                   "request_headers": [{"name": "Content-Type", "value": "image/png"}]}}))
    fake_http.route("PUT", "https://upload.example/put/42", lambda c: steps.append(("put", c.kwargs["headers"])) or Reply())
    fake_http.route("POST", f"{COMMUNITY}/chat/commitfileupload/", lambda c: steps.append("commit") or Reply(body={
        "success": 1, "result": {"success": 1, "details": {"url": "https://images.steamusercontent.com/ugc/1/2/"}}}))
    url = await Community(fake_http, lambda: ME).upload_chat_image(5, b"\x89PNG....", "image/png", 10, 20)
    assert url == "https://images.steamusercontent.com/ugc/1/2/"
    assert steps == ["begin", ("put", {"Content-Type": "image/png"}), "commit"]
