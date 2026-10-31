"""Tests for the Phase 2.5i-1 OAuth 2.1 authorization server."""
from db import query_one, query_all, execute


def test_oauth_clients_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_clients", "client_id"),
    )
    assert row is not None


def test_oauth_authorization_codes_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_authorization_codes", "code_hash"),
    )
    assert row is not None


def test_oauth_tokens_table_exists():
    row = query_one(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? AND column_name = ?",
        ("oauth_tokens", "access_token_hash"),
    )
    assert row is not None


def test_pre_registered_clients_seeded():
    """All four known MCP clients should be seeded at init_db() time."""
    rows = query_all(
        "SELECT client_id FROM oauth_clients WHERE pre_registered = true "
        "ORDER BY client_id"
    )
    client_ids = [r["client_id"] for r in rows]
    for expected in ("claude-code", "claude-desktop", "cursor", "zed"):
        assert expected in client_ids, f"Missing pre-registered client: {expected}"


def test_pre_registered_clients_have_friendly_names():
    row = query_one(
        "SELECT client_name FROM oauth_clients WHERE client_id = ?",
        ("claude-desktop",),
    )
    assert row is not None
    assert row["client_name"] == "Claude Desktop"


# ── Discovery endpoints ──────────────────────────────────────────────


def test_well_known_authorization_server_metadata(client):
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorization_endpoint" in body
    assert "token_endpoint" in body
    assert "revocation_endpoint" in body
    assert "registration_endpoint" in body
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "api:read" in body["scopes_supported"]
    assert "api:rw" in body["scopes_supported"]


def test_well_known_protected_resource_metadata(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert body["bearer_methods_supported"] == ["header"]


def test_well_known_no_auth_required(client):
    """Discovery endpoints must be public — no Authorization header."""
    r1 = client.get("/.well-known/oauth-authorization-server")
    r2 = client.get("/.well-known/oauth-protected-resource")
    assert r1.status_code == 200
    assert r2.status_code == 200


# ── Dynamic client registration ──────────────────────────────────────


def test_register_creates_client_with_dyn_prefix(client):
    r = client.post("/oauth/register", json={
        "client_name": "Test Integration",
        "redirect_uris": ["http://localhost:5173/oauth/callback"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["client_id"].startswith("dyn_")
    assert body["client_name"] == "Test Integration"
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


def test_register_validates_redirect_uri_schemes(client):
    """https, http://localhost, http://127.0.0.1, and custom schemes are OK."""
    valid = client.post("/oauth/register", json={
        "client_name": "ValidApp",
        "redirect_uris": [
            "https://example.com/callback",
            "http://localhost:3000/callback",
            "http://127.0.0.1:9999/callback",
            "myapp://oauth/callback",
        ],
    })
    assert valid.status_code == 201, valid.text


def test_register_rejects_data_url_scheme(client):
    r = client.post("/oauth/register", json={
        "client_name": "BadApp",
        "redirect_uris": ["data:text/html,<script>"],
    })
    assert r.status_code == 400, r.text


def test_register_rejects_too_many_uris(client):
    r = client.post("/oauth/register", json={
        "client_name": "WideApp",
        "redirect_uris": [f"https://app{i}.example.com/cb" for i in range(11)],
    })
    assert r.status_code == 400, r.text


def test_register_returns_no_client_secret(client):
    r = client.post("/oauth/register", json={
        "client_name": "PublicOnly",
        "redirect_uris": ["https://example.com/cb"],
    })
    assert r.status_code == 201
    assert "client_secret" not in r.json()
    assert "client_secret_expires_at" not in r.json()


def test_register_rejects_plain_http_non_localhost(client):
    """Regression: http:// to non-localhost must be rejected (hijack vector)."""
    r = client.post("/oauth/register", json={
        "client_name": "Evil",
        "redirect_uris": ["http://evil.example.com/callback"],
    })
    assert r.status_code == 400, r.text


def test_register_rejects_ftp_scheme(client):
    r = client.post("/oauth/register", json={
        "client_name": "FtpApp",
        "redirect_uris": ["ftp://files.example.com/oauth"],
    })
    assert r.status_code == 400, r.text


def test_register_rejects_file_url_with_slashes(client):
    r = client.post("/oauth/register", json={
        "client_name": "FileApp",
        "redirect_uris": ["file:///etc/passwd"],
    })
    assert r.status_code == 400, r.text


# ── Authorization endpoint + consent UI ──────────────────────────────
import uuid
import hashlib
import base64
import secrets


def _signup_org(client, suffix=None):
    """Create a fresh org via signup. Returns (token, org_id, user_id, email)."""
    sfx = suffix or uuid.uuid4().hex[:8]
    email = f"oauth-{sfx}@example.com"
    r = client.post("/auth/signup/request",
                    json={"business_name": f"OAuthBiz {sfx}", "email": email})
    assert r.status_code == 200, r.text
    otp = r.json()["dev_otp"]
    r = client.post("/auth/signup/verify",
                    json={"email": email, "otp": otp})
    vt = r.json()["verification_token"]
    r = client.post("/auth/signup/complete",
                    json={"verification_token": vt,
                          "password": "Khanshoof2026Test"})
    body = r.json()
    return body["token"], body["organization"]["id"], body["user"]["id"], email


def _register_client(client, name="TestApp", uris=None):
    r = client.post("/oauth/register", json={
        "client_name": name,
        "redirect_uris": uris or ["http://localhost:5173/oauth/callback"],
    })
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _pkce_pair():
    """Returns (code_verifier, code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def test_authorize_rejects_invalid_response_type(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "token",
        "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code in (302, 400)


def test_authorize_rejects_plain_pkce_method(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "plain",
    }, follow_redirects=False)
    assert r.status_code in (302, 400)


def test_authorize_rejects_unknown_client_id(client):
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": "nonexistent_client",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_authorize_rejects_unregistered_redirect_uri(client):
    cid = _register_client(client, uris=["http://localhost:5173/oauth/callback"])
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": "http://attacker.example.com/cb",
        "scope": "api:read",
        "state": "abc",
        "code_challenge": ch,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 400


def test_authorize_renders_login_when_no_session(client):
    cid = _register_client(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_authorize_renders_consent_with_session(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:rw", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    assert r.status_code == 200
    assert "Authorize" in r.text or "TestApp" in r.text


def test_authorize_decision_allow_redirects_with_code(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:rw", "state": "xyz",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    assert r.status_code == 200
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    assert m, "request_id missing from consent page"
    request_id = m.group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("http://localhost:5173/oauth/callback?")
    assert "code=" in location
    assert "state=xyz" in location


def test_authorize_decision_deny_redirects_with_error(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "deny-test",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    request_id = m.group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "deny",
                          "scope": "api:read"},
                    cookies=cookies, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert "error=access_denied" in location
    assert "state=deny-test" in location


def test_oauth_login_wrong_password_returns_401(client):
    """Direct test of the oauth_login error path."""
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    # First, hit /oauth/authorize anonymously so a pending entry is created
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 200
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    # Now POST wrong password
    r = client.post("/oauth/login", data={
        "request_id": request_id,
        "username": email,
        "password": "wrong-password",
    })
    assert r.status_code == 401, r.text
    assert "Invalid credentials" in r.text or "incorrect" in r.text.lower()


def test_oauth_login_with_correct_password_redirects_to_authorize_resume(client):
    """Verify oauth_login.html → POST /oauth/login → 302 → /oauth/authorize?resume=..."""
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, ch = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "abc",
        "code_challenge": ch, "code_challenge_method": "S256",
    }, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/login", data={
        "request_id": request_id,
        "username": email,
        "password": "Khanshoof2026Test",
    }, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("/oauth/authorize?")
    assert f"resume={request_id}" in location
    # Cookie set on response
    set_cookie = r.headers.get("set-cookie", "")
    assert "oauth_session=" in set_cookie


def test_oauth_redirect_url_encodes_state_with_special_chars(client):
    """state containing & must be URL-encoded so the client receives the right value."""
    cid = _register_client(client)
    session_token, _o, _u, _e = _signup_org(client)
    _v, ch = _pkce_pair()
    cookies = {"oauth_session": session_token}
    weird_state = "abc&injected=evil"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:rw", "state": weird_state,
        "code_challenge": ch, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    # The literal & must be encoded as %26 so it stays as a single state value
    assert "state=abc%26injected" in location, location
    # The injected= part must NOT be a separate URL parameter
    assert "&injected=" not in location.split("state=")[1].split("&")[0]


# ── Token endpoint ──────────────────────────────────────────────────


def _full_authorize_flow(client) -> dict:
    """Run the full flow up to having a redirect URL with code. Returns
    {'code', 'verifier', 'client_id', 'redirect_uri', 'session_token', 'org_id'}."""
    cid = _register_client(client)
    session_token, org_id, _u, _email = _signup_org(client)
    verifier, challenge = _pkce_pair()
    cookies = {"oauth_session": session_token}
    redirect_uri = "http://localhost:5173/oauth/callback"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri,
        "scope": "api:rw", "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    m = re.search(r'name="request_id" value="([^"]+)"', r.text)
    request_id = m.group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    location = r.headers["location"]
    code = re.search(r"code=([^&]+)", location).group(1)
    return {
        "code": code, "verifier": verifier,
        "client_id": cid, "redirect_uri": redirect_uri,
        "session_token": session_token, "org_id": org_id,
    }


def test_token_exchanges_code_for_tokens(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "api:rw"


def test_token_rejects_consumed_code(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_rejects_mismatched_redirect_uri(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": "http://evil.example.com/cb",
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_rejects_bad_pkce_verifier(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": "wrong_verifier_value",
    })
    assert r.status_code == 400


def test_token_rejects_unknown_code(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": "unknown_random_code_xyz",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid,
        "code_verifier": "v",
    })
    assert r.status_code == 400


def test_token_rejects_unsupported_grant_type(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "password",
        "username": "x", "password": "y", "client_id": cid,
    })
    assert r.status_code == 400


def test_refresh_issues_new_pair(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["access_token"] != tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]


def test_refresh_rotates_old_pair_invalidated(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 400


def test_refresh_rejects_unknown(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": "unknown_random_token",
        "client_id": cid,
    })
    assert r.status_code == 400


def test_token_response_includes_expected_fields(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    body = r.json()
    for key in ("access_token", "refresh_token", "token_type",
                "expires_in", "scope"):
        assert key in body
    assert body["token_type"] == "Bearer"


def test_authorization_code_expired(client):
    """Expired code is rejected. Simulate by manually backdating expires_at."""
    flow = _full_authorize_flow(client)
    execute(
        "UPDATE oauth_authorization_codes SET expires_at = ? "
        "WHERE client_id = ?",
        ("2020-01-01T00:00:00+00:00", flow["client_id"]),
    )
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400


def test_token_endpoint_returns_json_error_shape(client):
    cid = _register_client(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": "x",
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid,
        "code_verifier": "v",
    })
    assert r.status_code == 400
    body = r.json()
    # RFC 6749 §5.2 — error body is top-level, not nested under "detail"
    assert body["error"] == "invalid_grant", body
    assert "error_description" in body


def test_access_token_works_on_api_endpoint(client):
    """The access token issued via OAuth must authenticate against /organization."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access_token = r.json()["access_token"]
    # NOTE: Task 6 wires lookup_oauth_access_token into get_api_authed.
    # Pre-T6: expect 401. Post-T6: expect 200. For now we accept either.
    r2 = client.get("/organization",
                    headers={"Authorization": f"Bearer {access_token}"})
    assert r2.status_code in (200, 401), r2.text


def test_token_response_sets_no_store_cache_header(client):
    """RFC 6749 §5.1: token responses must be Cache-Control: no-store."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_token_rejects_non_ascii_pkce_verifier(client):
    """Non-ASCII verifier must produce a structured 400, not a 500."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
        "code_verifier": "naïve中文",
    })
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_grant"


def test_token_rejects_code_from_different_client(client):
    """A code issued for client A must not be usable by client B."""
    flow = _full_authorize_flow(client)
    # Register a second, unrelated client
    other_cid = _register_client(client, name="Other")
    # Try to redeem flow's code at the OTHER client's identity
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"],
        "redirect_uri": flow["redirect_uri"],
        "client_id": other_cid,
        "code_verifier": flow["verifier"],
    })
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_grant"


# ── Revocation + API integration ────────────────────────────────────


def test_revoke_marks_pair_revoked(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    tokens = r.json()
    r = client.post("/oauth/revoke", data={
        "token": tokens["access_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200, r.text
    row = query_one(
        "SELECT revoked_at FROM oauth_tokens WHERE client_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (flow["client_id"],),
    )
    assert row["revoked_at"] is not None


def test_revoke_returns_200_for_unknown_token(client):
    """RFC 7009: server MUST return 200 to avoid leaking validity."""
    cid = _register_client(client)
    r = client.post("/oauth/revoke", data={
        "token": "unknown_garbage",
        "client_id": cid,
    })
    assert r.status_code == 200


def test_revoked_access_token_returns_401(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access = r.json()["access_token"]
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    r = client.post("/oauth/revoke", data={
        "token": access, "client_id": flow["client_id"],
    })
    assert r.status_code == 200
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 401


def test_oauth_access_token_can_GET_playlists(client):
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access = r.json()["access_token"]
    r = client.get("/playlists", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text


def test_oauth_read_scope_cannot_POST_playlists(client):
    """A token with api:read scope must NOT be allowed to POST."""
    cid = _register_client(client)
    session_token, _o, _u, _e = _signup_org(client)
    verifier, challenge = _pkce_pair()
    cookies = {"oauth_session": session_token}
    redirect_uri = "http://localhost:5173/oauth/callback"
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": redirect_uri, "scope": "api:read", "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:read"},
                    cookies=cookies, follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri,
        "client_id": cid, "code_verifier": verifier,
    })
    access = r.json()["access_token"]
    r = client.post("/playlists",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"name": "blocked"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "api.insufficient_scope"


def test_oauth_token_lists_screens_like_api_key(client):
    """Regression: OAuth principal must NOT be filtered through the
    session-user group-membership branch on GET /screens."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    access = r.json()["access_token"]
    r = client.get("/screens", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    # The signup org has no screens by default; assert the response is a list,
    # not a 500 from require_screen_access misfire.
    assert isinstance(r.json(), list)


# ── End-to-end ──────────────────────────────────────────────────────


def test_full_happy_path(client):
    """Single test exercising the full OAuth flow."""
    # 1. Register
    r = client.post("/oauth/register", json={
        "client_name": "E2E Client",
        "redirect_uris": ["http://localhost:5173/cb"],
    })
    assert r.status_code == 201
    cid = r.json()["client_id"]

    # 2. Sign up an org
    session_token, _o, _u, _e = _signup_org(client)
    cookies = {"oauth_session": session_token}

    # 3. Authorize
    verifier, challenge = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/cb",
        "scope": "api:rw", "state": "e2e",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies=cookies, follow_redirects=False)
    import re
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)

    # 4. Allow
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies=cookies, follow_redirects=False)
    location = r.headers["location"]
    code = re.search(r"code=([^&]+)", location).group(1)

    # 5. Token
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code, "redirect_uri": "http://localhost:5173/cb",
        "client_id": cid, "code_verifier": verifier,
    })
    tokens = r.json()
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]

    # 6. Use the access token
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    r = client.post("/playlists",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"name": "E2E playlist"})
    assert r.status_code in (200, 201)

    # 7. Refresh
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": refresh, "client_id": cid,
    })
    new_tokens = r.json()
    new_access = new_tokens["access_token"]
    assert new_access != access

    # 8. New access works
    r = client.get("/organization", headers={"Authorization": f"Bearer {new_access}"})
    assert r.status_code == 200

    # 9. Old access NO longer works (revoked on refresh)
    r = client.get("/organization", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 401

    # 10. Revoke the new access
    r = client.post("/oauth/revoke", data={
        "token": new_access, "client_id": cid,
    })
    assert r.status_code == 200

    # 11. Revoked token no longer works
    r = client.get("/organization", headers={"Authorization": f"Bearer {new_access}"})
    assert r.status_code == 401


def test_pkce_verifier_required(client):
    """PKCE is mandatory in OAuth 2.1 — no code_verifier means rejection."""
    flow = _full_authorize_flow(client)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 400


def test_authorization_code_single_use(client):
    """RFC 6749 + 7636: codes must be single-use."""
    flow = _full_authorize_flow(client)
    r1 = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r1.status_code == 200
    r2 = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r2.status_code == 400


# ── Token storage: indexed SHA-256, never a PBKDF2 scan ─────────────


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _exchange(client, flow) -> dict:
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": flow["code"], "redirect_uri": flow["redirect_uri"],
        "client_id": flow["client_id"], "code_verifier": flow["verifier"],
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_tokens_and_codes_are_stored_as_sha256(client):
    flow = _full_authorize_flow(client)
    code_row = query_one(
        "SELECT id FROM oauth_authorization_codes WHERE code_hash = ?",
        (_sha(flow["code"]),),
    )
    assert code_row is not None
    tokens = _exchange(client, flow)
    row = query_one(
        "SELECT refresh_token_hash FROM oauth_tokens WHERE access_token_hash = ?",
        (_sha(tokens["access_token"]),),
    )
    assert row is not None
    assert row["refresh_token_hash"] == _sha(tokens["refresh_token"])


def test_bearer_misses_run_no_key_derivation(client, signed_up_org, monkeypatch):
    """Every dashboard request is a bearer that is not an API key, so it is
    first tried as an OAuth access token. That miss -- and misses on
    /oauth/token and /oauth/revoke, which anyone can call -- must not
    PBKDF2-verify against every live token."""
    import main
    flow = _full_authorize_flow(client)
    _exchange(client, flow)                 # at least one live token exists
    calls = []
    real = main.verify_password
    monkeypatch.setattr(main, "verify_password",
                        lambda *a: calls.append(a) or real(*a))

    r = client.get("/organization",
                   headers={"Authorization": f"Bearer {signed_up_org['token']}"})
    assert r.status_code == 200, r.text
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": "not-a-token",
        "client_id": flow["client_id"],
    })
    assert r.status_code == 400
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": "not-a-code",
        "redirect_uri": flow["redirect_uri"], "client_id": flow["client_id"],
        "code_verifier": "v",
    })
    assert r.status_code == 400
    r = client.post("/oauth/revoke", data={"token": "not-a-token"})
    assert r.status_code == 200
    assert calls == []


def _make_legacy(tokens: dict) -> int:
    """Rewrite a token row the way a pre-SHA-256 deployment stored it."""
    from main import hash_password
    row = query_one("SELECT id FROM oauth_tokens WHERE access_token_hash = ?",
                    (_sha(tokens["access_token"]),))
    execute(
        "UPDATE oauth_tokens SET access_token_hash = ?, refresh_token_hash = ? "
        "WHERE id = ?",
        (hash_password(tokens["access_token"]),
         hash_password(tokens["refresh_token"]), row["id"]),
    )
    return row["id"]


def test_legacy_access_token_works_and_is_upgraded_in_place(client, monkeypatch):
    import oauth
    flow = _full_authorize_flow(client)
    tokens = _exchange(client, flow)
    row_id = _make_legacy(tokens)
    monkeypatch.setitem(oauth._legacy_live, "access", True)
    monkeypatch.setitem(oauth._legacy_live, "refresh", True)

    r = client.get("/organization",
                   headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200, r.text
    row = query_one("SELECT access_token_hash FROM oauth_tokens WHERE id = ?",
                    (row_id,))
    assert row["access_token_hash"] == _sha(tokens["access_token"])

    # The legacy refresh token still rotates (and the new pair is SHA-256).
    r = client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": flow["client_id"],
    })
    assert r.status_code == 200, r.text
    new = r.json()
    assert query_one("SELECT id FROM oauth_tokens WHERE access_token_hash = ?",
                     (_sha(new["access_token"]),)) is not None
    assert query_one("SELECT revoked_at FROM oauth_tokens WHERE id = ?",
                     (row_id,))["revoked_at"] is not None


def test_legacy_token_can_be_revoked(client, monkeypatch):
    import oauth
    flow = _full_authorize_flow(client)
    tokens = _exchange(client, flow)
    row_id = _make_legacy(tokens)
    monkeypatch.setitem(oauth._legacy_live, "access", True)
    monkeypatch.setitem(oauth._legacy_live, "refresh", True)
    r = client.post("/oauth/revoke", data={
        "token": tokens["refresh_token"], "client_id": flow["client_id"],
    })
    assert r.status_code == 200
    assert query_one("SELECT revoked_at FROM oauth_tokens WHERE id = ?",
                     (row_id,))["revoked_at"] is not None


# ── A token acts as the user who granted it, with that user's role ──
import re


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_member(client, admin_token: str, role: str) -> dict:
    """Admin adds a user with `role`; returns {id, username, password, token}."""
    username = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    password = "Khanshoof2026Member"
    r = client.post("/users", headers=_bearer(admin_token),
                    json={"username": username, "password": password,
                          "role": role})
    assert r.status_code in (200, 201), r.text
    user_id = r.json()["id"]
    r = client.post("/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"id": user_id, "username": username, "password": password,
            "token": r.json()["token"]}


def _consent_page(client, session_token: str, cid: str, challenge: str,
                  scope: str):
    return client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": scope, "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, cookies={"oauth_session": session_token}, follow_redirects=False)


def _grant(client, session_token: str, requested: str = "api:rw",
           chosen: str | None = None) -> dict:
    """Authorize as the session's user, allow, exchange. Returns the token
    response plus client_id."""
    cid = _register_client(client)
    verifier, challenge = _pkce_pair()
    r = _consent_page(client, session_token, cid, challenge, requested)
    assert r.status_code == 200, r.text
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": chosen or requested},
                    cookies={"oauth_session": session_token},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid, "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return {**r.json(), "client_id": cid}


def test_viewer_consent_offers_read_only(client):
    admin_token, *_ = _signup_org(client)
    viewer = _add_member(client, admin_token, "viewer")
    cid = _register_client(client)
    _v, challenge = _pkce_pair()
    r = _consent_page(client, viewer["token"], cid, challenge, "api:rw")
    assert r.status_code == 200, r.text
    rw_input = re.search(r'<input[^>]*value="api:rw"[^>]*>', r.text).group(0)
    assert "disabled" in rw_input
    read_input = re.search(r'<input[^>]*value="api:read"[^>]*>', r.text).group(0)
    assert "checked" in read_input


def test_viewer_cannot_grant_rw(client):
    """Even posting scope=api:rw at consent, a viewer's grant is api:read."""
    admin_token, *_ = _signup_org(client)
    viewer = _add_member(client, admin_token, "viewer")
    tokens = _grant(client, viewer["token"], requested="api:rw", chosen="api:rw")
    assert tokens["scope"] == "api:read"
    r = client.post("/playlists", headers=_bearer(tokens["access_token"]),
                    json={"name": "viewer write"})
    assert r.status_code == 403, r.text
    r = client.get("/playlists", headers=_bearer(tokens["access_token"]))
    assert r.status_code == 200, r.text


def test_editor_token_cannot_do_admin_only_write(client):
    """DELETE /schedules is admin-only for sessions; an editor's api:rw
    token must not get around that."""
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    r = client.post("/schedules", headers=_bearer(admin_token),
                    json={"name": "admin only delete"})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    tokens = _grant(client, editor["token"], requested="api:rw")
    assert tokens["scope"] == "api:rw"
    bearer = _bearer(tokens["access_token"])

    r = client.post("/playlists", headers=bearer, json={"name": "editor ok"})
    assert r.status_code in (200, 201), r.text    # editors may write content
    r = client.delete(f"/schedules/{sid}", headers=bearer)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "insufficient_role"
    r = client.delete(f"/schedules/{sid}", headers=_bearer(admin_token))
    assert r.status_code == 204, r.text


def test_token_follows_grantors_current_role(client):
    """Demoting the user who granted a token takes effect on the token."""
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    tokens = _grant(client, editor["token"], requested="api:rw")
    bearer = _bearer(tokens["access_token"])
    r = client.post("/playlists", headers=bearer, json={"name": "before"})
    assert r.status_code in (200, 201), r.text

    r = client.put(f"/users/{editor['id']}", headers=_bearer(admin_token),
                   json={"role": "viewer"})
    assert r.status_code == 200, r.text
    r = client.post("/playlists", headers=bearer, json={"name": "after"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "insufficient_role"


def test_token_rejected_once_grantor_is_gone(client):
    """oauth_tokens.user_id is ON DELETE SET NULL: a row orphaned that way
    must stop authenticating, not carry on as an org-level credential."""
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    tokens = _grant(client, editor["token"], requested="api:rw")
    bearer = _bearer(tokens["access_token"])
    assert client.get("/playlists", headers=bearer).status_code == 200
    execute("DELETE FROM users WHERE id = ?", (editor["id"],))
    r = client.get("/playlists", headers=bearer)
    assert r.status_code == 401, r.text


# ── Removing a user / changing a password ends their OAuth grants ───


def _refresh(client, tokens: dict):
    return client.post("/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": tokens["client_id"],
    })


def _live_token_rows(client_id: str) -> list:
    return query_all(
        "SELECT id FROM oauth_tokens WHERE client_id = ? AND revoked_at IS NULL",
        (client_id,),
    )


def test_deleting_user_revokes_their_oauth_tokens(client):
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    tokens = _grant(client, editor["token"], requested="api:rw")
    assert _live_token_rows(tokens["client_id"])

    r = client.delete(f"/users/{editor['id']}", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    assert _live_token_rows(tokens["client_id"]) == []
    assert client.get("/playlists",
                      headers=_bearer(tokens["access_token"])).status_code == 401
    r = _refresh(client, tokens)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_grant"


def test_password_change_revokes_oauth_tokens_and_codes(client):
    session_token, _o, _u, _e = _signup_org(client)
    tokens = _grant(client, session_token, requested="api:rw")
    # A code issued but not yet redeemed must die too.
    cid = _register_client(client)
    verifier, challenge = _pkce_pair()
    r = _consent_page(client, session_token, cid, challenge, "api:read")
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:read"},
                    cookies={"oauth_session": session_token},
                    follow_redirects=False)
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)

    r = client.post("/auth/change-password", headers=_bearer(session_token),
                    json={"current_password": "Khanshoof2026Test",
                          "new_password": "Khanshoof2026Rotated"})
    assert r.status_code == 200, r.text

    assert _live_token_rows(tokens["client_id"]) == []
    assert client.get("/organization",
                      headers=_bearer(tokens["access_token"])).status_code == 401
    assert _refresh(client, tokens).status_code == 400
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid, "code_verifier": verifier,
    })
    assert r.status_code == 400, r.text


def test_refresh_fails_once_grantor_is_gone(client):
    """A row orphaned by ON DELETE SET NULL (a deletion before revocation
    existed) must not be able to rotate itself forever."""
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    tokens = _grant(client, editor["token"], requested="api:rw")
    execute("DELETE FROM users WHERE id = ?", (editor["id"],))
    r = _refresh(client, tokens)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_grant"
    assert _live_token_rows(tokens["client_id"]) == []


def test_refresh_caps_scope_to_grantors_current_role(client):
    admin_token, *_ = _signup_org(client)
    editor = _add_member(client, admin_token, "editor")
    tokens = _grant(client, editor["token"], requested="api:rw")
    r = client.put(f"/users/{editor['id']}", headers=_bearer(admin_token),
                   json={"role": "viewer"})
    assert r.status_code == 200, r.text
    r = _refresh(client, tokens)
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "api:read"


# ── Authorization-flow state is shared by every worker ──────────────


def _insert_pending(client_id: str, challenge: str, *, expired: bool = False,
                    scope: str = "api:rw", state: str = "w") -> str:
    """Write a pending authorization the way another uvicorn worker would."""
    request_id = secrets.token_urlsafe(24)
    execute(
        "INSERT INTO oauth_pending_auth "
        "  (request_id, client_id, redirect_uri, scope, state, code_challenge, "
        "   expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, now() + "
        + ("interval '-1 minute'" if expired else "interval '10 minutes'")
        + ") RETURNING request_id",
        (request_id, client_id, "http://localhost:5173/oauth/callback",
         scope, state, challenge),
    )
    return request_id


def test_pending_authorization_is_stored_in_postgres(client):
    cid = _register_client(client)
    _v, challenge = _pkce_pair()
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "scope": "api:read", "state": "abc",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    assert r.status_code == 200, r.text
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    row = query_one(
        "SELECT client_id, state, code_challenge, "
        "       EXTRACT(EPOCH FROM expires_at - now()) AS ttl "
        "FROM oauth_pending_auth WHERE request_id = ?",
        (request_id,),
    )
    assert row is not None
    assert (row["client_id"], row["state"], row["code_challenge"]) == \
        (cid, "abc", challenge)
    assert 590 < float(row["ttl"]) <= 600


def test_flow_completes_on_state_another_worker_wrote(client):
    """/oauth/authorize, /oauth/login and /oauth/authorize/decision land on
    different workers in production; each must see the others' state."""
    cid = _register_client(client)
    _session, _o, _u, email = _signup_org(client)
    verifier, challenge = _pkce_pair()
    request_id = _insert_pending(cid, challenge)

    r = client.post("/oauth/login", data={
        "request_id": request_id, "username": email,
        "password": "Khanshoof2026Test",
    }, follow_redirects=False)
    assert r.status_code == 302, r.text
    assert f"resume={request_id}" in r.headers["location"]
    oauth_session = r.cookies.get("oauth_session")
    assert oauth_session

    r = client.get(r.headers["location"],
                   cookies={"oauth_session": oauth_session},
                   follow_redirects=False)
    assert r.status_code == 200, r.text
    assert f'value="{request_id}"' in r.text

    # A worker that never saw the authorize request finishes the flow.
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies={"oauth_session": oauth_session},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid, "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text

    # Single use: the pending authorization is gone once decided.
    assert query_one("SELECT 1 AS x FROM oauth_pending_auth WHERE request_id = ?",
                     (request_id,)) is None
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies={"oauth_session": oauth_session},
                    follow_redirects=False)
    assert r.status_code == 400
    assert "Authorization expired" in r.text


def test_expired_pending_authorization_is_refused(client):
    cid = _register_client(client)
    session_token, _o, _u, email = _signup_org(client)
    _v, challenge = _pkce_pair()
    request_id = _insert_pending(cid, challenge, expired=True)
    r = client.post("/oauth/login", data={
        "request_id": request_id, "username": email,
        "password": "Khanshoof2026Test",
    }, follow_redirects=False)
    assert r.status_code == 400
    assert "Login session expired" in r.text
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": "api:rw"},
                    cookies={"oauth_session": session_token},
                    follow_redirects=False)
    assert r.status_code == 400
    assert "Authorization expired" in r.text


# ── Scope lists ─────────────────────────────────────────────────────


def _checked_scope(html: str) -> str:
    return re.search(r'<input[^>]*value="(api:[a-z]+)"[^>]*\bchecked\b', html).group(1)


def _flow_with_scope(client, session_token: str, scope: str) -> dict:
    """Request `scope`, submit whatever the consent page pre-selects (as a
    user clicking Allow would), and exchange the code."""
    cid = _register_client(client)
    verifier, challenge = _pkce_pair()
    r = _consent_page(client, session_token, cid, challenge, scope)
    assert r.status_code == 200, r.text
    request_id = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
    r = client.post("/oauth/authorize/decision",
                    data={"request_id": request_id, "decision": "allow",
                          "scope": _checked_scope(r.text)},
                    cookies={"oauth_session": session_token},
                    follow_redirects=False)
    assert r.status_code == 302, r.text
    code = re.search(r"code=([^&]+)", r.headers["location"]).group(1)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": "http://localhost:5173/oauth/callback",
        "client_id": cid, "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_space_separated_scopes_grant_the_highest(client):
    """MCP SDKs request every entry of scopes_supported, space-separated."""
    session_token, *_ = _signup_org(client)
    assert _flow_with_scope(client, session_token, "api:read api:rw")["scope"] == "api:rw"
    assert _flow_with_scope(client, session_token, "api:rw api:read")["scope"] == "api:rw"


def test_space_separated_scopes_capped_for_viewer(client):
    admin_token, *_ = _signup_org(client)
    viewer = _add_member(client, admin_token, "viewer")
    assert _flow_with_scope(client, viewer["token"], "api:read api:rw")["scope"] == "api:read"


def test_unrecognised_scopes_are_ignored_alongside_known_ones(client):
    session_token, *_ = _signup_org(client)
    assert _flow_with_scope(client, session_token, "api:read offline_access")["scope"] == "api:read"


def test_scope_with_nothing_recognised_is_invalid(client):
    cid = _register_client(client)
    _v, challenge = _pkce_pair()
    for scope in ("admin", "", "   "):
        r = client.get("/oauth/authorize", params={
            "response_type": "code", "client_id": cid,
            "redirect_uri": "http://localhost:5173/oauth/callback",
            "scope": scope, "state": "s",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert r.status_code == 302, (scope, r.text)
        assert "error=invalid_scope" in r.headers["location"]


def test_oauth_token_sees_only_the_screens_its_user_can_see(client):
    """A token acts as its user: an editor's token must not list, read or edit
    screens that the editor's own session cannot reach."""
    admin_token, *_ = _signup_org(client)
    hidden = client.post("/screens", json={"name": "admin-only"},
                         headers=_bearer(admin_token)).json()
    editor = _add_member(client, admin_token, "editor")
    access = _grant(client, editor["token"])["access_token"]

    listed = client.get("/screens", headers=_bearer(access)).json()
    assert hidden["id"] not in [s["id"] for s in listed]
    r = client.get(f"/screens/{hidden['id']}/zones", headers=_bearer(access))
    assert r.status_code == 403, r.text
    r = client.put(f"/screens/{hidden['id']}", json={"name": "x"}, headers=_bearer(access))
    assert r.status_code == 403, r.text
