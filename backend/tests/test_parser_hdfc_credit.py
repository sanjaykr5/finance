from decimal import Decimal

import pytest

from app.parsers.hdfc_credit import _build_row, _extract_rows, _find_headers

# Column x-positions taken from a real statement's header row.
HEADER = {"top": 74.5, "desc_x": 136.3, "rewards_x": 428.5, "pi_x": 565.2}


def _w(text, top, x0):
    return {"text": text, "top": top, "x0": x0}


def _anchor(date="24/03/2026|", top=100.0):
    return _w(date, top, 25.5)


def test_build_row_reads_a_plain_debit_with_no_reward_points():
    band = [_w("Adobe", 100, 136.3), _w("Systems", 100, 200), _w("C", 100, 518.9), _w("68.77", 100, 523.9)]
    row = _build_row(_anchor(top=100), band, HEADER)
    assert row["reward_points"] == 0
    assert row["is_credit"] is False
    assert row["amount"] == Decimal("68.77")


def test_build_row_reads_a_debit_with_reward_points_earned():
    """"+ 8 C 382.32": the "+" belongs to the points count, not a credit
    marker — this must still classify as a debit."""
    band = [
        _w("Adobe", 100, 136.3),
        _w("+", 100, 430),
        _w("8", 100, 434),
        _w("C", 100, 518.9),
        _w("382.32", 100, 523.9),
    ]
    row = _build_row(_anchor(top=100), band, HEADER)
    assert row["reward_points"] == 8
    assert row["is_credit"] is False
    assert row["amount"] == Decimal("382.32")


def test_build_row_reads_a_plain_credit_payment():
    """"+ C 29,662.00": no points shown, so the leading "+" is the credit
    marker itself (e.g. an AUTOPAY payment)."""
    band = [
        _w("AUTOPAY", 100, 136.3),
        _w("THANK", 100, 200),
        _w("+", 100, 512.2),
        _w("C", 100, 518.9),
        _w("29,662.00", 100, 523.9),
    ]
    row = _build_row(_anchor(top=100), band, HEADER)
    assert row["reward_points"] == 0
    assert row["is_credit"] is True
    assert row["amount"] == Decimal("29662.00")


def test_build_row_reads_a_return_with_negative_points_and_a_credit_marker():
    """Regression: "- 332 + C 12,506.00" — a returned purchase's earlier
    reward points get clawed back (negative points) and, distinct from the
    points' own sign, a separate "+" appears right before "C" marking this
    row as a credit. Previously this raised "Unrecognised amount format"
    because the sign regex only accepted a leading "+"."""
    band = [
        _w("FLIPKART", 100, 136.3),
        _w("INTERNET", 100, 200),
        _w("-", 100, 436.1),
        _w("332", 100, 439.8),
        _w("+", 100, 512.2),
        _w("C", 100, 518.9),
        _w("12,506.00", 100, 523.9),
    ]
    row = _build_row(_anchor(date="31/12/2025|", top=100), band, HEADER)
    assert row["reward_points"] == -332
    assert row["is_credit"] is True
    assert row["amount"] == Decimal("12506.00")


def test_build_row_raises_on_unrecognised_amount_format():
    band = [_w("Mystery", 100, 136.3), _w("Row", 100, 200)]
    with pytest.raises(ValueError):
        _build_row(_anchor(top=100), band, HEADER)


def test_extract_rows_does_not_swallow_a_second_table_header_on_the_same_page():
    """Regression: when a page holds both the Domestic and International
    tables back-to-back with no vertical gap, the last Domestic row's band
    used to extend up to (and including) the International header's own
    row, since the boundary check used `<=` against that header's `top`.
    That pulled "REWARDS AMOUNT PI" straight into the last row's amount
    text and broke parsing. The last Domestic row must stop strictly before
    the next header."""
    words = [
        # Domestic header.
        _w("DATE", 74.5, 25.5),
        _w("TIME", 74.5, 90),
        _w("TRANSACTION", 74.5, 136.3),
        _w("DESCRIPTION", 74.5, 250),
        _w("REWARDS", 74.5, 428.5),
        _w("AMOUNT", 74.5, 480),
        _w("PI", 74.5, 565.2),
        # Last Domestic row, sitting some way above the next header.
        _w("19/02/2026|", 363.7, 25.5),
        _w("00:00", 363.7, 64.1),
        _w("IGST-VPS", 363.7, 136.3),
        _w("C", 363.7, 518.9),
        _w("67.51", 363.7, 523.9),
        _w("l", 363.7, 565.2),
        # International header, with no vertical gap before it — the last
        # Domestic row's band extends all the way down to this header's own
        # `top` (the real-world failure condition).
        _w("DATE", 391.1, 25.5),
        _w("TIME", 391.1, 90),
        _w("TRANSACTION", 391.1, 136.3),
        _w("DESCRIPTION", 391.1, 250),
        _w("REWARDS", 391.1, 428.7),
        _w("AMOUNT", 391.1, 480),
        _w("PI", 391.1, 566.7),
    ]

    headers = _find_headers(words)
    assert len(headers) == 2

    class _Page:
        def extract_words(self):
            return words

    rows = _extract_rows(_Page())
    assert len(rows) == 1
    assert rows[0]["amount"] == Decimal("67.51")
    assert rows[0]["description"] == "IGST-VPS"
