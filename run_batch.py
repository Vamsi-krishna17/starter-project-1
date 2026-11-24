"""Send every email in the input folders to the RUNNING app (POST /orders) and save one JSON file.

This script only makes HTTP requests, exactly like Swagger does. It has no LLM, database or
business logic of its own, so start the app first:  docker compose up --build

Usage (from the project root):
  python run_batch.py                               # Input/labelled + Input/practice
  python run_batch.py data/input/labelled           # one or more folders
  python run_batch.py --base-url http://localhost:8000 -o output/batch_results.json

The API address can also be set with the API_BASE_URL environment variable.
Input files are only read, never changed, moved or deleted.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

DEFAULT_DIRS = ["Input/labelled", "Input/practice", "input/labelled", "input/practice",
                "data/input/labelled", "data/input/practice"]
FALLBACK_DIR = "data/emails"
DEFAULT_OUTPUT = "output/batch_results.json"
DEFAULT_BASE_URL = "http://localhost:8000"
ENDPOINT = "/orders"
DEFAULT_TIMEOUT = 120.0  # seconds; each request makes two LLM calls
SKIP_SUFFIXES = {".json", ".md", ".sql", ".py", ".gitkeep"}
REQUIRED_FIELDS = {"customer", "delivery_city", "deadline", "items", "status", "issues", "reply"}


def default_folders() -> list[Path]:
    """The input folders that exist (Windows treats Input/input as one folder: never list it twice)."""
    found: list[Path] = []
    for name in DEFAULT_DIRS:
        p = Path(name)
        if p.is_dir() and not any(os.path.samefile(p, q) for q in found):
            found.append(p)
    return found


def find_email_files(folders: list[Path]) -> list[tuple[str, Path]]:
    files = []
    for folder in folders:
        for path in sorted(p for p in folder.iterdir() if p.is_file()):
            if path.name.startswith(".") or path.suffix.lower() in SKIP_SUFFIXES:
                continue
            files.append((folder.name, path))
    return files


def _error(kind: str, message: str, http_status: int | None = None) -> dict:
    return {"type": kind, "message": message, "http_status": http_status}


def send_email(client: httpx.Client, raw: str) -> tuple[dict | None, dict | None]:
    """POST one email to /orders. Returns (api_response, None) or (None, error_details)."""
    try:
        resp = client.post(ENDPOINT, json={"email": raw})  # same body Swagger sends
    except httpx.TimeoutException:
        return None, _error("timeout", "The API did not answer within the timeout")
    except httpx.ConnectError:
        return None, _error("connection_error", f"Cannot connect to {client.base_url}. Is the app running?")
    except httpx.HTTPError as exc:
        return None, _error("request_error", f"{type(exc).__name__}: {exc}")

    if resp.status_code != 200:
        return None, _error("http_error", resp.text[:300], resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        return None, _error("invalid_json_response", "The API returned 200 but the body is not valid JSON", 200)
    if not isinstance(body, dict) or not REQUIRED_FIELDS <= body.keys():
        missing = sorted(REQUIRED_FIELDS - body.keys()) if isinstance(body, dict) else "not an object"
        return None, _error("invalid_response", f"Response does not match the order contract (missing: {missing})", 200)
    return body, None


def run_batch(files: list[tuple[str, Path]], client: httpx.Client) -> dict:
    results = []
    for folder, path in files:
        entry = {"folder": folder, "file": path.name, "response": None, "error": None}
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            entry["response"], entry["error"] = send_email(client, raw)
        except OSError as exc:
            entry["error"] = _error("file_read_error", f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # one bad email must never stop the batch
            entry["error"] = _error("unexpected_error", f"{type(exc).__name__}: {exc}")
        results.append(entry)

    ok = [r for r in results if r["response"] is not None]
    failed = [r for r in results if r["response"] is None]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_url": str(client.base_url).rstrip("/") + ENDPOINT,
        "total_emails": len(results),
        "successful": len(ok),
        "failed": len(failed),
        "status_counts": dict(Counter(r["response"]["status"] for r in ok)),
        "error_counts": dict(Counter(r["error"]["type"] for r in failed)),
        "per_folder": {
            folder: {
                "total": sum(1 for r in results if r["folder"] == folder),
                "successful": sum(1 for r in ok if r["folder"] == folder),
                "failed": sum(1 for r in failed if r["folder"] == folder),
            }
            for folder in dict.fromkeys(r["folder"] for r in results)
        },
    }
    return {"summary": summary, "results": results}


def save(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)  # creates output/ if missing
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send emails to POST /orders and save the results as JSON")
    parser.add_argument("folders", nargs="*", help="folders with email files")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=os.getenv("API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    names = args.folders or [str(p) for p in default_folders()] or [FALLBACK_DIR]
    folders = [Path(n) for n in names]
    missing = [str(f) for f in folders if not f.is_dir()]
    if missing:
        sys.exit(f"Folder not found: {', '.join(missing)}")

    files = find_email_files(folders)
    print(f"Found {len(files)} email file(s) in: {', '.join(names)}")
    print(f"Sending to {args.base_url.rstrip('/')}{ENDPOINT}")

    with httpx.Client(base_url=args.base_url, timeout=args.timeout, transport=transport) as client:
        report = run_batch(files, client)
    save(report, Path(args.output))

    s = report["summary"]
    print(f"Successful: {s['successful']}/{s['total_emails']} | failed: {s['failed']} | status counts: {s['status_counts']}")
    if s["error_counts"]:
        print(f"Errors: {s['error_counts']}")
    if s["error_counts"].get("connection_error"):
        print("Hint: start the app first with 'docker compose up --build' and check --base-url.")
    print(f"Saved to {args.output}")
    return 1 if s["total_emails"] == 0 or s["successful"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
