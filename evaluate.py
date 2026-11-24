"""Run every labelled email through the real workflow and compare with answer_key.json.

Usage:  python evaluate.py [emails_dir] [answer_key.json]
Defaults: Input/labelled  answer_key-2.json   (SP1 regression: python evaluate.py <sp1 emails dir> answer_key-1.json)

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


def _num_equal(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 0.005


def compare_quote(actual: dict | None, expected: dict | None) -> list[str]:
    if expected is None:
        return [] if actual is None else ["quote: expected null"]
    if actual is None:
        return ["quote: got null"]
    misses = []
    for f in ("subtotal_inr", "delivery_inr", "total_inr"):
        if f in expected and not _num_equal(actual.get(f), expected[f]):
            misses.append(f"quote.{f}: got {actual.get(f)!r}, expected {expected[f]!r}")
    if "valid_until" in expected and actual.get("valid_until") != expected["valid_until"]:
        misses.append(f"quote.valid_until: got {actual.get('valid_until')!r}, expected {expected['valid_until']!r}")
    got = {l["product_code"]: l for l in actual.get("lines", [])}
    for exp in expected.get("lines", []):
        g = got.get(exp["product_code"])
        if g is None:
            misses.append(f"quote line {exp['product_code']} missing")
            continue
        for f in ("quantity", "unit_price_inr", "discount_percent", "line_total_inr"):
            if f in exp and not _num_equal(g.get(f), exp[f]):
                misses.append(f"quote.{exp['product_code']}.{f}: got {g.get(f)!r}, expected {exp[f]!r}")
    if len(got) != len(expected.get("lines", [])):
        misses.append(f"quote: {len(got)} line(s), expected {len(expected.get('lines', []))}")
    return misses


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
    if "quote" in expected:  # Starter Project 2; Starter Project 1 keys have no quote and are unaffected
        misses += compare_quote(actual.get("quote"), expected["quote"])
    return misses


def main() -> None:
    emails_dir = Path(sys.argv[1] if len(sys.argv) > 1 else next((d for d in ("Input/labelled", "input/labelled", "data/input/labelled", "data/emails") if Path(d).is_dir()), "Input/labelled"))
    key = load_key(Path(sys.argv[2] if len(sys.argv) > 2 else next((k for k in ("answer_key-2.json", "answer_key.json", "data/answer_key.json") if Path(k).is_file()), "answer_key-2.json")))
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
