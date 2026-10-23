#!/usr/bin/env python3
"""Enforce permanent-ID-only matching in the repository's existing update_xrank.py.

The project keeps its large archive collector at repository root. This small,
idempotent build helper updates only the stable_key() fallback so it never uses
the four-digit nameId as a substitute for a source identifier.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "update_xrank.py"

REPLACEMENT = '''def stable_key(raw_id: str, name_id: Any) -> str:
    """Use only the immutable source identifier; never fall back to 4-digit nameId."""
    raw_id = str(raw_id or "").strip()
    try:
        padded = raw_id + "=" * (-len(raw_id) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")
        match = re.search(r":u-([^:]+)$", decoded)
        if match:
            return "uid:" + match.group(1)
    except (ValueError, UnicodeEncodeError):
        pass
    # Opaque IDs are still identifiers. Do not use nameId, display name, or rank.
    return "id:" + raw_id
'''


def main() -> None:
    if not TARGET.is_file():
        raise SystemExit("ERROR: update_xrank.py がリポジトリ直下にありません")
    source = TARGET.read_text(encoding="utf-8")
    pattern = re.compile(r"(?ms)^def stable_key\([^\n]*\)(?:\s*->[^:\n]+)?:\n.*?(?=^def [A-Za-z_]\w*\(|\Z)")
    match = pattern.search(source)
    if match:
        current = match.group(0)
        # If the helper is already strict, leave it unchanged to keep this idempotent.
        if 'return "nameId:"' not in current and "return 'nameId:'" not in current:
            print("update_xrank.py: stable_key() already uses no nameId fallback")
            return
        updated = source[:match.start()] + REPLACEMENT + "\n" + source[match.end():]
        TARGET.write_text(updated, encoding="utf-8")
        print("update_xrank.py: changed stable_key() to permanent-ID-only mode")
        return
    # Some variants inline the fallback instead of exposing stable_key(). Do not
    # guess at unsupported source layouts, because an unsafe identity patch is worse.
    if re.search(r"return\s+[\"']nameId:[\"']", source):
        raise SystemExit("ERROR: update_xrank.py に nameId fallback がありますが、安全に自動修正できません。stable_key() の実装を確認してください")
    print("update_xrank.py: no nameId fallback found; leaving collector unchanged")


if __name__ == "__main__":
    main()
