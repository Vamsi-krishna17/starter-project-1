"""Run labelled emails through the real workflow and compare each field with an answer key.

Usage (project root; needs the database up and .env filled in, it uses the real workflow):
  python evaluate.py EMAILS_DIR ANSWER_KEY [--ignore quote] [--report output/reports/sp2.json]

  SP1 regression : python evaluate.py Input/labelled-1 answer_key-1.json --ignore quote --report output/reports/sp1_regression.json
  SP2 labelled   : python evaluate.py Input/labelled-2 answer_key-2.json --report output/reports/sp2_labelled.json

Prints one line per email and a total. --report saves the field-level details (expected vs actual,
the full API response of every email) as JSON. Answer keys and emails are only read.
NOTE: the README scoring formula is not applied here; the counts below are plain match counts.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

TOP_FIELDS = ("customer", "delivery_city", "deadline", "status")


def load_key(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {str(e.get("file") or e.get("id") or e.get("email")): e for e in raw}
    return {Path(k).stem: (v.get("expected", v) if isinstance(v, dict) else v) for k, v in raw.items()}


def _num_equal(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 0.005


def _result(field, expected, actual, ok) -> dict:
    return {"field": field, "expected": expected, "actual": actual, "match": bool(ok)}


def compare_fields(actual: dict, expected: dict, ignore: set[str] = frozenset()) -> list[dict]:
    """One entry per compared field: {field, expected, actual, match}."""
    out: list[dict] = []
    for f in TOP_FIELDS:
        if f in expected and f not in ignore:
            out.append(_result(f, expected[f], actual.get(f), actual.get(f) == expected[f]))
    if "issues" in expected and "issues" not in ignore:   # strict: same issues, any order
        out.append(_result("issues", expected["issues"], actual.get("issues"),
                           sorted(actual.get("issues") or []) == sorted(expected["issues"])))
    if "items" in expected and "items" not in ignore:
        got = {(i["product_name"] or "").lower(): i for i in actual.get("items", [])}
        for exp in expected["items"]:
            name = exp.get("product_name")
            g = got.get((name or "").lower())
            if g is None:
                out.append(_result(f"items[{name}]", "present", "missing", False))
                continue
            for f in ("product_code", "requested", "available"):
                if f in exp:
                    out.append(_result(f"items[{name}].{f}", exp[f], g.get(f), g.get(f) == exp[f]))
        if len(actual.get("items", [])) != len(expected["items"]):
            out.append(_result("items.count", len(expected["items"]), len(actual.get("items", [])), False))
    if "quote" in expected and "quote" not in ignore:
        out += _compare_quote(actual.get("quote"), expected["quote"])
    return out


def _compare_quote(actual, expected) -> list[dict]:
    if expected is None or actual is None:
        return [_result("quote", expected, actual, expected is None and actual is None)]
    out = []
    for f in ("subtotal_inr", "delivery_inr", "total_inr"):
        if f in expected:
            out.append(_result(f"quote.{f}", expected[f], actual.get(f), _num_equal(actual.get(f), expected[f])))
    if "valid_until" in expected:
        out.append(_result("quote.valid_until", expected["valid_until"], actual.get("valid_until"),
                           actual.get("valid_until") == expected["valid_until"]))
    got = {l["product_code"]: l for l in actual.get("lines", [])}
    for exp in expected.get("lines", []):
        code = exp["product_code"]
        g = got.get(code)
        if g is None:
            out.append(_result(f"quote.lines[{code}]", "present", "missing", False))
            continue
        for f in ("quantity", "unit_price_inr", "discount_percent", "line_total_inr"):
            if f in exp:
                out.append(_result(f"quote.lines[{code}].{f}", exp[f], g.get(f), _num_equal(g.get(f), exp[f])))
    if len(got) != len(expected.get("lines", [])):
        out.append(_result("quote.lines.count", len(expected.get("lines", [])), len(got), False))
    return out


def run_evaluation(emails_dir: Path, key: dict, process, ignore: set[str] = frozenset()) -> dict:
    """process(raw_email_text) -> OrderResponse-like object with model_dump()."""
    emails, per_field = [], defaultdict(lambda: {"compared": 0, "matched": 0})
    for path in sorted(p for p in emails_dir.iterdir() if p.is_file() and not p.name.startswith(".")):
        if path.stem not in key:
            continue
        entry = {"id": path.stem, "file": path.name, "pass": False, "error": None, "fields": [], "response": None}
        try:
            entry["response"] = actual = process(path.read_text(encoding="utf-8-sig")).model_dump(mode="json")
            entry["fields"] = compare_fields(actual, key[path.stem], ignore)
            entry["reply_present"] = bool((actual.get("reply") or "").strip())   # free text: not compared with the key
            entry["pass"] = all(f["match"] for f in entry["fields"])
        except Exception as exc:  # keep going; report the failure
            entry["error"] = f"{type(exc).__name__}: {exc}"
        for f in entry["fields"]:
            top = f["field"].split("[")[0].split(".")[0]
            per_field[top]["compared"] += 1
            per_field[top]["matched"] += f["match"]
        emails.append(entry)
    compared = sum(v["compared"] for v in per_field.values())
    matched = sum(v["matched"] for v in per_field.values())
    return {
        "emails_dir": str(emails_dir), "ignored_fields": sorted(ignore),
        "totals": {
            "emails": len(emails), "pass": sum(e["pass"] for e in emails), "fail": sum(not e["pass"] for e in emails),
            "errors": sum(e["error"] is not None for e in emails),
            "fields_compared": compared, "fields_matched": matched,
            "field_match_pct": round(100 * matched / compared, 1) if compared else None,
        },
        "per_field": dict(per_field), "emails": emails,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evaluate emails against an answer key")
    p.add_argument("emails_dir", nargs="?", default="Input/labelled-2")
    p.add_argument("answer_key", nargs="?", default="answer_key-2.json")
    p.add_argument("--ignore", default="", help="comma-separated fields to skip, e.g. quote")
    p.add_argument("--report", help="save the full field-level report as JSON (a new file; nothing is overwritten silently)")
    args = p.parse_args(argv)

    emails_dir, key_path = Path(args.emails_dir), Path(args.answer_key)
    for path in (emails_dir, key_path):
        if not path.exists():
            sys.exit(f"Missing: {path}")
    report_path = Path(args.report) if args.report else None
    if report_path and report_path.exists():
        sys.exit(f"Report already exists, not overwriting: {report_path} (choose another --report)")

    from app.api.orders import get_facade  # real workflow: same code path as POST /orders

    ignore = {f.strip() for f in args.ignore.split(",") if f.strip()}
    report = run_evaluation(emails_dir, load_key(key_path), get_facade().process_email, ignore)
    report["answer_key"] = str(key_path)
    for e in report["emails"]:
        bad = [f"{f['field']}: got {f['actual']!r}, expected {f['expected']!r}" for f in e["fields"] if not f["match"]]
        print(f"{'PASS' if e['pass'] else 'FAIL'}  {e['file']}" + (f"  -> ERROR {e['error']}" if e["error"] else "")
              + (("  -> " + "; ".join(bad)) if bad else ""))
    t = report["totals"]
    print(f"\nTOTAL: {t['pass']}/{t['emails']} emails fully correct | fields matched {t['fields_matched']}/{t['fields_compared']} ({t['field_match_pct']}%)")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print(f"Report saved to {report_path}")
    return 0 if t["emails"] else 1


if __name__ == "__main__":
    sys.exit(main())