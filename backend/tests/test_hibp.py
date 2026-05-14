"""Tests for the HIBP k-anonymity breach check."""
import hashlib
from unittest.mock import patch, MagicMock
import pytest

from hibp import check_hibp_breach, HIBP_TIMEOUT_SECONDS


def _sha1_upper(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest().upper()


def test_returns_true_when_suffix_matches_with_count():
    pw = "Password123"
    sha1 = _sha1_upper(pw)
    suffix = sha1[5:]
    body = f"AAAAA:1\n{suffix}:42\nBBBBB:0\n"
    fake = MagicMock(text=body)
    fake.raise_for_status = MagicMock()
    with patch("hibp.requests.get", return_value=fake):
        assert check_hibp_breach(pw) is True


def test_returns_false_when_no_suffix_matches():
    fake = MagicMock(text="AAAAA:1\nBBBBB:1\n")
    fake.raise_for_status = MagicMock()
    with patch("hibp.requests.get", return_value=fake):
        assert check_hibp_breach("very-unlikely-Khanshoof-2026") is False


def test_fail_open_on_network_error(caplog):
    with patch("hibp.requests.get", side_effect=ConnectionError("boom")):
        with caplog.at_level("WARNING"):
            assert check_hibp_breach("anything") is False
    assert any("hibp_unreachable" in rec.getMessage() for rec in caplog.records)


def test_fail_open_on_http_error():
    fake = MagicMock()
    fake.raise_for_status = MagicMock(side_effect=Exception("500"))
    with patch("hibp.requests.get", return_value=fake):
        assert check_hibp_breach("anything") is False


def test_sends_only_5_char_prefix_in_url():
    pw = "Khanshoof2026Test"
    expected_prefix = _sha1_upper(pw)[:5]
    fake = MagicMock(text="")
    fake.raise_for_status = MagicMock()
    with patch("hibp.requests.get", return_value=fake) as mocked:
        check_hibp_breach(pw)
    called_url = mocked.call_args[0][0]
    assert called_url.endswith(f"/range/{expected_prefix}")
    # Full hash and password must NOT appear in URL
    assert _sha1_upper(pw) not in called_url
    assert pw not in called_url


def test_timeout_is_two_seconds():
    fake = MagicMock(text="")
    fake.raise_for_status = MagicMock()
    with patch("hibp.requests.get", return_value=fake) as mocked:
        check_hibp_breach("any")
    assert mocked.call_args.kwargs.get("timeout") == HIBP_TIMEOUT_SECONDS
    assert HIBP_TIMEOUT_SECONDS == 2.0


def test_zero_count_does_not_count_as_breach():
    pw = "edge"
    suffix = _sha1_upper(pw)[5:]
    body = f"{suffix}:0\n"
    fake = MagicMock(text=body)
    fake.raise_for_status = MagicMock()
    with patch("hibp.requests.get", return_value=fake):
        assert check_hibp_breach(pw) is False
