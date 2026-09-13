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
    monkeypatch.setitem(PARSERS_BY_KEY, parser_key, _FakeParser(provider, description))
    account = client.post("/api/accounts", json={"kind": kind, "provider": provider}).json()
    assert account["parser"] == parser_key
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


def test_rules_apply_can_be_scoped_to_a_single_tag(client, monkeypatch):
    """The rules popup's "Apply to existing" button applies only the rules
    for the tag it's open on, not every tag's rules at once.
    """
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()
    food_tag = client.post("/api/tags", json={"name": "food"}).json()

    # Transactions land untagged, since no rules exist yet at insert time —
    # rules are added afterwards, like backfilling a newly created tag.
    _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )
    _register_and_insert_one(client, monkeypatch, "bank", "HSBC", "hsbc_savings", "SWIGGY order 123")

    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
        },
    )
    client.post(
        "/api/rules",
        json={
            "tag_id": food_tag["id"],
            "match_type": "contains",
            "pattern": "SWIGGY",
            "priority": 0,
        },
    )

    r = client.post("/api/rules/apply", json={"tag_id": rent_tag["id"]})
    assert r.status_code == 200
    assert r.json()["updated"] == 1

    tags_by_name = {t["name"]: t for t in client.get("/api/tags").json()}
    assert tags_by_name["rent"]["txn_count"] == 1
    assert tags_by_name["food"]["txn_count"] == 0


def test_rules_apply_without_tag_id_still_applies_every_tag(client, monkeypatch):
    """Existing global-apply behavior is preserved when no tag_id is given."""
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()
    food_tag = client.post("/api/tags", json={"name": "food"}).json()

    _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )
    _register_and_insert_one(client, monkeypatch, "bank", "HSBC", "hsbc_savings", "SWIGGY order 123")

    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
        },
    )
    client.post(
        "/api/rules",
        json={
            "tag_id": food_tag["id"],
            "match_type": "contains",
            "pattern": "SWIGGY",
            "priority": 0,
        },
    )

    r = client.post("/api/rules/apply", json={})
    assert r.status_code == 200
    assert r.json()["updated"] == 2


def test_a_tag_can_have_more_than_one_regex_rule(client):
    """A tag isn't limited to a single auto-tag pattern."""
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()

    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
        },
    )
    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX9042",
            "priority": 0,
        },
    )

    rules = client.get("/api/rules").json()
    assert len([r for r in rules if r["tag_id"] == rent_tag["id"]]) == 2


def test_rule_scoped_to_an_account_only_matches_that_account(client, monkeypatch):
    """A rule with an account filter shouldn't tag a same-pattern
    transaction that came in on a different account.
    """
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()

    hdfc = _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )
    _register_and_insert_one(
        client, monkeypatch, "bank", "HSBC", "hsbc_savings", "Paid to Bank Account XXXXXX3811"
    )

    r = client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
            "account_id": hdfc["id"],
        },
    )
    assert r.status_code == 200

    applied = client.post("/api/rules/apply", json={"tag_id": rent_tag["id"]})
    assert applied.json()["updated"] == 1

    tags_by_name = {t["name"]: t for t in client.get("/api/tags").json()}
    assert tags_by_name["rent"]["txn_count"] == 1


def test_rule_list_reports_the_scoped_account(client, monkeypatch):
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()
    hdfc = _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )

    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
            "account_id": hdfc["id"],
        },
    )
    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "contains",
            "pattern": "unscoped",
            "priority": 0,
        },
    )

    rules = client.get("/api/rules").json()
    scoped = next(r for r in rules if r["pattern"] == r"Bank Account XXXXXX3811")
    unscoped = next(r for r in rules if r["pattern"] == "unscoped")
    assert scoped["account_id"] == hdfc["id"]
    assert scoped["account_label"] == hdfc["label"]
    assert unscoped["account_id"] is None
    assert unscoped["account_label"] is None


def test_deleting_an_account_removes_rules_scoped_to_it(client, monkeypatch):
    rent_tag = client.post("/api/tags", json={"name": "rent"}).json()
    hdfc = _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )
    client.post(
        "/api/rules",
        json={
            "tag_id": rent_tag["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
            "account_id": hdfc["id"],
        },
    )

    r = client.delete(f"/api/accounts/{hdfc['id']}")
    assert r.status_code == 200

    rules = client.get("/api/rules").json()
    assert rules == []
