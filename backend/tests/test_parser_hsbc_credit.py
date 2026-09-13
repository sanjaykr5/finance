from decimal import Decimal

import pytest

from app.parsers.hsbc_credit import _extract_summary_totals

# Shape of the text pdfplumber extracts from a real HSBC credit card
# statement's summary section (trimmed to what's relevant here).
SUMMARY_SECTION = (
    "07FEB JOINING FEE CC26038404949 12,000.00\n"
    "07FEB IGST ASSESSMENT @18.00% CC26038404949 2,160.00\n"
    "TOTAL PURCHASE OUTSTANDING 14,160.00\n"
    "TOTAL CASH OUTSTANDING 0.00\n"
    "TOTAL BALANCE TRANSFER OUTSTANDING 0.00\n"
    "TOTAL LOAN OUTSTANDING 0.00\n"
    "07FEB NET OUTSTANDING BALANCE 14,160.00\n"
    "0.00 14,160.00 0.00 14,160.00\n"
    "0.00 0.00 0.00 0.00\n"
)


def test_extract_summary_totals_with_zero_opening_balance():
    """Regression test: a statement whose opening balance is 0.00 (a card's
    first statement, or any month paid off in full) must not let that
    literal "0.00" collide with the trailing "0.00" of an unrelated, larger
    amount earlier in the text (here, "...BALANCE 14,160.00") and anchor
    the summary extraction one column early — which previously misread the
    opening-balance/debit/credit columns as debit/credit/closing.
    """
    expected_debit, expected_credit = _extract_summary_totals(SUMMARY_SECTION)
    assert expected_debit == Decimal("14160.00")
    assert expected_credit == Decimal("0.00")


def test_extract_summary_totals_missing_raises():
    with pytest.raises(ValueError):
        _extract_summary_totals("nothing relevant in here")
