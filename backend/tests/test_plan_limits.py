def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_starter_plan_blocks_fourth_screen(client, signed_up_org):
    """Starter trial limit is 3 screens. The 4th POST must return 402."""
    token = signed_up_org["token"]
    assert signed_up_org["org"]["screen_limit"] == 3

    for i in range(3):
        r = client.post(
            "/screens",
            json={"name": f"ok-{i}"},
            headers=_bearer(token),
        )
        assert r.status_code == 200, f"screen {i + 1}/3 should succeed: {r.text}"

    r = client.post(
        "/screens",
        json={"name": "over-limit"},
        headers=_bearer(token),
    )
    assert r.status_code == 402, f"expected 402, got {r.status_code}: {r.text}"
    assert "limit" in r.json()["detail"].lower()


def test_organization_screens_used_counter(client, signed_up_org):
    token = signed_up_org["token"]

    r = client.get("/organization", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["screens_used"] == 0

    for name in ("s1", "s2"):
        r = client.post("/screens", json={"name": name}, headers=_bearer(token))
        assert r.status_code == 200, r.text

    r = client.get("/organization", headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["screens_used"] == 2
