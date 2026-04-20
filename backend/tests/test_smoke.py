def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_signup_creates_starter_org(signed_up_org):
    org = signed_up_org["org"]
    assert signed_up_org["token"]
    assert org["plan"] == "starter"
    assert org["screen_limit"] == 3
    assert org["subscription_status"] == "trialing"
    assert org["trial_ends_at"]
