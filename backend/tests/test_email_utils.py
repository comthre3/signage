import pytest

from email_utils import is_valid_email, send_via_resend, RESEND_ENDPOINT


# ── is_valid_email ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "addr",
    [
        "owner@example.com",
        "first.last@example.com",
        "first+tag@sub.example.co.uk",
        "abc123@khanshoof.com",
        "a@b.io",
    ],
)
def test_valid_emails(addr: str) -> None:
    assert is_valid_email(addr) is True


@pytest.mark.parametrize(
    "addr",
    [
        "",
        "no-at-sign",
        "@no-local.com",
        "no-domain@",
        "spaces in@example.com",
        "double@@example.com",
        "no-tld@example",
        "trailing-dot@example.",
        "owner@.example.com",
        "owner@example..com",
        "owner@-example.com",
        "owner@example-.com",
        "a" * 250 + "@example.com",  # local part > 64
        "owner@" + ("x" * 250) + ".com",  # domain too long
    ],
)
def test_invalid_emails(addr: str) -> None:
    assert is_valid_email(addr) is False


def test_email_is_case_insensitive_in_validation() -> None:
    assert is_valid_email("Owner@Example.COM") is True


# ── send_via_resend ───────────────────────────────────────────────────

class _StubResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """Captures the last httpx.post() call for assertions."""

    def __init__(self, response: _StubResponse):
        self._response = response
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_json: dict | None = None
        self.call_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url: str, headers: dict, json: dict, timeout: float):
        self.call_count += 1
        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        return self._response


def _patch_httpx(monkeypatch, response: _StubResponse) -> _StubClient:
    stub = _StubClient(response)
    import email_utils
    monkeypatch.setattr(email_utils.httpx, "Client", lambda *a, **kw: stub)
    return stub


def test_send_via_resend_posts_to_correct_endpoint(monkeypatch) -> None:
    stub = _patch_httpx(monkeypatch, _StubResponse(200, {"id": "evt_123"}))
    send_via_resend(
        api_key="re_test_xyz",
        from_addr="Khanshoof <noreply@khanshoof.com>",
        to="user@example.com",
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
    )
    assert stub.call_count == 1
    assert stub.last_url == RESEND_ENDPOINT


def test_send_via_resend_sets_bearer_auth(monkeypatch) -> None:
    stub = _patch_httpx(monkeypatch, _StubResponse(200, {"id": "evt_123"}))
    send_via_resend(
        api_key="re_test_xyz",
        from_addr="Khanshoof <noreply@khanshoof.com>",
        to="user@example.com",
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
    )
    assert stub.last_headers["Authorization"] == "Bearer re_test_xyz"
    assert stub.last_headers["Content-Type"] == "application/json"


def test_send_via_resend_payload_matches_resend_schema(monkeypatch) -> None:
    stub = _patch_httpx(monkeypatch, _StubResponse(200, {"id": "evt_123"}))
    send_via_resend(
        api_key="re_test_xyz",
        from_addr="Khanshoof <noreply@khanshoof.com>",
        to="user@example.com",
        subject="Your code",
        html="<p>123456</p>",
        text="123456",
    )
    body = stub.last_json
    assert body["from"] == "Khanshoof <noreply@khanshoof.com>"
    assert body["to"] == ["user@example.com"]
    assert body["subject"] == "Your code"
    assert body["html"] == "<p>123456</p>"
    assert body["text"] == "123456"


def test_send_via_resend_returns_message_id_on_success(monkeypatch) -> None:
    _patch_httpx(monkeypatch, _StubResponse(200, {"id": "evt_abc"}))
    result = send_via_resend(
        api_key="re_test_xyz",
        from_addr="Khanshoof <noreply@khanshoof.com>",
        to="user@example.com",
        subject="x",
        html="x",
        text="x",
    )
    assert result == "evt_abc"


def test_send_via_resend_raises_on_4xx(monkeypatch) -> None:
    _patch_httpx(monkeypatch, _StubResponse(422, {"message": "Invalid from"}))
    with pytest.raises(RuntimeError) as exc:
        send_via_resend(
            api_key="re_test_xyz",
            from_addr="bad@unverified.com",
            to="user@example.com",
            subject="x",
            html="x",
            text="x",
        )
    assert "422" in str(exc.value)


# ── send_signup_otp_email integration ─────────────────────────────────

def test_signup_otp_email_calls_resend_when_api_key_set(monkeypatch) -> None:
    captured: dict = {}

    def fake_send(*, api_key, from_addr, to, subject, html, text):
        captured["api_key"] = api_key
        captured["from_addr"] = from_addr
        captured["to"] = to
        captured["subject"] = subject
        captured["html"] = html
        captured["text"] = text
        return "evt_signup_1"

    import main
    monkeypatch.setattr(main, "send_via_resend", fake_send)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_signup")
    monkeypatch.setenv("RESEND_FROM", "Khanshoof <noreply@khanshoof.com>")

    main.send_signup_otp_email("user@example.com", "Acme Coffee", "123456")

    assert captured["api_key"] == "re_test_signup"
    assert captured["from_addr"] == "Khanshoof <noreply@khanshoof.com>"
    assert captured["to"] == "user@example.com"
    assert "123456" in captured["text"]
    assert "123456" in captured["html"]
    assert "Acme Coffee" in captured["html"] or "Acme Coffee" in captured["text"]


def test_signup_otp_email_falls_back_to_log_without_api_key(monkeypatch, caplog) -> None:
    called = {"count": 0}

    def fake_send(**kwargs):
        called["count"] += 1
        return "should_not_be_called"

    import main
    monkeypatch.setattr(main, "send_via_resend", fake_send)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    import logging as _logging
    with caplog.at_level(_logging.INFO, logger="signage"):
        main.send_signup_otp_email("user@example.com", "Acme", "654321")

    assert called["count"] == 0
    assert "654321" in caplog.text


def test_signup_otp_email_swallows_resend_failure(monkeypatch) -> None:
    def fake_send(**kwargs):
        raise RuntimeError("Resend API error 422: bad domain")

    import main
    monkeypatch.setattr(main, "send_via_resend", fake_send)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_signup")
    # Must NOT raise — signup should not 500 if email provider is flaky
    main.send_signup_otp_email("user@example.com", "Acme", "111111")
