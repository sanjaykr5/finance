from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass
class ParsedTransaction:
    txn_date: date
    description: str
    amount: Decimal  # negative = spend, positive = credit/refund
    # Explicit 'debit' or 'credit' as stated by the statement itself — kept
    # independent of amount's sign so the two can be cross-checked.
    txn_type: str
    account_last4: str | None = None
    # Raw instrument the statement attributes the transaction to (a masked
    # card/account number, or a label like "Account"), verbatim. Distinct
    # from account_last4, which is just the trailing digits extracted from it.
    instrument: str | None = None
    # Bank/UPI-provided reference such as a UTR or cheque no. Only set when
    # the statement actually carries one — None otherwise.
    txn_ref: str | None = None
    # Time of day the statement reports for this transaction, "HH:MM", when
    # the statement carries one (txn_date only has day-level precision).
    txn_time: str | None = None
    # The payment app/processor's own transaction id (distinct from txn_ref,
    # which is the bank-side UTR/reference).
    transaction_id: str | None = None
    raw: dict = field(default_factory=dict)


class Parser(Protocol):
    source: str

    def can_parse(self, filename: str, content: bytes) -> bool: ...

    def parse(
        self, content: bytes, password: str | None = None
    ) -> list[ParsedTransaction]: ...


class UnsupportedFileError(Exception):
    pass


class PasswordRequiredError(Exception):
    pass
