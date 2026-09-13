"""Parser for HSBC Premier savings account statement PDFs ("Composite
Statement").

Unlike every other parser in this package, these statements carry no text
layer at all — they're scans (or scan-quality renders): `pdfplumber`
reports zero characters per page. So this parser OCRs each page
(`pytesseract`, at 300dpi) instead of reading embedded text, and works from
each *word's* pixel position rather than from lines of text — the same
general approach `hdfc_credit.py` uses for its own position-dependent
layout, adapted for a fully different table shape:

    Date       Transaction Details        Deposits   Withdrawals   Balance
    28Jul2026  BALANCE BROUGHT FORWARD                             987,696.06
    31Jul2026  TRANSFER FROM
               071-059208-001
               ALTIMATE AI INDIA PVT LT
               SOF NRE63002              339,210.00               1,326,906.06

Each transaction spans several description lines (payee + one or two UPI/
reference lines), but — verified against real statements — neither the
date nor the amount reliably lands on the block's first or last line; they
land wherever that particular sub-line fell in the original table row.
What's reliable: a line carrying a Deposits or Withdrawals amount closes
out the current transaction, using whichever date was most recently
printed (carried forward, even across a page break) and every description
line accumulated since the previous transaction closed.

Numeric columns (Deposits/Withdrawals/Balance) are bucketed by nearest
header x-position rather than a fixed left/right cutoff, because a
right-aligned column's shorter values can start to the left of its own
header — see `_bucket_row`. Every transaction's balance is reconciled
against a running total row by row (not just in aggregate) as
`_walk_transactions` goes, and the whole result is cross-checked again
against the statement's own closing "Transaction Turnover"/"Transaction
Count" summary — same "refuse to import a possibly-misparsed statement"
convention the other parsers use, just doubled up here given OCR is a
strictly less reliable source than embedded text.

Only one savings account table per statement is supported; a statement
covering more than one deposit account raises rather than silently picking
one (see `_find_account_number`).
"""

import io
import re
from datetime import datetime
from decimal import Decimal

import pdfplumber
import pytesseract
from pdfminer.pdfdocument import PDFPasswordIncorrect

from .base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError

# Pages are OCR'd at this resolution; the column x-positions baked into
# real-statement test fixtures assume it.
_OCR_DPI = 300

# Words from different pages are combined into one row-ordered list before
# processing (so a transaction split across a page break still carries its
# date/description forward correctly). Adding page_index * this to every
# word's `top` keeps pages strictly ordered without a tolerance-window
# collision — it's far larger than any single page's pixel height.
_PAGE_TOP_OFFSET = 1_000_000

# Two words are on the same visual row if their tops are within this many
# OCR pixels of each other (at 300dpi, same-row words in practice land on
# the exact same top, but scan noise can shift a glyph a few px).
_ROW_TOLERANCE = 8

# "133-167221-006" — HSBC's savings account number shape.
ACCOUNT_NUMBER_RE = re.compile(r"^\d{3}-\d{6}-\d{3}$")
# "28Jul2026"
DATE_RE = re.compile(r"^\d{2}[A-Za-z]{3}\d{4}$")
# "339,210.00" or a debit balance like "1,234.56DR".
AMOUNT_RE = re.compile(r"^([\d,]+\.\d{2})(DR)?$")
# A bare integer with no decimal point — never a real Deposits/Withdrawals/
# Balance value on its own (those always carry ".XX"), but can be the
# split-off prefix of one large amount OCR tokenized into two words.
BARE_INT_RE = re.compile(r"^[\d,]+$")
# The Transaction Count row's values are plain integers, not amounts.
NUMERIC_RE = re.compile(r"^[\d,]+(\.\d{2})?$")


class HSBCSavingsAccountParser:
    source = "HSBC Savings Account"

    def can_parse(self, filename: str, content: bytes) -> bool:
        # These statements are scanned/rasterized — there's no text to
        # sniff before OCR actually runs, so this only narrows down to
        # "some PDF"; parse() does the real validation.
        return filename.lower().endswith(".pdf") and content[:5] == b"%PDF-"

    def parse(
        self, content: bytes, password: str | None = None
    ) -> list[ParsedTransaction]:
        try:
            pdf = pdfplumber.open(io.BytesIO(content), password=password or "")
        except PDFPasswordIncorrect:
            raise PasswordRequiredError("PDF password required or incorrect")

        with pdf:
            words = []
            for page_index, page in enumerate(pdf.pages):
                words.extend(_ocr_page_words(page, page_index))

        if not any(w["text"] == "HSBC" for w in words) or not any(
            w["text"] == "Premier" for w in words
        ):
            raise UnsupportedFileError("Not an HSBC Premier account statement")

        rows = _words_to_rows(words)
        account_number = _find_account_number(rows)
        last4 = re.sub(r"\D", "", account_number)[-4:]
        header = _find_table_header(rows)
        txns = _parse_transactions_table(rows, header)

        return [
            ParsedTransaction(
                txn_date=_parse_txn_date(t["date"]),
                description=t["description"],
                amount=t["deposit"] if t["deposit"] is not None else -t["withdrawal"],
                txn_type="credit" if t["deposit"] is not None else "debit",
                account_last4=last4,
                instrument=account_number,
                raw={
                    "date": t["date"],
                    "description": t["description"],
                    "deposit": str(t["deposit"]) if t["deposit"] is not None else None,
                    "withdrawal": str(t["withdrawal"]) if t["withdrawal"] is not None else None,
                    "balance": str(t["balance"]),
                },
            )
            for t in txns
        ]


def _is_boilerplate_row(row: list[dict]) -> bool:
    """Whether a row is part of the fixed page boilerplate — the "HSBC /
    HSBC Premier" logo line, the "Premier Account Statement" title, a
    "Page N of 3" footer/header, the "SAVINGS ACCOUNT-RES <acct>" heading,
    the Date/.../Balance column header, or the Nominee/MICR/IFSC detail
    lines under it — rather than transaction content. Every continuation
    page repeats some or all of this above its table (observed on real
    statements, not just assumed), and `_parse_transactions_table` drops
    these rows from the body it walks so none of it gets swept into
    whatever transaction happens to be "in progress" when that page's
    table resumes."""
    texts = {w["text"] for w in row}
    return (
        ("HSBC" in texts and "Premier" in texts)
        or {"Premier", "Account", "Statement"} <= texts
        or ("Page" in texts and "of" in texts)
        or {"Date", "Deposits", "Withdrawals", "Balance"} <= texts
        or ("SAVINGS" in texts and "ACCOUNT-RES" in texts)
        or ("Nominee" in texts and "Registered:" in texts)
        or ("MICR" in texts and "IFSC" in texts)
        or "(DR=Debit)" in texts
        or texts == {"INR"}
    )


def _drop_boilerplate_rows(rows: list[list[dict]]) -> list[list[dict]]:
    return [r for r in rows if not _is_boilerplate_row(r)]


def _ocr_page_words(page, page_index: int) -> list[dict]:
    image = page.to_image(resolution=_OCR_DPI).original
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    raw_words = []
    for text, top, left in zip(data["text"], data["top"], data["left"]):
        text = text.strip()
        if text:
            raw_words.append({"text": text, "top": top, "x0": left})

    offset = page_index * _PAGE_TOP_OFFSET
    for w in raw_words:
        w["top"] += offset
    return raw_words


def _parse_txn_date(s: str):
    return datetime.strptime(s, "%d%b%Y").date()


def _words_to_rows(words: list[dict]) -> list[list[dict]]:
    """Group OCR words into visual rows (top-to-bottom), each row's words
    sorted left-to-right. `words` is a flat, unordered list of
    {"text", "top", "x0"} dicts."""
    ordered = sorted(words, key=lambda w: w["top"])
    rows: list[list[dict]] = []
    for w in ordered:
        if rows and abs(w["top"] - rows[-1][0]["top"]) <= _ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


def _find_table_header(rows: list[list[dict]]) -> dict:
    """Locate the "Date | Transaction Details | Deposits | Withdrawals |
    Balance" header row and record each numeric column's x-position, used
    to bucket that column's amounts by nearest-header-x rather than a fixed
    left/right cutoff (a right-aligned Balance value can start to the left
    of its own header's x0 for a short number, so a strict boundary
    misclassifies it — see hsbc_savings design notes)."""
    for row in rows:
        by_text = {w["text"]: w for w in row}
        if not {"Date", "Deposits", "Withdrawals", "Balance"} <= by_text.keys():
            continue
        return {
            "top": by_text["Date"]["top"],
            "date_x": by_text["Date"]["x0"],
            "deposits_x": by_text["Deposits"]["x0"],
            "withdrawals_x": by_text["Withdrawals"]["x0"],
            "balance_x": by_text["Balance"]["x0"],
        }
    raise ValueError(
        "Could not locate the Date/Transaction Details/Deposits/Withdrawals/"
        "Balance table header — not an HSBC savings account statement, or "
        "OCR failed to read this page's header row"
    )


def _find_account_number(rows: list[list[dict]]) -> str:
    """Locate the "SAVINGS ACCOUNT-RES <account number>" heading that
    precedes each account's transaction table. Only one such heading is
    supported per statement — a statement with more than one savings
    account table is ambiguous (which one is "the" account this parser is
    registered against?) and raises rather than silently picking one."""
    found = []
    for row in rows:
        texts = {w["text"] for w in row}
        if "SAVINGS" not in texts or "ACCOUNT-RES" not in texts:
            continue
        for w in row:
            if ACCOUNT_NUMBER_RE.match(w["text"]):
                found.append(w["text"])
                break

    if not found:
        raise ValueError(
            "Could not locate a 'SAVINGS ACCOUNT-RES <account number>' "
            "heading — not an HSBC savings account statement, or OCR "
            "failed to read it"
        )
    if len(set(found)) > 1:
        raise ValueError(
            f"Found multiple savings accounts in this statement ({found}) "
            "— parsing statements with more than one savings account isn't "
            "supported yet"
        )
    return found[0]


def _nearest_deposit_withdrawal(row: list[dict], header: dict) -> tuple[str, str]:
    """Of a row's numeric-shaped words, return the ones nearest the
    Deposits and Withdrawals header x-positions respectively (same
    nearest-header approach as `_bucket_row`, since these rows' values are
    numbers rather than a strict money-format, but still right-aligned per
    column)."""
    numeric = [w for w in _merge_split_amounts(row, header) if NUMERIC_RE.match(w["text"])]
    deposit = min(numeric, key=lambda w: abs(w["x0"] - header["deposits_x"]))
    withdrawal = min(numeric, key=lambda w: abs(w["x0"] - header["withdrawals_x"]))
    return deposit["text"], withdrawal["text"]


def _extract_summary(rows: list[list[dict]], header: dict) -> dict:
    """Read the statement's own closing "Transaction Turnover" (deposit/
    withdrawal totals) and "Transaction Count" rows, used to cross-check
    the parsed transactions against — a second, independent check beyond
    the per-row balance reconciliation `_walk_transactions` already does."""
    turnover = count = None
    for row in rows:
        if _row_contains(row, "Transaction", "Turnover"):
            deposit_s, withdrawal_s = _nearest_deposit_withdrawal(row, header)
            turnover = (_to_decimal(deposit_s), _to_decimal(withdrawal_s))
        elif _row_contains(row, "Transaction", "Count"):
            deposit_s, withdrawal_s = _nearest_deposit_withdrawal(row, header)
            count = (int(deposit_s), int(withdrawal_s))

    if turnover is None or count is None:
        raise ValueError(
            "Could not locate this statement's closing 'Transaction "
            "Turnover' / 'Transaction Count' summary to cross-check the "
            "parsed transactions against"
        )
    return {
        "deposit_total": turnover[0],
        "withdrawal_total": turnover[1],
        "deposit_count": count[0],
        "withdrawal_count": count[1],
    }


def _validate_summary_totals(txns: list[dict], summary: dict) -> None:
    """Cross-check parsed transactions against the statement's own closing
    Transaction Turnover/Count summary — a second, independent check on top
    of `_walk_transactions`'s per-row balance reconciliation. Raises on any
    mismatch rather than risk importing a misparsed statement."""
    deposit_total = sum((t["deposit"] for t in txns if t["deposit"] is not None), Decimal("0"))
    withdrawal_total = sum((t["withdrawal"] for t in txns if t["withdrawal"] is not None), Decimal("0"))
    deposit_count = sum(1 for t in txns if t["deposit"] is not None)
    withdrawal_count = sum(1 for t in txns if t["withdrawal"] is not None)

    if (
        deposit_total != summary["deposit_total"]
        or withdrawal_total != summary["withdrawal_total"]
        or deposit_count != summary["deposit_count"]
        or withdrawal_count != summary["withdrawal_count"]
    ):
        raise ValueError(
            "Parsed transaction totals don't match this statement's own "
            f"Transaction Turnover/Count summary (deposits: parsed "
            f"{deposit_total} x{deposit_count} vs stated "
            f"{summary['deposit_total']} x{summary['deposit_count']}; "
            f"withdrawals: parsed {withdrawal_total} x{withdrawal_count} vs "
            f"stated {summary['withdrawal_total']} x"
            f"{summary['withdrawal_count']}) — refusing to import a "
            "possibly-misparsed statement"
        )


def _row_contains(row: list[dict], *phrase: str) -> bool:
    """Whether `phrase` appears as a contiguous run of words anywhere in
    `row` — not necessarily at the start, since a leading date token (e.g.
    "1Jan2026 BALANCE BROUGHT FORWARD") can sit before it."""
    texts = [w["text"] for w in row]
    n = len(phrase)
    return any(texts[i : i + n] == list(phrase) for i in range(len(texts) - n + 1))


def _parse_transactions_table(rows: list[list[dict]], header: dict) -> list[dict]:
    """Orchestrate the full transactions table: bucket every row, run the
    reconstruction from the opening 'Balance Brought Forward' up to (not
    including) the closing 'Transaction Turnover' summary, and cross-check
    the result against that summary."""
    bbf_index = next(
        (
            i
            for i, r in enumerate(rows)
            if _row_contains(r, "BALANCE", "BROUGHT", "FORWARD")
            or _row_contains(r, "OPENING", "BALANCE")
        ),
        None,
    )
    if bbf_index is None:
        raise ValueError(
            "Could not locate the statement's opening 'BALANCE BROUGHT "
            "FORWARD' (or 'OPENING BALANCE') row"
        )
    turnover_index = next(
        (i for i, r in enumerate(rows) if _row_contains(r, "Transaction", "Turnover")),
        None,
    )
    if turnover_index is None or turnover_index <= bbf_index:
        raise ValueError(
            "Could not locate the statement's closing 'Transaction "
            "Turnover' row after its transactions"
        )

    body = _drop_boilerplate_rows(rows[bbf_index:turnover_index])
    bucketed = [_bucket_row(r, header) for r in body]
    txns = _walk_transactions(bucketed)
    summary = _extract_summary(rows, header)
    _validate_summary_totals(txns, summary)
    return txns


def _to_decimal(s: str) -> Decimal:
    """Parse an amount, stripping a trailing "DR" (debit) marker into a
    negative value — only ever expected on the Balance column, for an
    overdrawn account."""
    m = AMOUNT_RE.match(s)
    if not m:
        raise ValueError(f"{s!r} doesn't look like an amount")
    value = Decimal(m.group(1).replace(",", ""))
    return -value if m.group(2) else value


def _merge_split_dates(row: list[dict]) -> list[dict]:
    """OCR occasionally tokenizes a date like "27Jul2026" as two separate
    words ("27" / "Jul2026", or a three-way "27" / "Jul" / "2026") rather
    than one. Unmerged, none of the fragments match DATE_RE, so the date is
    silently dropped instead of raising — and the transaction gets whatever
    date was last carried forward instead, wrong with no error. `row` must
    already be sorted left-to-right (as `_words_to_rows` leaves it)."""
    merged = []
    i = 0
    while i < len(row):
        for window in (3, 2):
            if i + window <= len(row):
                candidate = "".join(w["text"] for w in row[i : i + window])
                if DATE_RE.match(candidate):
                    merged.append({"text": candidate, "top": row[i]["top"], "x0": row[i]["x0"]})
                    i += window
                    break
        else:
            merged.append(row[i])
            i += 1
    return merged


def _merge_split_amounts(row: list[dict], header: dict) -> list[dict]:
    """OCR occasionally splits one large, Indian-grouped amount (e.g.
    "5,98,082.96") into two words: a bare integer with no decimal point
    ("598"), immediately followed by the rest as its own decimal-bearing
    word ("082.96"). Unmerged, the prefix (not amount-shaped) falls into
    the description while the lone suffix is indistinguishable from a
    genuine small amount — silently corrupting the value instead of
    raising. Only merges within the numeric-columns zone (see
    `_AMOUNT_X_MARGIN`), since a bare integer followed by a decimal number
    elsewhere is just two unrelated reference numbers in the description."""
    merged = []
    i = 0
    while i < len(row):
        w = row[i]
        nxt = row[i + 1] if i + 1 < len(row) else None
        if (
            nxt
            and BARE_INT_RE.match(w["text"])
            and AMOUNT_RE.match(nxt["text"])
            and w["x0"] >= header["deposits_x"] - _AMOUNT_X_MARGIN
        ):
            merged.append({"text": w["text"] + nxt["text"], "top": w["top"], "x0": w["x0"]})
            i += 2
        else:
            merged.append(w)
            i += 1
    return merged


_COL_HEADER_KEY = {
    "deposits": "deposits_x",
    "withdrawals": "withdrawals_x",
    "balance": "balance_x",
}

# A card transaction's reference line can embed its own amount-shaped
# number with no thousands comma (e.g. "IN049500INR 2948.10", a merchant
# reference code) well to the left of the real Deposits/Withdrawals/
# Balance cell, which is comma-formatted and right-aligned starting near
# its header. Only amount-shaped words at least this close to the
# leftmost numeric header (Deposits) are treated as real amounts; this
# margin comfortably covers a real value's left overhang (observed as low
# as ~30px short of its own header) while staying well clear of
# description-column text (observed no further right than ~500px short).
_AMOUNT_X_MARGIN = 300


def _bucket_row(row: list[dict], header: dict) -> dict:
    """Assign one visual row's words to Date/Transaction Details/Deposits/
    Withdrawals/Balance. Amount-shaped words close enough to the numeric
    columns go to whichever of the three they're nearest to (not a fixed
    left/right cutoff — a right-aligned column's short values can start to
    the left of its own header) — see `_AMOUNT_X_MARGIN` for why "close
    enough" matters. Every other word is description text, regardless of
    x-position, so a stray description word that happens to land near a
    numeric column's x never gets mistaken for an amount."""
    date = None
    description_words = []
    amounts: dict[str, list[dict]] = {"deposits": [], "withdrawals": [], "balance": []}

    for w in _merge_split_dates(_merge_split_amounts(row, header)):
        if DATE_RE.match(w["text"]):
            date = w["text"]
            continue
        if AMOUNT_RE.match(w["text"]) and w["x0"] >= header["deposits_x"] - _AMOUNT_X_MARGIN:
            nearest = min(
                ("deposits", "withdrawals", "balance"),
                key=lambda col: abs(w["x0"] - header[_COL_HEADER_KEY[col]]),
            )
            amounts[nearest].append(w)
            continue
        description_words.append(w)

    return {
        "date": date,
        "description": " ".join(w["text"] for w in description_words),
        "deposit": _to_decimal(amounts["deposits"][0]["text"]) if amounts["deposits"] else None,
        "withdrawal": _to_decimal(amounts["withdrawals"][0]["text"]) if amounts["withdrawals"] else None,
        "balance": _to_decimal(amounts["balance"][0]["text"]) if amounts["balance"] else None,
    }


# Running-balance bookkeeping rows, not transactions — matched on their
# fixed wording. Each still carries a stated balance used to validate
# continuity (opening balance of a page against the running total so far,
# or the true closing balance at the end of the statement).
_BALANCE_BROUGHT_FORWARD = "BALANCE BROUGHT FORWARD"
_BALANCE_CARRIED_FORWARD = "Balance Carried Forward"
_CLOSING_BALANCE = "CLOSING BALANCE"
# An account's very first-ever statement has no prior period to carry a
# balance forward from, so it opens with this instead.
_OPENING_BALANCE = "OPENING BALANCE"


def _walk_transactions(rows: list[dict]) -> list[dict]:
    """Reconstruct transactions from bucketed rows (see `_bucket_row`),
    starting at the statement's own opening "Balance Brought Forward" and
    reconciling every transaction's balance against a running total as it
    goes — refusing (raising ValueError) at the first mismatch rather than
    risk importing a misparsed row. A transaction's date is whichever date
    was most recently printed by the time its amount line is reached, and
    its description is every detail line since the previous transaction
    closed — see the module-level design notes for why neither is reliably
    on the transaction's first or last line."""
    if not rows or rows[0]["description"] not in (_BALANCE_BROUGHT_FORWARD, _OPENING_BALANCE):
        raise ValueError(
            "Expected the transactions table to open with a 'BALANCE "
            "BROUGHT FORWARD' (or, for an account's first-ever statement, "
            "'OPENING BALANCE') row — statement layout not recognised"
        )
    if rows[0]["balance"] is None:
        raise ValueError(f"{rows[0]['description']!r} row has no balance")

    running_balance = rows[0]["balance"]
    last_date = rows[0]["date"]
    pending_description: list[str] = []
    txns: list[dict] = []

    for row in rows[1:]:
        if row["date"]:
            last_date = row["date"]

        if row["description"] in (
            _BALANCE_BROUGHT_FORWARD,
            _BALANCE_CARRIED_FORWARD,
            _CLOSING_BALANCE,
            _OPENING_BALANCE,
        ):
            if row["balance"] != running_balance:
                raise ValueError(
                    f"{row['description']!r} states balance {row['balance']}, "
                    f"but the running balance from parsed transactions is "
                    f"{running_balance} — refusing to import a possibly "
                    "misparsed statement"
                )
            continue

        if row["description"]:
            pending_description.append(row["description"])

        if row["deposit"] is None and row["withdrawal"] is None:
            continue

        if row["deposit"] is not None and row["withdrawal"] is not None:
            raise ValueError(
                f"Row {row['description']!r} has both a deposit and a "
                "withdrawal amount — ambiguous, refusing to guess which"
            )
        if row["balance"] is None:
            raise ValueError(
                f"Transaction {' '.join(pending_description)!r} has an "
                "amount but no balance to validate it against"
            )
        if last_date is None:
            raise ValueError(
                f"Transaction {' '.join(pending_description)!r} closed "
                "before any date was seen"
            )

        expected_balance = (
            running_balance + (row["deposit"] or Decimal("0")) - (row["withdrawal"] or Decimal("0"))
        )
        if expected_balance != row["balance"]:
            raise ValueError(
                f"Transaction {' '.join(pending_description)!r} dated "
                f"{last_date} should leave a balance of {expected_balance} "
                f"({running_balance} plus/minus this row's amount) but the "
                f"statement states {row['balance']} — refusing to import a "
                "possibly misparsed statement"
            )

        txns.append(
            {
                "date": last_date,
                "description": " ".join(pending_description),
                "deposit": row["deposit"],
                "withdrawal": row["withdrawal"],
                "balance": row["balance"],
            }
        )
        running_balance = row["balance"]
        pending_description = []

    return txns
