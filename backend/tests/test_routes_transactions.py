from datetime import date
from decimal import Decimal

from app.parsers.base import ParsedTransaction
from app.parsers.detect import PARSERS_BY_KEY


class _FakeParser:
    """Stands in for a real parser so a test can insert one transaction
    without needing a real statement file for the format.
    """

    def __init__(self, source, description):
        self.source = source
        self._description = description

    def can_parse(self, filename, content):
        return True

    def parse(self, content, password=None):
        return [
            ParsedTransaction(
                txn_date=date(2025, 1, 1),
                description=self._description,
                amount=Decimal("-1.00"),
                txn_type="debit",
            )
        ]


def _register_and_insert_one(client, monkeypatch, kind, provider, parser_key, description):
    """Register an account (using its real, auto-assigned parser key) and
    insert one transaction through the normal upload/confirm flow, with the
    parser faked out so no real statement file is needed.
    """
    monkeypatch.setitem(PARSERS_BY_KEY, parser_key, _FakeParser(provider, description))
    account = client.post("/api/accounts", json={"kind": kind, "provider": provider}).json()
    assert account["parser"] == parser_key
    # Uploaded-file dedup is keyed by content hash alone (not per-account),
    # so each account in a test needs distinct bytes to avoid a false
    # "duplicate_file" short-circuit.
    token = client.post(
        "/api/upload/parse",
        data={"account_id": str(account["id"])},
        files={"file": ("s.csv", f"irrelevant-{provider}".encode(), "text/csv")},
    ).json()["upload_token"]
    r = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0]},
    )
    assert r.status_code == 200
    return account


def test_source_filter_excludes_other_sources(client, monkeypatch):
    """The Credit Cards page locks itself to the credit-card sources via a
    `source` query param on /transactions — the server must actually filter
    on it, or every source (e.g. PhonePe) leaks into that view.
    """
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    _register_and_insert_one(client, monkeypatch, "upi", "PhonePe", "phonepe", "PhonePe txn")

    r = client.get("/api/transactions", params=[("source", "HDFC Credit Card")])
    assert r.status_code == 200
    body = r.json()
    assert [t["source"] for t in body] == ["HDFC Credit Card"]

    count = client.get(
        "/api/transactions/count", params=[("source", "HDFC Credit Card")]
    ).json()
    assert count["total"] == 1


def test_source_filter_accepts_multiple_values(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    _register_and_insert_one(client, monkeypatch, "credit_card", "HSBC", "hsbc_credit", "HSBC txn")
    _register_and_insert_one(client, monkeypatch, "upi", "PhonePe", "phonepe", "PhonePe txn")

    r = client.get(
        "/api/transactions",
        params=[("source", "HDFC Credit Card"), ("source", "HSBC Credit Card")],
    )
    sources = {t["source"] for t in r.json()}
    assert sources == {"HDFC Credit Card", "HSBC Credit Card"}


def test_no_source_filter_returns_every_source(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    _register_and_insert_one(client, monkeypatch, "upi", "PhonePe", "phonepe", "PhonePe txn")

    r = client.get("/api/transactions")
    sources = {t["source"] for t in r.json()}
    assert sources == {"HDFC Credit Card", "PhonePe"}


def test_new_transaction_defaults_to_unaudited(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")

    txn = client.get("/api/transactions").json()[0]
    assert txn["notes"] is None
    assert txn["audited"] is False


def test_patch_notes_and_audited(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]

    r = client.patch(
        f"/api/transactions/{txn_id}",
        json={"notes": "split with roommate", "audited": True},
    )
    assert r.status_code == 200

    txn = client.get("/api/transactions").json()[0]
    assert txn["notes"] == "split with roommate"
    assert txn["audited"] is True


def test_patch_one_field_leaves_others_untouched(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]

    client.patch(f"/api/transactions/{txn_id}", json={"notes": "first"})
    client.patch(f"/api/transactions/{txn_id}", json={"audited": True})

    txn = client.get("/api/transactions").json()[0]
    assert txn["notes"] == "first"
    assert txn["audited"] is True

    # A tag_id-only PATCH (the pre-existing TagPicker contract) must not
    # reset notes/audited back to their defaults.
    tag = client.post("/api/tags", json={"name": "Shared"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_id": tag["id"]})
    txn = client.get("/api/transactions").json()[0]
    assert txn["tag_id"] == tag["id"]
    assert txn["notes"] == "first"
    assert txn["audited"] is True
