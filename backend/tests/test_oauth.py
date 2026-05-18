"""Tests for the Phase 2.5i-1 OAuth 2.1 authorization server."""
from db import query_one, query_all


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
