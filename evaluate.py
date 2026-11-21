"""Run every labelled email through the real workflow and compare with answer_key.json.

Usage:  python evaluate.py [emails_dir] [answer_key.json]
Defaults: data/emails  data/answer_key.json

NOTE: the exact scoring rules are in the data pack README. This script uses a simple
field-by-field comparison; adjust `compare()` to match the README if it differs.
"""
import json
import sys
from pathlib import Path

from app.api.orders import get_facade


def load_key(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {str(e.get("file") or e.get("id") or e.get("email")): e for e in raw}
    return {Path(k).stem: (v.get("expected", v) if isinstance(v, dict) else v) for k, v in raw.items()}


def compare(actual: dict, expected: dict) -> list[str]:
    misses = []
    for field in ("status", "customer", "delivery_city", "deadline"):
        if field in expected and actual.get(field) != expected[field]:
            misses.append(f"{field}: got {actual.get(field)!r}, expected {expected[field]!r}")
    if "items" in expected:
        got = {(i["product_name"] or "").lower(): i for i in actual["items"]}
        for exp in expected["items"]:
            g = got.get((exp.get("product_name") or "").lower())
            if g is None:
                misses.append(f"item {exp.get('product_name')!r} missing")
                continue
            for f in ("product_code", "requested", "available"):
                if f in exp and g.get(f) != exp[f]:
                    misses.append(f"{exp.get('product_name')}.{f}: got {g.get(f)!r}, expected {exp[f]!r}")
    return misses


def main() -> None:
    emails_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/emails")
    key = load_key(Path(sys.argv[2] if len(sys.argv) > 2 else "data/answer_key.json"))
    facade = get_facade()
    passed = total = 0
    for path in sorted(p for p in emails_dir.iterdir() if p.is_file()):
        if path.stem not in key:
            continue  # practice emails have no key: run them through the API and read by hand
        total += 1
        try:
            actual = facade.process_email(path.read_text(encoding="utf-8")).model_dump()
            misses = compare(actual, key[path.stem])
        except Exception as exc:  # keep going, report the failure
            misses = [f"ERROR {type(exc).__name__}: {exc}"]
        passed += not misses
        print(f"{'PASS' if not misses else 'FAIL'}  {path.name}" + ("" if not misses else "  -> " + "; ".join(misses)))
    print(f"\nTOTAL: {passed}/{total} emails fully correct")


if __name__ == "__main__":
    main()
