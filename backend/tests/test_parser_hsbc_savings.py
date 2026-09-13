from decimal import Decimal

import pytest

from app.parsers.hsbc_savings import (
    _bucket_row,
    _drop_boilerplate_rows,
    _extract_summary,
    _find_account_number,
    _find_table_header,
    _parse_transactions_table,
    _to_decimal,
    _validate_summary_totals,
    _walk_transactions,
    _words_to_rows,
)

# Column x-positions taken from a real statement's OCR'd header row.
HEADER = {"top": 2271, "date_x": 155, "deposits_x": 1400, "withdrawals_x": 1703, "balance_x": 2182}


def _row(date=None, description="", deposit=None, withdrawal=None, balance=None):
    return {
        "date": date,
        "description": description,
        "deposit": Decimal(deposit) if deposit is not None else None,
        "withdrawal": Decimal(withdrawal) if withdrawal is not None else None,
        "balance": Decimal(balance) if balance is not None else None,
    }


def _w(text, top, x0):
    return {"text": text, "top": top, "x0": x0}


def test_find_table_header_locates_column_x_positions():
    rows = _words_to_rows(
        [
            _w("SAVINGS", 2083, 157),
            _w("ACCOUNT-RES", 2083, 343),
            _w("Date", 2271, 155),
            _w("Transaction", 2271, 428),
            _w("Details", 2271, 624),
            _w("Deposits", 2271, 1400),
            _w("Withdrawals", 2271, 1703),
            _w("Balance", 2271, 2182),
        ]
    )
    header = _find_table_header(rows)
    assert header["top"] == 2271
    assert header["date_x"] == 155
    assert header["deposits_x"] == 1400
    assert header["withdrawals_x"] == 1703
    assert header["balance_x"] == 2182


def test_find_table_header_raises_when_absent():
    rows = _words_to_rows([_w("SAVINGS", 2083, 157), _w("ACCOUNT-RES", 2083, 343)])
    with pytest.raises(ValueError):
        _find_table_header(rows)


def test_find_account_number_locates_the_savings_account_heading():
    rows = _words_to_rows(
        [
            _w("SAVINGS", 2083, 157),
            _w("ACCOUNT-RES", 2083, 343),
            _w("133-167221-006", 2076, 946),
            _w("Nominee", 2149, 159),
        ]
    )
    assert _find_account_number(rows) == "133-167221-006"


def test_find_account_number_raises_when_absent():
    rows = _words_to_rows([_w("Nominee", 2149, 159), _w("Registered:", 2149, 322)])
    with pytest.raises(ValueError):
        _find_account_number(rows)


def test_find_account_number_raises_when_multiple_accounts_present():
    """More than one savings account table in one statement isn't
    supported yet — refuse rather than silently picking one."""
    rows = _words_to_rows(
        [
            _w("SAVINGS", 100, 157),
            _w("ACCOUNT-RES", 100, 343),
            _w("133-167221-006", 100, 946),
            _w("SAVINGS", 900, 157),
            _w("ACCOUNT-RES", 900, 343),
            _w("133-167221-007", 900, 946),
        ]
    )
    with pytest.raises(ValueError):
        _find_account_number(rows)


def test_to_decimal_raises_a_clear_error_on_unparseable_input():
    """Regression: a malformed amount (e.g. OCR reading a turnover total
    without its decimal point) must raise a clear ValueError, not crash
    with an opaque AttributeError from calling .group() on a failed match."""
    with pytest.raises(ValueError):
        _to_decimal("50000")


def test_bucket_row_does_not_treat_an_embedded_reference_number_as_an_amount():
    """Regression: a card transaction's reference line can embed its own
    amount-shaped number with no thousands comma (e.g. "IN049500INR
    2948.10", a merchant reference code), positioned well inside the
    description column — not the real, comma-formatted, right-aligned
    Withdrawals/Balance cell further right on the *next* line. Matched on
    shape alone, that embedded number would be wrongly bucketed as this
    row's amount and falsely close out the transaction early."""
    row = [_w("IN049500INR", 2679, 431), _w("2948.10", 2679, 636)]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["deposit"] is None
    assert bucketed["withdrawal"] is None
    assert bucketed["balance"] is None
    assert bucketed["description"] == "IN049500INR 2948.10"


def test_bucket_row_reassembles_a_large_amount_ocr_split_across_two_words():
    """Regression: OCR occasionally splits a large Indian-grouped amount
    like "5,98,082.96" into two separate words — a bare integer prefix
    ("598") with no decimal point, immediately followed by the rest as its
    own decimal-bearing word ("082.96"). Unmerged, the bare prefix (not
    amount-shaped) falls into the description while the *suffix alone* gets
    treated as the real amount — silently corrupting it (598082.96 becomes
    82.96) rather than raising, since a lone decimal-bearing word is
    indistinguishable from a genuine small amount."""
    row = [_w("SOF", 2787, 430), _w("NRE12604", 2787, 508), _w("598", 2787, 2094), _w("082.96", 2787, 2156)]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["balance"] == Decimal("598082.96")
    assert bucketed["description"] == "SOF NRE12604"


def test_bucket_row_reassembles_a_date_ocr_split_across_two_words():
    """Regression: OCR occasionally tokenizes "27Jul2026" as "27" and
    "Jul2026" (two separate words with a gap between them). Left unmerged,
    neither fragment matches the date shape, so the row's date is silently
    dropped instead of raising — and `_walk_transactions` then carries
    forward the *previous* transaction's date instead, corrupting it
    without any error. The fragments must be reassembled before matching."""
    row = [_w("27", 2841, 155), _w("Jul2026", 2841, 200), _w("CREDIT", 2841, 428), _w("CARD", 2841, 500)]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["date"] == "27Jul2026"
    assert bucketed["description"] == "CREDIT CARD"


def test_bucket_row_reads_date_and_description_only_row():
    row = [_w("31Jul2026", 2463, 155), _w("TRANSFER", 2463, 428), _w("FROM", 2463, 619)]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed == {
        "date": "31Jul2026",
        "description": "TRANSFER FROM",
        "deposit": None,
        "withdrawal": None,
        "balance": None,
    }


def test_bucket_row_assigns_deposit_and_balance_by_nearest_header_not_boundary():
    # Balance value's left edge (2094) sits left of the Balance header's own
    # x0 (2182), because right-aligned numbers vary in left edge by digit
    # count. Nearest-header assignment must still land it in Balance, not
    # Withdrawals.
    row = [
        _w("SOF", 2625, 430),
        _w("NRE63002", 2625, 508),
        _w("339,210.00", 2625, 1369),
        _w("1,326,906.06", 2625, 2094),
    ]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["description"] == "SOF NRE63002"
    assert bucketed["deposit"] == Decimal("339210.00")
    assert bucketed["withdrawal"] is None
    assert bucketed["balance"] == Decimal("1326906.06")


def test_bucket_row_assigns_withdrawal():
    row = [
        _w("Bharat", 2787, 431),
        _w("100,000.00", 2787, 1723),
        _w("1,226,906.06", 2787, 2069),
    ]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["withdrawal"] == Decimal("100000.00")
    assert bucketed["deposit"] is None
    assert bucketed["balance"] == Decimal("1226906.06")


def test_bucket_row_does_not_misclassify_a_short_description_word_as_deposit():
    # Regression: a short trailing description word ("L", from a payee name
    # OCR truncated to "Pvt L") can land at an x0 past the Deposits/
    # Description midpoint on a row with no actual deposit. Because
    # bucketing is by amount *shape* first, this must stay in the
    # description, not get treated as a (unparseable) deposit amount.
    row = [
        _w("Google", 2949, 430),
        _w("Services", 2949, 723),
        _w("Pvt", 2949, 860),
        _w("L", 2949, 917),
        _w("22,622.00", 2949, 1738),
        _w("1,204,284.06", 2949, 2069),
    ]
    bucketed = _bucket_row(row, HEADER)
    assert bucketed["description"] == "Google Services Pvt L"
    assert bucketed["deposit"] is None
    assert bucketed["withdrawal"] == Decimal("22622.00")


def test_walk_transactions_reconstructs_multiline_blocks_with_carried_over_dates():
    """Real HSBC layout: the date and the amount+balance frequently land on
    *different* lines within one transaction's multi-line detail block —
    never reliably the first or the last line. A transaction's date is
    whatever date was most recently printed by the time its amount line is
    reached; its description is every detail line since the previous
    transaction closed."""
    rows = [
        _row("28Jul2026", "BALANCE BROUGHT FORWARD", balance="987696.06"),
        _row("31Jul2026", "TRANSFER FROM"),
        _row(None, "071-059208-001"),
        _row(None, "ALTIMATE AI INDIA PVT LT"),
        _row(None, "SOF NRE63002", deposit="339210.00", balance="1326906.06"),
        _row("10Aug2026", "UPI20260810000741335"),
        _row(None, "864896552119"),
        _row(None, "Bharat Connect Credit Card Bill Pa", withdrawal="100000.00", balance="1226906.06"),
        _row("12Aug2026", "UPI20260812000341909"),
        _row(None, "622461644185"),
        _row(None, "Google India Digital Services Pvt L", withdrawal="22622.00", balance="1204284.06"),
        _row(None, "Balance Carried Forward", balance="1204284.06"),
    ]

    txns = _walk_transactions(rows)

    assert [t["date"] for t in txns] == ["31Jul2026", "10Aug2026", "12Aug2026"]
    assert txns[0]["description"] == "TRANSFER FROM 071-059208-001 ALTIMATE AI INDIA PVT LT SOF NRE63002"
    assert txns[0]["deposit"] == Decimal("339210.00")
    assert txns[0]["balance"] == Decimal("1326906.06")
    assert txns[1]["description"] == "UPI20260810000741335 864896552119 Bharat Connect Credit Card Bill Pa"
    assert txns[1]["withdrawal"] == Decimal("100000.00")
    assert txns[2]["withdrawal"] == Decimal("22622.00")


def test_walk_transactions_carries_date_across_a_page_break():
    rows = [
        _row("12Aug2026", "BALANCE BROUGHT FORWARD", balance="1204284.06"),
        _row(None, "SHIVA GUPTA", deposit="26250.00", balance="1230534.06"),
        _row(None, "Balance Carried Forward", balance="1230534.06"),
        # New page: no date repeated until it changes.
        _row(None, "BALANCE BROUGHT FORWARD", balance="1230534.06"),
        _row(None, "Bank Account XXXXXX3811", withdrawal="95000.00", balance="1135534.06"),
        _row("25Aug2026", "UPI20260825000529296"),
        _row(None, "Shruti .", withdrawal="54000.00", balance="1081534.06"),
        _row(None, "CLOSING BALANCE", balance="1081534.06"),
    ]

    txns = _walk_transactions(rows)

    assert [t["date"] for t in txns] == ["12Aug2026", "12Aug2026", "25Aug2026"]
    assert txns[1]["withdrawal"] == Decimal("95000.00")
    assert txns[2]["withdrawal"] == Decimal("54000.00")


def test_walk_transactions_raises_on_balance_mismatch():
    rows = [
        _row("1Jan2026", "BALANCE BROUGHT FORWARD", balance="1000.00"),
        _row(None, "SOME PAYEE", deposit="100.00", balance="1200.00"),  # should be 1100.00
    ]
    with pytest.raises(ValueError):
        _walk_transactions(rows)


def test_walk_transactions_accepts_opening_balance_as_the_leading_row():
    """An account's very first-ever statement has no prior period to carry
    a balance forward from, so it opens with "OPENING BALANCE" instead of
    "BALANCE BROUGHT FORWARD"."""
    rows = [
        _row("01Jan2026", "OPENING BALANCE", balance="0.00"),
        _row(None, "SALARY CREDIT", deposit="500.00", balance="500.00"),
    ]
    txns = _walk_transactions(rows)
    assert len(txns) == 1
    assert txns[0]["deposit"] == Decimal("500.00")


def test_walk_transactions_requires_a_leading_balance_brought_forward_row():
    with pytest.raises(ValueError):
        _walk_transactions([_row("1Jan2026", "SOME PAYEE", deposit="100.00", balance="1100.00")])


def test_extract_summary_reads_turnover_and_count():
    rows = _words_to_rows(
        [
            _w("Transaction", 3717, 431),
            _w("Turnover", 3717, 610),
            _w("365,460.00", 3717, 1338),
            _w("271,622.00", 3717, 1682),
            _w("Transaction", 3762, 431),
            _w("Count", 3762, 610),
            _w("2", 3762, 1467),
            _w("4", 3762, 1745),
        ]
    )
    summary = _extract_summary(rows, HEADER)
    assert summary == {
        "deposit_total": Decimal("365460.00"),
        "withdrawal_total": Decimal("271622.00"),
        "deposit_count": 2,
        "withdrawal_count": 4,
    }


def test_extract_summary_reassembles_a_large_amount_split_across_two_words():
    """Same OCR split-amount artifact as `_bucket_row`'s regression test,
    but on the Transaction Turnover row itself: "259,887.92" split into a
    bare-integer "259" and a decimal-bearing "887.92"."""
    rows = _words_to_rows(
        [
            _w("Transaction", 3717, 431),
            _w("Turnover", 3717, 610),
            _w("356,455.00", 3717, 1369),
            _w("259", 3717, 1720),
            _w("887.92", 3717, 1782),
            _w("Transaction", 3762, 431),
            _w("Count", 3762, 610),
            _w("2", 3762, 1467),
            _w("4", 3762, 1745),
        ]
    )
    summary = _extract_summary(rows, HEADER)
    assert summary["withdrawal_total"] == Decimal("259887.92")


def test_extract_summary_raises_when_absent():
    with pytest.raises(ValueError):
        _extract_summary(_words_to_rows([_w("nothing", 100, 100)]), HEADER)


def test_validate_summary_totals_accepts_a_matching_summary():
    txns = [
        {"deposit": Decimal("100.00"), "withdrawal": None},
        {"deposit": None, "withdrawal": Decimal("40.00")},
    ]
    summary = {
        "deposit_total": Decimal("100.00"),
        "withdrawal_total": Decimal("40.00"),
        "deposit_count": 1,
        "withdrawal_count": 1,
    }
    _validate_summary_totals(txns, summary)  # must not raise


def test_validate_summary_totals_rejects_a_mismatched_total():
    txns = [{"deposit": Decimal("100.00"), "withdrawal": None}]
    summary = {
        "deposit_total": Decimal("999.00"),
        "withdrawal_total": Decimal("0"),
        "deposit_count": 1,
        "withdrawal_count": 0,
    }
    with pytest.raises(ValueError):
        _validate_summary_totals(txns, summary)


def test_parse_transactions_table_integration():
    """A minimal but complete single-page table: header, opening-balance
    noise rows (the two-line Balance header + its own "INR" currency
    label), one deposit transaction spanning two description lines, and
    the closing balance + turnover/count summary — proving the whole
    pipeline (skip noise, find the account, reconstruct the transaction,
    cross-check the summary) wires together correctly."""
    words = [
        # Table header (two-line Balance header + currency sub-label).
        _w("Date", 100, 155),
        _w("Transaction", 100, 428),
        _w("Details", 100, 624),
        _w("Deposits", 100, 1400),
        _w("Withdrawals", 100, 1703),
        _w("Balance", 100, 2182),
        _w("(DR=Debit)", 144, 2131),
        _w("INR", 184, 2156),
        # Opening balance.
        _w("01Jan2026", 238, 152),
        _w("BALANCE", 238, 431),
        _w("BROUGHT", 238, 594),
        _w("FORWARD", 238, 770),
        _w("1,000.00", 238, 2094),
        # One deposit transaction, description wrapping onto a second line.
        _w("01Jan2026", 292, 153),
        _w("SALARY", 292, 428),
        _w("CREDIT", 346, 430),
        _w("500.00", 346, 1369),
        _w("1,500.00", 346, 2069),
        # Closing balance.
        _w("CLOSING", 400, 431),
        _w("BALANCE", 400, 557),
        _w("1,500.00", 400, 2069),
        # Summary.
        _w("Transaction", 454, 431),
        _w("Turnover", 454, 610),
        _w("500.00", 454, 1338),
        _w("0.00", 454, 1682),
        _w("Transaction", 508, 431),
        _w("Count", 508, 610),
        _w("1", 508, 1467),
        _w("0", 508, 1745),
    ]
    rows = _words_to_rows(words)
    header = _find_table_header(rows)

    txns = _parse_transactions_table(rows, header)

    assert len(txns) == 1
    assert txns[0]["date"] == "01Jan2026"
    assert txns[0]["description"] == "SALARY CREDIT"
    assert txns[0]["deposit"] == Decimal("500.00")
    assert txns[0]["balance"] == Decimal("1500.00")


def test_drop_boilerplate_rows_removes_a_repeated_page_masthead_and_header():
    """A continuation page repeats "HSBC / HSBC Premier / Premier Account
    Statement / Page N of 3", the "SAVINGS ACCOUNT-RES <acct>" heading and
    the Date/.../Balance column header again above its table. Left in, all
    of it gets swept into the description of whatever transaction is "in
    progress" when that page's table resumes (a real bug this
    regression-tests) — every one of these marker rows must be dropped,
    wherever in the body they fall, while real transaction rows around them
    survive untouched."""
    rows = _words_to_rows(
        [
            _w("SOME", 50, 431),
            _w("PAYEE", 50, 550),
            _w("HSBC", 100, 100),
            _w("Premier", 100, 300),
            _w("Premier", 200, 100),
            _w("Account", 200, 300),
            _w("Statement", 200, 500),
            _w("Page", 300, 100),
            _w("3", 300, 200),
            _w("of", 300, 250),
            _w("3", 300, 300),
            _w("SAVINGS", 400, 157),
            _w("ACCOUNT-RES", 400, 343),
            _w("133-167221-006", 400, 946),
            _w("Date", 500, 155),
            _w("Transaction", 500, 428),
            _w("Details", 500, 624),
            _w("Deposits", 500, 1400),
            _w("Withdrawals", 500, 1703),
            _w("Balance", 500, 2182),
            _w("Balance", 600, 431),
            _w("Brought", 600, 557),
            _w("Forward", 600, 675),
        ]
    )
    kept = [[w["text"] for w in row] for row in _drop_boilerplate_rows(rows)]
    assert kept == [["SOME", "PAYEE"], ["Balance", "Brought", "Forward"]]


def test_drop_boilerplate_rows_removes_the_balance_column_sub_header():
    """The Balance column's header wraps onto two extra lines below
    "Balance" itself: "(DR=Debit)" and, on the very first table, a
    standalone "INR" currency label. Real statements have shown these sit
    close enough to a continuation page's "Balance Brought Forward" row to
    cluster with (or immediately precede) it without matching the main
    Date/Deposits/Withdrawals/Balance header signature, so they need their
    own check or they leak into a transaction's description."""
    rows = _words_to_rows(
        [
            _w("(DR=Debit)", 100, 2131),
            _w("INR", 150, 2156),
            _w("SOME", 200, 431),
            _w("PAYEE", 200, 550),
        ]
    )
    kept = [[w["text"] for w in row] for row in _drop_boilerplate_rows(rows)]
    assert kept == [["SOME", "PAYEE"]]


def test_drop_boilerplate_rows_leaves_rows_unchanged_when_none_found():
    rows = _words_to_rows([_w("SOME", 400, 431), _w("PAYEE", 400, 550)])
    assert _drop_boilerplate_rows(rows) == rows


def test_words_to_rows_groups_by_vertical_proximity_and_sorts_left_to_right():
    # Two visual rows, words given out of order and interleaved to prove
    # both the row-grouping and the within-row left-to-right sort happen.
    words = [
        _w("Bank", 100, 428),
        _w("28Jul2026", 100, 155),
        _w("987,696.06", 103, 2094),
        _w("TRANSFER", 200, 428),
        _w("31Jul2026", 202, 155),
    ]
    rows = _words_to_rows(words)
    assert [w["text"] for w in rows[0]] == ["28Jul2026", "Bank", "987,696.06"]
    assert [w["text"] for w in rows[1]] == ["31Jul2026", "TRANSFER"]
