"""Parser for HDFC Bank savings/current account statement PDFs.

Password-protected like the HDFC credit card statement (same bank, same kind
of account-number-derived password), but this one is a genuine ruled table —
pdfplumber's `extract_tables()` reads it cleanly page by page, no word-
position reconstruction needed:

    Date       Narration                          Chq./Ref No.  Value Date  Withdrawal Amount  Deposit Amount  Closing Balance*
    05/01/2025 UPI-SBI CARDS AND PAYMEN-SBICARDS   450554942766  05/01/2025  6,514.00           0.00            4,03,793.73
               ANDPAYMENTS.BDPG@ICICI-ICIC0DC0099
               -450554942766-PAY

A long narration wraps across 2-3 lines *within its own table cell* — HDFC's
generator hard-wraps at a fixed character width with no regard for word
boundaries and no space inserted at the break, so reconstructing it is just
`"".join(cell.split("\\n"))`, not a `" ".join`.

Only one of Withdrawal/Deposit is ever nonzero per row (bank statements, not
credit cards, so no separate rewards/points column); which one is nonzero is
what decides credit vs debit — there's no keyword heuristic to get wrong. The
header row ("Date  Narration  ...") only appears once, at the top of page
1's table; every other page's table starts straight into transaction rows.

As with the other HDFC/HSBC parsers, every parse cross-checks its own
debit/credit totals *and* transaction counts against the statement's own
"STATEMENT SUMMARY" block (Dr Count, Cr Count, Debits, Credits) and refuses
to return any results if they don't match exactly — so a row shape this
parser doesn't recognise yet fails loudly instead of silently importing a
wrong number.
"""

import io
import re
from datetime import datetime
from decimal import Decimal

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from .base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
ACCOUNT_RE = re.compile(r"Account number\s*:\s*(\d+)")
SUMMARY_RE = re.compile(
    r"Opening Balance\s+Dr Count\s+Cr Count\s+Debits\s+Credits\s+Closing Balance\s*\n"
    r"([\d,]+\.\d{2})\s+(\d+)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})"
)


class HDFCBankStatementParser:
    source = "HDFC Bank"

    def can_parse(self, filename: str, content: bytes) -> bool:
        # There's no naming convention to sniff and the content is
        # encrypted, so this only narrows down to "some PDF" — parse() does
        # the real validation once it can actually read the decrypted text.
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
            if "HDFC BANK LIMITED" not in full_text or "Narration" not in full_text:
                raise UnsupportedFileError("Not an HDFC bank account statement")

            account_match = ACCOUNT_RE.search(full_text)
            instrument = "Account"
            last4 = account_match.group(1)[-4:] if account_match else None

            rows: list[dict] = []
            for page in pdf.pages:
                for table in page.extract_tables():
                    for r in table:
                        if not r or not r[0] or not DATE_RE.match(r[0]):
                            continue  # header row, or a stray non-txn row
                        rows.append(_build_row(r))

            summary_match = SUMMARY_RE.search(full_text)
            if not summary_match:
                raise ValueError(
                    "Could not locate this statement's summary totals to "
                    "cross-check the parsed transactions against"
                )
            expected_dr_count = int(summary_match.group(2))
            expected_cr_count = int(summary_match.group(3))
            expected_debits = _to_decimal(summary_match.group(4))
            expected_credits = _to_decimal(summary_match.group(5))

        txns = []
        debit_total = Decimal("0")
        credit_total = Decimal("0")
        dr_count = 0
        cr_count = 0
        for r in rows:
            is_credit = r["deposit"] != 0
            if is_credit:
                credit_total += r["deposit"]
                cr_count += 1
            else:
                debit_total += r["withdrawal"]
                dr_count += 1
            txns.append(
                ParsedTransaction(
                    txn_date=r["date"],
                    description=r["narration"],
                    amount=r["deposit"] if is_credit else -r["withdrawal"],
                    txn_type="credit" if is_credit else "debit",
                    account_last4=last4,
                    instrument=instrument,
                    txn_ref=r["ref_no"],
                    raw={
                        "date": r["date"].isoformat(),
                        "narration": r["narration"],
                        "ref_no": r["ref_no"],
                        "value_date": r["value_date"],
                        "withdrawal": str(r["withdrawal"]),
                        "deposit": str(r["deposit"]),
                        "closing_balance": str(r["closing_balance"]),
                    },
                )
            )

        if (
            debit_total != expected_debits
            or credit_total != expected_credits
            or dr_count != expected_dr_count
            or cr_count != expected_cr_count
        ):
            raise ValueError(
                "Parsed transaction totals don't match this statement's own "
                f"summary (debits: parsed {debit_total} ({dr_count} txns) vs "
                f"stated {expected_debits} ({expected_dr_count} txns); "
                f"credits: parsed {credit_total} ({cr_count} txns) vs stated "
                f"{expected_credits} ({expected_cr_count} txns)) — refusing "
                "to import a possibly-misparsed statement"
            )

        return txns


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _build_row(r: list) -> dict:
    date_str, narration_cell, ref_no, value_date, withdrawal, deposit, closing_balance = r
    return {
        "date": datetime.strptime(date_str, "%d/%m/%Y").date(),
        # HDFC hard-wraps mid-word at a fixed column width with no space at
        # the break — see module docstring — so lines are joined bare.
        "narration": "".join((narration_cell or "").split("\n")),
        "ref_no": ref_no or None,
        "value_date": value_date or None,
        "withdrawal": _to_decimal(withdrawal or "0"),
        "deposit": _to_decimal(deposit or "0"),
        "closing_balance": _to_decimal(closing_balance or "0"),
    }
