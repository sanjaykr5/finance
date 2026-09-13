from .base import Parser
from .hdfc_bank import HDFCBankStatementParser
from .hdfc_credit import HDFCCreditCardParser
from .hsbc_credit import HSBCCreditCardParser
from .hsbc_savings import HSBCSavingsAccountParser
from .phonepe import PhonePeParser

# Every parser this app knows, keyed by the string an `accounts.parser` row
# points at. Which parser an upload uses is no longer auto-detected across
# all of them — the target account (chosen at upload time) says which one
# to use, since each account's statements only ever come from one place.
PARSERS_BY_KEY: dict[str, Parser] = {
    "hdfc_bank": HDFCBankStatementParser(),
    "hdfc_credit": HDFCCreditCardParser(),
    "hsbc_credit": HSBCCreditCardParser(),
    "hsbc_savings": HSBCSavingsAccountParser(),
    "phonepe": PhonePeParser(),
}

# (kind, provider-lowercased) -> parser key, used to auto-assign a parser
# when an account is registered for a provider this app already knows how
# to parse. An unrecognised provider (or a bank account from a provider
# this app has no parser for) resolves to None, and the account is
# registered without a parser until one exists — see routes/accounts.py.
_KNOWN_PARSERS = {
    ("bank", "hdfc"): "hdfc_bank",
    ("credit_card", "hdfc"): "hdfc_credit",
    ("credit_card", "hsbc"): "hsbc_credit",
    ("bank", "hsbc"): "hsbc_savings",
    ("upi", "phonepe"): "phonepe",
}


def resolve_parser_key(kind: str, provider: str) -> str | None:
    return _KNOWN_PARSERS.get((kind, provider.strip().lower()))
