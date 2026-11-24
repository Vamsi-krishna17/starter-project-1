from datetime import date

from app.utils.email_parser import parse_email

RAW = "From: a@b.com\r\nSubject: Order\r\nDate: Wed, 18 Nov 2026 09:00:00 +0530\r\n\r\nPlease send 10 laptops.\r\n\r\nThanks"


def test_valid_email():
    p = parse_email(RAW)
    assert p.date == date(2026, 11, 18)
    assert p.subject == "Order"
    assert "Please send 10 laptops." in p.body and "Thanks" in p.body  # blank line inside body kept


def test_missing_date():
    assert parse_email("Subject: x\n\nhello").date is None


def test_invalid_date():
    assert parse_email("Date: not a date\n\nhello").date is None


def test_plain_text_without_headers_does_not_crash():
    assert isinstance(parse_email("just some text").body, str)
