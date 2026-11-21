"""Parse a raw email with Python's email module (never split on a blank line)."""
import email
from dataclasses import dataclass
from datetime import date
from email.message import Message
from email.utils import parsedate_to_datetime

from loguru import logger


@dataclass
class ParsedEmail:
    subject: str
    body: str
    date: date | None  # None when the Date header is missing or invalid


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return _decode(part)
        return ""
    return _decode(msg)


def parse_email(raw: str) -> ParsedEmail:
    msg = email.message_from_string(raw)
    body = _extract_body(msg).strip()
    subject = str(msg.get("Subject", "") or "").strip()

    sent: date | None = None
    header = msg.get("Date")
    if header:
        try:
            sent = parsedate_to_datetime(str(header)).date()
        except (TypeError, ValueError):
            logger.warning("Invalid Date header, treating as missing")
    else:
        logger.warning("Email has no Date header")
    return ParsedEmail(subject=subject, body=body, date=sent)
