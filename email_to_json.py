"""Turn a raw email into the JSON body for POST /orders, ready to paste into Swagger.

Run (pick one):
  python email_to_json.py mail.txt               # read the email from a file
  python email_to_json.py mail.txt -o body.json  # ...and also save the JSON to a file
  Get-Clipboard | python email_to_json.py        # Windows PowerShell: copied email -> JSON
  pbpaste | python email_to_json.py              # macOS
  python email_to_json.py                        # paste the email, then Ctrl+Z + Enter (Windows)
                                                 # or Ctrl+D (macOS/Linux)
This script only prints JSON. It does not call the API.
"""
import argparse
import json
import sys


def email_to_json(raw: str) -> str:
    """Return the request body {"email": "<raw email>"} as valid, pretty-printed JSON."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return json.dumps({"email": text}, indent=2)  # escapes \n, quotes, backslashes, unicode


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw email -> JSON request body for POST /orders")
    parser.add_argument("file", nargs="?", help="text file holding the raw email (default: read stdin)")
    parser.add_argument("-o", "--output", help="also save the JSON to this file")
    args = parser.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8-sig") as f:
            raw = f.read()
    else:
        if sys.stdin.isatty():
            print("Paste the raw email, then press Ctrl+Z and Enter (Windows) or Ctrl+D (macOS/Linux):", file=sys.stderr)
        raw = sys.stdin.read()

    if not raw.strip():
        sys.exit("No email text received.")

    result = email_to_json(raw)
    print(result)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result + "\n")
        print(f"Saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
