import base64
import time

import pytest
import rsa

from steam_mfran.core.session import LoginError, SessionManager
from steam_mfran.steam.auth import SteamAuthenticator
from steam_mfran.steam.enums import EAuthSessionGuardType as G
from steam_mfran.steam.http import COMMUNITY, LOGIN, WEBAPI
from steam_mfran.storage import JsonStore

from .conftest import Reply, make_jwt, proto_reply

STEAMID = 76561198191298143
PUB, PRIV = rsa.newkeys(512)
AUTH = f"{WEBAPI}/IAuthenticationService"


def refresh_token(exp_in: float = 100 * 86400) -> str:
    return make_jwt({"iss": "steam", "sub": str(STEAMID), "aud": ["web", "renew", "derive"], "exp": int(time.time() + exp_in)})


def access_token(exp_in: float = 86400) -> str:
    return make_jwt({"iss": "r:abc", "sub": str(STEAMID), "aud": ["web:community"], "exp": int(time.time() + exp_in)})


def install_login_routes(http, confirmations, poll_ready_after=1, refresh=None, access=None):
    state = {"polls": 0, "codes": [], "begin": None}
    refresh = refresh or refresh_token()
    access = access or access_token()

    http.route("GET", f"{AUTH}/GetPasswordRSAPublicKey", lambda c: proto_reply(
        "CAuthentication_GetPasswordRSAPublicKey_Response",
        {"publickey_mod": format(PUB.n, "x"), "publickey_exp": format(PUB.e, "x"), "timestamp": 1234567}))

    def begin(call):
        state["begin"] = call.proto("CAuthentication_BeginAuthSessionViaCredentials_Request")
        return proto_reply("CAuthentication_BeginAuthSessionViaCredentials_Response", {
            "client_id": 987654321, "request_id": b"\x01\x02\x03", "interval": 0.01, "steamid": STEAMID,
            "allowed_confirmations": [{"confirmation_type": t} for t in confirmations],
        })
    http.route("POST", f"{AUTH}/BeginAuthSessionViaCredentials", begin)

    def code(call):
        req = call.proto("CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request")
        state["codes"].append(req)
        if req["code"] != "ABC12":
            return proto_reply("CAuthentication_UpdateAuthSessionWithSteamGuardCode_Response", {}, eresult=88)
        return proto_reply("CAuthentication_UpdateAuthSessionWithSteamGuardCode_Response", {})
    http.route("POST", f"{AUTH}/UpdateAuthSessionWithSteamGuardCode", code)

    def poll(call):
        req = call.proto("CAuthentication_PollAuthSessionStatus_Request")
        assert req == {"client_id": 987654321, "request_id": b"\x01\x02\x03"}
        state["polls"] += 1
        if state["polls"] < poll_ready_after or (G.DeviceCode in confirmations and not state["codes"]
                                                  and G.DeviceConfirmation not in confirmations):
            return proto_reply("CAuthentication_PollAuthSessionStatus_Response", {})
        return proto_reply("CAuthentication_PollAuthSessionStatus_Response", {
            "refresh_token": refresh, "access_token": access, "account_name": "houwy", "new_guard_data": "guard-jwt"})
    http.route("POST", f"{AUTH}/PollAuthSessionStatus", poll)
    install_finalize_routes(http, state, refresh, access)
    return state


def install_finalize_routes(http, state, refresh, access, error=None):
    def finalize(call):
        state["finalize"] = call.form
        if error:
            return Reply(body={"error": error})
        assert call.form["nonce"] == refresh
        return Reply(body={"steamID": str(STEAMID), "transfer_info": [
            {"url": f"{COMMUNITY}/login/settoken", "params": {"nonce": "n1", "auth": "a1"}},
            {"url": "https://store.steampowered.com/login/settoken", "params": {"nonce": "n2", "auth": "a2"}},
        ]})
    http.route("POST", f"{LOGIN}/jwt/finalizelogin", finalize)

    def transfer(call):
        domain = call.url.split("/")[2]
        assert call.form["steamID"] == str(STEAMID)
        return Reply(body={"result": 1}, cookies={domain: {"steamLoginSecure": f"{STEAMID}%7C%7C{access}"}})
    http.route("POST", f"{COMMUNITY}/login/settoken", transfer)
    http.route("POST", "https://store.steampowered.com/login/settoken", transfer)
    http.route("GET", f"{COMMUNITY}/chat/clientjstoken", lambda c: Reply(body={"logged_in": True}))


async def test_full_login_with_device_code(fake_http):
    state = install_login_routes(fake_http, [G.DeviceCode, G.DeviceConfirmation])
    auth = SteamAuthenticator(fake_http)
    pending = await auth.begin("houwy", "hunter2")

    req = state["begin"]
    assert req["account_name"] == "houwy"
    assert req["website_id"] == "Community"
    assert req["persistence"] == 1 and req["remember_login"] is True and "platform_type" not in req
    assert req["device_details"]["platform_type"] == 2
    assert rsa.decrypt(base64.b64decode(req["encrypted_password"]), PRIV) == b"hunter2"
    assert req["encryption_timestamp"] == 1234567
    assert pending.code_type == G.DeviceCode

    await auth.submit_code(pending, "abc12")
    assert state["codes"][0]["code"] == "ABC12" and state["codes"][0]["code_type"] == G.DeviceCode
    tokens = await auth.wait_for_tokens(pending, timeout=5)
    assert tokens.steamid == STEAMID and tokens.account_name == "houwy" and tokens.guard_data == "guard-jwt"

    token = await auth.create_web_session(tokens.refresh_token, tokens.steamid)
    assert token == tokens.access_token
    assert state["finalize"]["sessionid"] == fake_http.get_cookie("steamcommunity.com", "sessionid")
    assert fake_http.get_cookie("store.steampowered.com", "sessionid") == state["finalize"]["sessionid"]


async def test_session_manager_login_persist_and_restore(fake_http, tmp_path):
    install_login_routes(fake_http, [G.DeviceCode])
    store = JsonStore(tmp_path / "session.json", private=True)
    changes = []
    mgr = SessionManager(fake_http, store, lambda: changes.append(mgr.state))
    await mgr.login("houwy", "hunter2")
    assert mgr.state == "awaiting_guard"
    assert mgr.public()["guard"]["code"] == "device"

    with pytest.raises(LoginError, match="not correct"):
        await mgr.submit_code("WRONG")
    assert mgr.state == "awaiting_guard"

    await mgr.submit_code("abc12")
    assert mgr.state == "authenticated"
    saved = store.load()
    assert saved["steamid"] == str(STEAMID) and saved["refresh_token"] and saved["access_token"]
    assert (tmp_path / "session.json").stat().st_mode & 0o077 == 0

    # A new process restores from the refresh token without a password.
    from .conftest import FakeHttp
    http2 = FakeHttp()
    install_finalize_routes(http2, {}, saved["refresh_token"], access_token())
    saved["access_token"] = access_token(exp_in=-10)  # expired -> must refresh
    store.save(saved)
    mgr2 = SessionManager(http2, store, lambda: None)
    await mgr2.restore()
    assert mgr2.state == "authenticated"
    assert any("finalizelogin" in c.url for c in http2.calls)


async def test_restore_with_rejected_refresh_token_asks_for_login(fake_http, tmp_path):
    store = JsonStore(tmp_path / "session.json")
    store.save({"account_name": "houwy", "steamid": str(STEAMID), "refresh_token": refresh_token(), "access_token": None})
    install_finalize_routes(fake_http, {}, None, None, error=26)
    mgr = SessionManager(fake_http, store, lambda: None)
    await mgr.restore()
    assert mgr.state == "expired"
    assert mgr.public()["account_name"] == "houwy"


async def test_restore_with_expired_refresh_token(fake_http, tmp_path):
    store = JsonStore(tmp_path / "session.json")
    store.save({"steamid": str(STEAMID), "refresh_token": refresh_token(exp_in=-5)})
    mgr = SessionManager(fake_http, store, lambda: None)
    await mgr.restore()
    assert mgr.state == "expired"
    assert not fake_http.calls


async def test_wrong_password_is_reported(fake_http, tmp_path):
    install_login_routes(fake_http, [G.DeviceCode])
    fake_http.routes.insert(0, ("POST", f"{AUTH}/BeginAuthSessionViaCredentials", lambda c: proto_reply(
        "CAuthentication_BeginAuthSessionViaCredentials_Response", {}, eresult=5)))
    mgr = SessionManager(fake_http, JsonStore(tmp_path / "s.json"), lambda: None)
    with pytest.raises(LoginError, match="Incorrect account name or password"):
        await mgr.login("houwy", "bad")
    assert mgr.state == "logged_out"


async def test_email_guard_triggers_checkdevice(fake_http, tmp_path):
    install_login_routes(fake_http, [G.EmailCode, G.MachineToken])
    seen = {}

    def checkdevice(call):
        seen.update(call.form)
        return Reply(body={"result": 29})  # machine token not accepted -> e-mail sent
    fake_http.route("POST", f"{LOGIN}/jwt/checkdevice", checkdevice)
    mgr = SessionManager(fake_http, JsonStore(tmp_path / "s.json"), lambda: None)
    await mgr.login("houwy", "pw")
    assert seen == {"clientid": "987654321", "steamid": str(STEAMID)}
    assert mgr.public()["guard"]["code"] == "email"


async def test_device_confirmation_completes_in_background(fake_http, tmp_path):
    install_login_routes(fake_http, [G.DeviceConfirmation], poll_ready_after=2)
    mgr = SessionManager(fake_http, JsonStore(tmp_path / "s.json"), lambda: None)
    await mgr.login("houwy", "pw")
    assert mgr.public()["guard"]["confirmation"] is True
    await mgr._confirm_task
    assert mgr.state == "authenticated"
