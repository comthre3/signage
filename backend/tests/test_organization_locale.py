def test_patch_organization_locale_admin_succeeds(client, signed_up_org):
    token = signed_up_org["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "ar"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locale"] == "ar"


def test_patch_organization_locale_rejects_invalid(client, signed_up_org):
    token = signed_up_org["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "fr"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_locale"


def test_patch_organization_locale_requires_auth(client):
    resp = client.patch("/organizations/me", json={"locale": "ar"})
    assert resp.status_code == 401


def test_patch_organization_locale_requires_admin(client, signed_up_org):
    """Editor cannot change org locale."""
    import uuid as _uuid
    suffix = _uuid.uuid4().hex[:8]
    editor_email = f"editor-{suffix}@example.com"
    admin_token = signed_up_org["token"]
    r = client.post(
        "/users",
        json={"username": editor_email, "password": "testpass1", "role": "editor"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/auth/login",
        json={"username": editor_email, "password": "testpass1"},
    )
    editor_token = r.json()["token"]
    resp = client.patch(
        "/organizations/me",
        json={"locale": "ar"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert resp.status_code == 403


def test_signup_complete_response_includes_locale(signed_up_org):
    assert signed_up_org["org"]["locale"] == "en"


def test_login_response_includes_org_locale(client, signed_up_org, unique_business):
    r = client.post(
        "/auth/login",
        json={
            "username": unique_business["email"],
            "password": unique_business["password"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "organization" in body
    assert body["organization"]["locale"] == "en"


def test_me_response_includes_org_locale(client, signed_up_org):
    token = signed_up_org["token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "organization" in body
    assert body["organization"]["locale"] == "en"
