#!/usr/bin/env python3
"""Verify en.json and ar.json for each app have matching keys with non-empty values.

Exits 0 if all good, 1 otherwise. Run from repo root:
    python3 scripts/check_i18n.py
"""
import json
import sys
from pathlib import Path

APPS = ["frontend", "landing", "player"]
ROOT = Path(__file__).resolve().parent.parent


def check_app(app: str) -> list[str]:
    en_path = ROOT / app / "i18n" / "en.json"
    ar_path = ROOT / app / "i18n" / "ar.json"
    if not en_path.exists():
        return [f"{app}: missing en.json"]
    if not ar_path.exists():
        return [f"{app}: missing ar.json"]
    en = json.loads(en_path.read_text())
    ar = json.loads(ar_path.read_text())
    errors: list[str] = []
    for key in en:
        if key not in ar:
            errors.append(f"{app}: key missing in ar.json: {key}")
        elif not str(ar[key]).strip():
            errors.append(f"{app}: key empty in ar.json: {key}")
    for key in ar:
        if key not in en:
            errors.append(f"{app}: key in ar.json but not en.json: {key}")
    return errors


def main() -> int:
    all_errors: list[str] = []
    for app in APPS:
        all_errors.extend(check_app(app))
    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        return 1
    print(f"i18n OK across {', '.join(APPS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
