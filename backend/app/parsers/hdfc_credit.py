"""Parser for HDFC Bank ("Regalia") credit card statement PDFs.

These are password-protected PDFs (the cardholder's own statement password,
supplied by the caller — this parser cannot guess it). Each statement has a
"Domestic Transactions" table and, only in months with foreign-currency
spend, an "International Transactions" table, both sharing one column
layout:

    DATE & TIME   TRANSACTION DESCRIPTION            REWARDS   AMOUNT      PI
    24/03/2026| 15:15   Adobe Systems SoftwareI Bangalore   + 8   C 382.32   l

"C" in the extracted text is HDFC's rupee glyph (₹) coming through as a
plain "C" because of the statement's embedded font encoding — not a typo,
and not touched here since amounts are parsed out of it numerically anyway.

The date/time and amount normally sit on one line, but longer descriptions
(GST line items in particular) wrap onto a second line that HDFC's layout
renders *around* the anchor line rather than strictly below it — the second
line can appear either above or below the date/amount line depending on how
the row's height balances out. So rather than splitting the page's text into
lines, this parser works from each word's raw (x, y) position: it finds
every date/time "anchor" on the page, then for each one pulls in every word
vertically closer to that anchor than to its neighbouring anchors, and
sorts those words top-to-bottom to reconstruct the description in the
right order regardless of which side of the anchor they rendered on.

Every transaction is a debit (increases the amount due) except payments
back to the card, which HDFC always describes starting with "AUTOPAY" or
similar. As a safety net beyond that keyword heuristic, this parser
cross-checks its own debit/credit totals against the statement's own
printed "PURCHASES/DEBIT" and "PAYMENTS/CREDITS RECEIVED" summary figures
for the current billing cycle, and refuses to return any results if they
don't match to the cent — so a transaction shape this parser doesn't
recognise yet fails loudly instead of silently importing a wrong number.
"""

import io
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from .base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError

# A word's x0 can differ from its column header's x0 by a hair (sub-point
# floating point slop from glyph metrics), which is enough to flip a naive
# `>=` column-boundary check for the very first character of a cell. All
# column comparisons below are padded by this margin.
_EPS = 1.0

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}\|?$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
# "+ 8 C 382.32": reward points earned, then the (always unsigned) amount.
AMOUNT_WITH_POINTS_RE = re.compile(r"^\+\s*(\d+)\s*C\s*([\d,]+\.\d{2})$")
# "+ C 11,749.00" (no points, e.g. a payment) or plain "C 68.77" (a fee/tax
# line, which HDFC doesn't even print a "+" placeholder for).
AMOUNT_PLAIN_RE = re.compile(r"^\+?\s*C\s*([\d,]+\.\d{2})$")
# "C11,749.47 C11,749.00 + C382.32 + C0.00 =" → previous dues, payments/
# credits received, purchases/debit, finance charges, all for the current
# billing cycle — used to cross-check the parsed rows below.
SUMMARY_RE = re.compile(
    r"C([\d,]+\.\d{2})\s+C([\d,]+\.\d{2})\s*\+\s*C([\d,]+\.\d{2})\s*\+\s*C([\d,]+\.\d{2})\s*="
)
CARD_RE = re.compile(r"Credit Card No\.\s*(\S+)")
REF_RE = re.compile(r"Ref#\s*([A-Za-z0-9]+)")

# Keywords that mark a row as money coming back onto the card (a credit)
# rather than a purchase/fee (a debit). Only "AUTOPAY ..." has been seen in
# practice; the rest are a safety net for refunds/reversals this parser
# hasn't encountered yet. The summary cross-check below is what actually
# guarantees correctness, not this list.
CREDIT_KEYWORDS = ("AUTOPAY", "PAYMENT RECEIVED", "REFUND", "REVERSAL", "CASHBACK")

# Phrases (as consecutive words) that mark the end of a transactions table,
# so the last row's word-band doesn't bleed into whatever summary content
# follows it on the same page.
STOP_PHRASES = [
    ("Rewards", "Program", "Points", "Summary"),
    ("TRANSACTIONS", "TOTAL", "AMOUNT"),
    ("GST", "Summary"),
    ("*Transaction", "time", "captured"),
]


class HDFCCreditCardParser:
    source = "HDFC Credit Card"

    def can_parse(self, filename: str, content: bytes) -> bool:
        # There's no naming convention to sniff (statements are typically
        # saved as e.g. "April_2026.pdf") and the content is encrypted, so
        # this only narrows down to "some PDF" — parse() does the real
        # validation once it can actually read the decrypted text.
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
            if "HDFC Bank Credit Cards" not in full_text or "Credit Card No." not in full_text:
                raise UnsupportedFileError("Not an HDFC credit card statement")

            card_match = CARD_RE.search(full_text)
            instrument = card_match.group(1) if card_match else None
            last4 = instrument[-4:] if instrument else None

            rows: list[dict] = []
            for page in pdf.pages:
                rows.extend(_extract_rows(page))

            summary_match = SUMMARY_RE.search(full_text)
            if not summary_match:
                raise ValueError(
                    "Could not locate this statement's summary totals to "
                    "cross-check the parsed transactions against"
                )
            payments_credits = _to_decimal(summary_match.group(2))
            purchases_debit = _to_decimal(summary_match.group(3))

        txns = []
        debit_total = Decimal("0")
        credit_total = Decimal("0")
        for r in rows:
            is_credit = _is_credit(r["description"])
            if is_credit:
                credit_total += r["amount"]
            else:
                debit_total += r["amount"]
            ref_match = REF_RE.search(r["description"])
            txns.append(
                ParsedTransaction(
                    txn_date=r["date"],
                    description=r["description"],
                    amount=r["amount"] if is_credit else -r["amount"],
                    txn_type="credit" if is_credit else "debit",
                    account_last4=last4,
                    instrument=instrument,
                    txn_ref=ref_match.group(1) if ref_match else None,
                    txn_time=r["time"],
                    raw={
                        "date": r["date"].isoformat(),
                        "time": r["time"],
                        "description": r["description"],
                        "reward_points": r["reward_points"],
                        "amount": str(r["amount"]),
                    },
                )
            )

        if debit_total != purchases_debit or credit_total != payments_credits:
            raise ValueError(
                "Parsed transaction totals don't match this statement's own "
                f"summary (debits: parsed {debit_total} vs stated "
                f"{purchases_debit}; credits: parsed {credit_total} vs "
                f"stated {payments_credits}) — refusing to import a "
                "possibly-misparsed statement"
            )

        return txns


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _is_credit(description: str) -> bool:
    upper = description.upper()
    return any(k in upper for k in CREDIT_KEYWORDS)


def _find_headers(words: list[dict]) -> list[dict]:
    """Locate each transactions-table header row (there's one per table —
    Domestic and, in months with foreign spend, International) and record
    the x-position of each of its columns.
    """
    buckets: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        buckets[round(w["top"])].append(w)

    headers = []
    for _, ws in sorted(buckets.items()):
        by_text = {w["text"]: w for w in ws}
        if not {"DATE", "TIME", "REWARDS", "AMOUNT", "PI"} <= by_text.keys():
            continue
        desc_word = by_text.get("TRANSACTION") or by_text.get("DESCRIPTION")
        headers.append(
            {
                "top": by_text["DATE"]["top"],
                "desc_x": desc_word["x0"],
                "rewards_x": by_text["REWARDS"]["x0"],
                "pi_x": by_text["PI"]["x0"],
            }
        )
    return headers


def _find_stop_top(words: list[dict], after_top: float) -> float:
    """Top of the first stop-phrase occurrence below `after_top`, or +inf."""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    texts = [w["text"] for w in ordered]
    tops = [w["top"] for w in ordered]
    best = float("inf")
    for phrase in STOP_PHRASES:
        n = len(phrase)
        for i in range(len(texts) - n + 1):
            if tops[i] > after_top and tuple(texts[i : i + n]) == phrase:
                best = min(best, tops[i])
                break
    return best


def _build_row(anchor: dict, band_words: list[dict], header: dict) -> dict:
    date_str = anchor["text"].rstrip("|")
    time_word = next(
        (
            w
            for w in band_words
            if TIME_RE.match(w["text"])
            and w["x0"] < header["desc_x"]
            and abs(w["top"] - anchor["top"]) < 1
        ),
        None,
    )
    desc_words = sorted(
        (
            w
            for w in band_words
            if header["desc_x"] - _EPS <= w["x0"] < header["rewards_x"] - _EPS
        ),
        key=lambda w: (w["top"], w["x0"]),
    )
    # Everything from the rewards column onward, minus the trailing "l"
    # (the Purchase Indicator bullet — a Wingdings glyph HDFC's font
    # substitution renders as a plain lowercase L).
    tail_words = sorted(
        (
            w
            for w in band_words
            if w["x0"] >= header["rewards_x"] - _EPS and w["text"] != "l"
        ),
        key=lambda w: (w["top"], w["x0"]),
    )
    description = " ".join(w["text"] for w in desc_words)
    tail = " ".join(w["text"] for w in tail_words)

    m = AMOUNT_WITH_POINTS_RE.match(tail)
    if m:
        reward_points = int(m.group(1))
        amount = _to_decimal(m.group(2))
    else:
        m = AMOUNT_PLAIN_RE.match(tail)
        if not m:
            raise ValueError(
                f"Unrecognised amount format {tail!r} on row dated {date_str}"
            )
        reward_points = 0
        amount = _to_decimal(m.group(1))

    return {
        "date": datetime.strptime(date_str, "%d/%m/%Y").date(),
        "time": time_word["text"] if time_word else None,
        "description": description,
        "reward_points": reward_points,
        "amount": amount,
    }


def _extract_rows(page) -> list[dict]:
    words = page.extract_words()
    headers = _find_headers(words)
    rows = []
    for h_idx, header in enumerate(headers):
        stop_top = _find_stop_top(words, header["top"])
        if h_idx + 1 < len(headers):
            stop_top = min(stop_top, headers[h_idx + 1]["top"])
        table_words = [w for w in words if header["top"] < w["top"] <= stop_top]

        anchors = sorted(
            (
                w
                for w in table_words
                if DATE_RE.match(w["text"]) and w["x0"] < header["desc_x"]
            ),
            key=lambda w: w["top"],
        )
        if not anchors:
            continue

        # The row(s) between the header and the first transaction (the
        # column header itself, plus a cardholder-name sub-row) shouldn't
        # be pulled into transaction 1's description band.
        tops_before_first = sorted(
            t for t in {w["top"] for w in table_words} if t < anchors[0]["top"]
        )
        first_band_top = tops_before_first[-1] if tops_before_first else header["top"]

        for i, anchor in enumerate(anchors):
            band_top = (
                first_band_top
                if i == 0
                else (anchors[i - 1]["top"] + anchor["top"]) / 2
            )
            band_bottom = (
                (anchors[i + 1]["top"] + anchor["top"]) / 2
                if i + 1 < len(anchors)
                else stop_top
            )
            band_words = [
                w for w in table_words if band_top < w["top"] <= band_bottom
            ]
            rows.append(_build_row(anchor, band_words, header))
    return rows
