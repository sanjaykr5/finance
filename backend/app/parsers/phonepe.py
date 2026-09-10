"""Parser for PhonePe's "Transaction Statement" CSV export.

Expected shape (as downloaded from the PhonePe app, Account > Statement):

    Transaction Statement for +91XXXXXXXXXX
    Duration,01 Jan 2025 - 19 Aug 2026

    Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
    2025-01-05,	08:56,Paid to SBI cards and Payment services Pvt Ltd,T2501050855583627138256,450554942766,Debit,XXXXXX7512,6514.00
    ...
    This is an automatically generated statement. Customer(s) are requested to ...

Two header lines and a blank line precede the real CSV header, and a block of
disclaimer text follows the last data row instead of a clean EOF.

The "Credit/debit instrument" column is either a masked card/UPI id ending in
4 digits (e.g. "XXXXXX7512", "XXXX325601") or the literal "Account" when money
moved directly to/from the linked bank account with no card involved.
"""

import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from .base import ParsedTransaction

HEADER = (
    "Date,Time,Transaction Details,Transaction ID,UTR,"
    "Transaction Type,Credit/debit instrument,Amount"
)
FOOTER_MARKER = "This is an automatically generated statement"

# Column order of a data row, also used as the raw dict's keys.
FIELDS = (
    "date", "time", "details", "transaction_id",
    "utr", "transaction_type", "instrument", "amount",
)


class PhonePeParser:
    source = "Phonepe"

    def can_parse(self, filename: str, content: bytes) -> bool:
        if not filename.lower().endswith(".csv"):
            return False
        text = _decode(content)
        if text is None:
            return False
        return HEADER in text and "Transaction Statement for" in text

    def parse(
        self, content: bytes, password: str | None = None
    ) -> list[ParsedTransaction]:
        del password  # CSV export carries no password protection
        text = _decode(content)
        if text is None:
            raise ValueError("Unable to decode file as text")

        lines = text.splitlines()
        header_idx = next(
            (i for i, line in enumerate(lines) if line.startswith(HEADER)), None
        )
        if header_idx is None:
            raise ValueError("PhonePe CSV header not found")

        footer_idx = next(
            (
                i
                for i, line in enumerate(lines[header_idx + 1 :], start=header_idx + 1)
                if not line.strip() or line.startswith(FOOTER_MARKER)
            ),
            len(lines),
        )

        rows = csv.reader(lines[header_idx + 1 : footer_idx])
        return [t for row in rows if (t := self._parse_row(row)) is not None]

    def _parse_row(self, row: list[str]) -> ParsedTransaction | None:
        if len(row) != len(FIELDS):
            return None  # not a well-formed data row
        raw = dict(zip(FIELDS, (v.strip() for v in row)))

        try:
            txn_date = date.fromisoformat(raw["date"])
        except ValueError:
            return None

        try:
            amount = Decimal(raw["amount"])
        except InvalidOperation:
            return None

        if raw["transaction_type"] == "Debit":
            amount = -amount
            txn_type = "debit"
        elif raw["transaction_type"] == "Credit":
            txn_type = "credit"
        else:
            return None

        instrument = raw["instrument"]
        last4 = (
            instrument[-4:]
            if instrument != "Account" and instrument[-4:].isdigit()
            else None
        )

        return ParsedTransaction(
            txn_date=txn_date,
            description=raw["details"],
            amount=amount,
            txn_type=txn_type,
            account_last4=last4,
            instrument=instrument,
            txn_ref=raw["utr"] or None,
            txn_time=raw["time"] or None,
            transaction_id=raw["transaction_id"] or None,
            raw=raw,
        )


def _decode(content: bytes) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None
