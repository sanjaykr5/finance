"""Parser for HSBC ("Premier") credit card statement PDFs.

Password-protected like the HDFC parser, but a much simpler layout: every
transaction is one plain text line with no wrapping, so this parser works
directly off `page.extract_text()` rather than word positions:

    DDMON   DESCRIPTION LOCATION STATE/COUNTRY   AMOUNT[.XX]   [CR]
    07FEB   SKYDANCE HOSPITALITY BANGALORE KAR   3,700.00
    25FEB   CREDIT CARD PAYMENT                  4,552.90      CR

A trailing "CR" marks a credit (a payment or refund, reducing the amount
due); everything else is a debit — HSBC states this explicitly per line,
unlike HDFC's statement where it has to be inferred. The transaction date
has no year (just day + 3-letter month), so the year is resolved against
the statement's own "DD MON YYYY To DD MON YYYY" billing period.

The transactions table spans as many pages as it needs, each repeating the
same cardholder/card-number/page-number boilerplate above and below the
rows; the boilerplate simply doesn't match the transaction line shape, so
it's naturally skipped without needing to be pattern-matched and excluded
explicitly. The one exception is the "07MON NET OUTSTANDING BALANCE ..."
line right after the transaction table on its last page, which *would*
otherwise match the transaction shape — the scan stops as soon as it hits
"TOTAL PURCHASE OUTSTANDING" (which always precedes that line) specifically
to avoid it.

As with the HDFC parser, every parse cross-checks its own debit/credit
totals against the statement's own summary figures (its running
opening-balance / total-debits / total-credits / closing-balance line) and
refuses to return results on a mismatch, rather than risk silently
importing a misparsed statement.
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from .base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError

# "07FEB SKYDANCE HOSPITALITY BANGALORE KAR 3,700.00" or "... 4,552.90 CR".
# The description is whatever sits between the date and the final amount.
TXN_RE = re.compile(r"^(\d{2})([A-Z]{3})\s+(.+?)\s+([\d,]+\.\d{2})(\s+CR)?$")
STOP_RE = re.compile(r"^TOTAL PURCHASE OUTSTANDING")
PERIOD_RE = re.compile(r"(\d{2}) ([A-Z]{3}) (\d{4}) To (\d{2}) ([A-Z]{3}) (\d{4})")
OPENING_BALANCE_RE = re.compile(r"OPENING BALANCE\s+([\d,]+\.\d{2})")
CARD_RE = re.compile(r"(51xx xxxx xxxx \d{4})\s+[A-Z .]+")


class HSBCCreditCardParser:
    source = "HSBC Credit Card"

    def can_parse(self, filename: str, content: bytes) -> bool:
        # Encrypted content means there's nothing to sniff before parse()
        # actually decrypts it — see detect.py for how multiple PDF parsers
        # claiming the same file gets resolved.
        return filename.lower().endswith(".pdf") and content[:5] == b"%PDF-"

    def parse(
        self, content: bytes, password: str | None = None
    ) -> list[ParsedTransaction]:
        try:
            pdf = pdfplumber.open(io.BytesIO(content), password=password or "")
        except PDFPasswordIncorrect:
            raise PasswordRequiredError("PDF password required or incorrect")

        with pdf:
            full_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            if "HSBC" not in full_text or "OPENING BALANCE" not in full_text:
                raise UnsupportedFileError("Not an HSBC credit card statement")

            period_match = PERIOD_RE.search(full_text)
            if not period_match:
                raise ValueError("Could not locate this statement's billing period")
            period_start = _parse_period_date(period_match.group(1, 2, 3))
            period_end = _parse_period_date(period_match.group(4, 5, 6))

            card_match = CARD_RE.search(full_text)
            instrument = card_match.group(1) if card_match else None
            last4 = instrument[-4:] if instrument else None

            ob_match = OPENING_BALANCE_RE.search(full_text)
            if not ob_match:
                raise ValueError("Could not locate this statement's opening balance")
            summary_match = re.search(
                re.escape(ob_match.group(1))
                + r"\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
                full_text,
            )
            if not summary_match:
                raise ValueError(
                    "Could not locate this statement's running summary totals "
                    "to cross-check the parsed transactions against"
                )
            expected_debit = _to_decimal(summary_match.group(1))
            expected_credit = _to_decimal(summary_match.group(2))

            rows = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                stopped = False
                for line in text.splitlines():
                    if STOP_RE.match(line):
                        stopped = True
                        break
                    m = TXN_RE.match(line)
                    if m:
                        rows.append(_build_row(m, period_start, period_end))
                if stopped:
                    break

        txns = []
        debit_total = Decimal("0")
        credit_total = Decimal("0")
        for r in rows:
            if r["is_credit"]:
                credit_total += r["amount"]
            else:
                debit_total += r["amount"]
            txns.append(
                ParsedTransaction(
                    txn_date=r["date"],
                    description=r["description"],
                    amount=r["amount"] if r["is_credit"] else -r["amount"],
                    txn_type="credit" if r["is_credit"] else "debit",
                    account_last4=last4,
                    instrument=instrument,
                    raw={
                        "date": r["date"].isoformat(),
                        "description": r["description"],
                        "amount": str(r["amount"]),
                        "is_credit": r["is_credit"],
                    },
                )
            )

        if debit_total != expected_debit or credit_total != expected_credit:
            raise ValueError(
                "Parsed transaction totals don't match this statement's own "
                f"summary (debits: parsed {debit_total} vs stated "
                f"{expected_debit}; credits: parsed {credit_total} vs "
                f"stated {expected_credit}) — refusing to import a "
                "possibly-misparsed statement"
            )

        return txns


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _parse_period_date(parts: tuple[str, str, str]) -> date:
    day, month, year = parts
    return datetime.strptime(f"{day}{month}{year}", "%d%b%Y").date()


def _build_row(m: re.Match, period_start: date, period_end: date) -> dict:
    day, month, description, amount_str, cr = m.groups()
    year = _resolve_year(month, period_start, period_end)
    return {
        "date": datetime.strptime(f"{day}{month}{year}", "%d%b%Y").date(),
        "description": description,
        "amount": _to_decimal(amount_str),
        "is_credit": bool(cr),
    }


def _resolve_year(month_abbr: str, period_start: date, period_end: date) -> int:
    if month_abbr == period_start.strftime("%b").upper():
        return period_start.year
    if month_abbr == period_end.strftime("%b").upper():
        return period_end.year
    raise ValueError(
        f"Transaction month {month_abbr!r} falls outside this statement's "
        f"billing period ({period_start} to {period_end})"
    )
