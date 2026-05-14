"""Have I Been Pwned k-anonymity password breach check.

Fail-open: any error (network, timeout, parse) returns False so a transient
HIBP outage cannot block password sets for legitimate users.
"""
import hashlib
import logging

import requests

log = logging.getLogger("signage.hibp")

HIBP_URL = "https://api.pwnedpasswords.com/range/{prefix}"
HIBP_TIMEOUT_SECONDS = 2.0


def check_hibp_breach(password: str) -> bool:
    """Return True iff *password* appears in the HIBP breach corpus."""
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        resp = requests.get(
            HIBP_URL.format(prefix=prefix),
            headers={
                "Add-Padding": "true",
                "User-Agent": "khanshoof-signage",
            },
            timeout=HIBP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("hibp_unreachable: %s", exc)
        return False
    for line in resp.text.splitlines():
        hash_suffix, _, count = line.partition(":")
        if hash_suffix.strip() == suffix:
            try:
                return int(count.strip()) > 0
            except ValueError:
                return False
    return False
