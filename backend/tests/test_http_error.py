from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from main import http_error


def test_http_error_returns_httpexception():
    err = http_error(400, "email_taken", "Email is already registered")
    assert isinstance(err, HTTPException)
    assert err.status_code == 400
    assert err.detail == {"code": "email_taken", "message": "Email is already registered"}


def test_http_error_renders_through_fastapi():
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise http_error(403, "insufficient_role", "Insufficient role")

    client = TestClient(app)
    resp = client.get("/boom")
    assert resp.status_code == 403
    assert resp.json() == {"detail": {"code": "insufficient_role", "message": "Insufficient role"}}
