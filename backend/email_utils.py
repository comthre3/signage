"""Email validation + Resend transactional email sender.

Pure functions; safe to import from main.py and tests without side effects.
Network calls are isolated inside `send_via_resend()` so tests can patch
`httpx.Client`.
"""
import re

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0

# RFC-lite: practical regex that catches the malformed addresses we see in
# signup attempts without dragging in `email-validator`. Length caps mirror
# RFC 5321 (local ≤64, domain ≤253). Domain labels can't start/end with `-`
# and we forbid consecutive dots and leading/trailing dots in either side.
_LOCAL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")


def is_valid_email(email: str) -> bool:
    if not email or not isinstance(email, str):
        return False
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if len(local) > 64 or len(domain) > 253:
        return False
    if ".." in local or ".." in domain:
        return False
    if local.startswith(".") or local.endswith("."):
        return False
    if not _LOCAL_RE.match(local):
        return False
    if "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith("."):
        return False
    labels = domain.split(".")
    if any(not _LABEL_RE.match(label) for label in labels):
        return False
    if len(labels[-1]) < 2:  # require a real TLD
        return False
    return True


def send_via_resend(
    *,
    api_key: str,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
) -> str:
    """POST to Resend's /emails endpoint. Returns the message id on 2xx.

    Raises RuntimeError on non-2xx responses; caller decides whether to
    swallow the failure (e.g. signup OTP) or surface it.
    """
    payload = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client() as client:
        response = client.post(
            RESEND_ENDPOINT,
            headers=headers,
            json=payload,
            timeout=RESEND_TIMEOUT_SECONDS,
        )
    if not (200 <= response.status_code < 300):
        raise RuntimeError(
            f"Resend API error {response.status_code}: {response.text}"
        )
    return response.json().get("id", "")
