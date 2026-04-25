"""Niupay KNET HTTP client.

Thin wrapper around the single Niupay endpoint used for payment creation.
Keeps the API key + mode in env; never logs the raw body.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

NIUPAY_URL = "https://niupay.me/api/requestKnet"


def _env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(f"{name} env var not set")
    return val


def create_knet_request(
    *,
    trackid: str,
    amount_kwd: int,
    response_url: str,
    success_url: str,
    error_url: str,
) -> dict[str, Any]:
    """POST to Niupay /api/requestKnet. Returns the parsed JSON body.

    Raises httpx.HTTPStatusError on non-2xx, httpx.RequestError on network failure.
    """
    payload = {
        "apikey":      _env("NIUPAY_API_KEY"),
        "type":        int(os.getenv("NIUPAY_MODE", "1")),
        "trackid":     trackid,
        "amount":      f"{amount_kwd}.000",
        "language":    1,
        "responseUrl": response_url,
        "successUrl":  success_url,
        "errorUrl":    error_url,
    }
    res = httpx.post(NIUPAY_URL, json=payload, timeout=15.0)
    res.raise_for_status()
    return res.json()
