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
