from datetime import date
from decimal import Decimal

from app.parsers.base import ParsedTransaction
from app.parsers.detect import PARSERS_BY_KEY

PHONEPE_CSV = """Transaction Statement for +91XXXXXXXXXX
Duration,01 Jan 2025 - 19 Aug 2026

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
2025-01-05,08:56,Paid to Test Merchant,T2501050855583627138256,450554942766,Debit,XXXXXX7512,100.00
2025-01-06,09:00,Received from Friend,T2501060900001234567890,450554942767,Credit,Account,50.00

This is an automatically generated statement.
"""


def _register_phonepe_account(client):
    r = client.post("/api/accounts", json={"kind": "upi", "provider": "PhonePe"})
    assert r.status_code == 200
    return r.json()["id"]


def _upload_csv(client, account_id, csv_text=PHONEPE_CSV, filename="statement.csv", password=None):
    data = {"account_id": str(account_id)}
    if password:
        data["password"] = password
    return client.post(
        "/api/upload/parse",
        data=data,
        files={"file": (filename, csv_text.encode(), "text/csv")},
    )


def test_parse_returns_token_and_two_rows(client):
    account_id = _register_phonepe_account(client)
    r = _upload_csv(client, account_id)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["upload_token"]
    assert len(body["rows"]) == 2
    assert all(row["duplicate"] is False for row in body["rows"])
    assert "raw" not in body["rows"][0]


def test_parse_unknown_account_400s(client):
    r = _upload_csv(client, 999999)
    assert r.status_code == 400


def test_confirm_inserts_only_selected_rows(client):
    account_id = _register_phonepe_account(client)
    token = _upload_csv(client, account_id).json()["upload_token"]

    r = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["parsed"] == 2
    assert body["inserted"] == 1
    assert body["skipped_duplicates"] == 1

    txns = client.get("/api/transactions").json()
    assert len(txns) == 1
    assert txns[0]["description"] == "Paid to Test Merchant"


def test_confirm_is_single_use(client):
    account_id = _register_phonepe_account(client)
    token = _upload_csv(client, account_id).json()["upload_token"]
    first = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0, 1]},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0, 1]},
    )
    assert second.status_code == 404

    txns = client.get("/api/transactions").json()
    assert len(txns) == 2  # the second, failed confirm must not double-insert


def test_double_confirm_of_same_file_bytes_via_two_tokens_is_rejected(client):
    """Two separate /upload/parse calls on identical bytes (e.g. two browser
    tabs) each get their own token. Confirming both must not double-insert
    transaction rows nor 500 — the second confirm should be rejected with
    409 and the DB should reflect only the first confirm's rows.
    """
    account_id = _register_phonepe_account(client)
    first_token = _upload_csv(client, account_id).json()["upload_token"]
    second_token = _upload_csv(client, account_id).json()["upload_token"]
    assert first_token != second_token

    first = client.post(
        "/api/upload/confirm",
        json={"upload_token": first_token, "selected_indices": [0, 1]},
    )
    assert first.status_code == 200
    assert first.json()["inserted"] == 2

    second = client.post(
        "/api/upload/confirm",
        json={"upload_token": second_token, "selected_indices": [0, 1]},
    )
    assert second.status_code == 409

    txns = client.get("/api/transactions").json()
    assert len(txns) == 2  # only the first confirm's rows, not double-inserted


def test_confirm_rejects_out_of_range_index(client):
    account_id = _register_phonepe_account(client)
    token = _upload_csv(client, account_id).json()["upload_token"]
    r = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [5]},
    )
    assert r.status_code == 400


def test_cancel_discards_staged_upload(client):
    account_id = _register_phonepe_account(client)
    token = _upload_csv(client, account_id).json()["upload_token"]
    r = client.delete(f"/api/upload/parse/{token}")
    assert r.status_code == 200

    confirm = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0]},
    )
    assert confirm.status_code == 404


def test_reupload_same_file_short_circuits(client):
    account_id = _register_phonepe_account(client)
    first_token = _upload_csv(client, account_id).json()["upload_token"]
    client.post(
        "/api/upload/confirm",
        json={"upload_token": first_token, "selected_indices": [0, 1]},
    )

    second = _upload_csv(client, account_id)
    assert second.json()["status"] == "duplicate_file"


def test_second_upload_with_overlapping_rows_flags_duplicates(client):
    account_id = _register_phonepe_account(client)
    first_token = _upload_csv(client, account_id).json()["upload_token"]
    client.post(
        "/api/upload/confirm",
        json={"upload_token": first_token, "selected_indices": [0, 1]},
    )

    # Different bytes (different sha256, so it's not short-circuited by the
    # exact-file check above) but the same two transactions, to exercise
    # per-row duplicate detection distinctly from the file-level check.
    variant = PHONEPE_CSV.replace(
        "01 Jan 2025 - 19 Aug 2026", "01 Jan 2025 - 20 Aug 2026"
    )
    second = _upload_csv(client, account_id, csv_text=variant)
    body = second.json()
    assert body["status"] == "ok"
    assert all(row["duplicate"] for row in body["rows"])


class _RecordingParser:
    source = "Fake"

    def __init__(self):
        self.received_password = "not-called"

    def can_parse(self, filename, content):
        return True

    def parse(self, content, password=None):
        self.received_password = password
        return [
            ParsedTransaction(
                txn_date=date(2025, 1, 1),
                description="fake txn",
                amount=Decimal("-1.00"),
                txn_type="debit",
            )
        ]


def _register_fake_account(client, monkeypatch, provider, password=None):
    fake = _RecordingParser()
    monkeypatch.setitem(PARSERS_BY_KEY, "fake", fake)
    account = client.post(
        "/api/accounts",
        json={"kind": "bank", "provider": provider, "password": password},
    ).json()
    # Accounts only get a parser auto-assigned for providers resolve_parser_key
    # knows about — point this account at the fake parser directly.
    import app.db as db_module

    conn = db_module.get_conn()
    conn.execute("UPDATE accounts SET parser = 'fake' WHERE id = ?", [account["id"]])
    return account["id"], fake


def test_parse_uses_stored_password_when_no_override(client, monkeypatch):
    account_id, fake = _register_fake_account(client, monkeypatch, "FakeBank", password="stored-pw")
    r = client.post(
        "/api/upload/parse",
        data={"account_id": str(account_id)},
        files={"file": ("s.csv", b"irrelevant", "text/csv")},
    )
    assert r.status_code == 200
    assert fake.received_password == "stored-pw"


def test_parse_override_password_wins_over_stored(client, monkeypatch):
    account_id, fake = _register_fake_account(client, monkeypatch, "FakeBank2", password="stored-pw")
    r = client.post(
        "/api/upload/parse",
        data={"account_id": str(account_id), "password": "override-pw"},
        files={"file": ("s.csv", b"irrelevant", "text/csv")},
    )
    assert r.status_code == 200
    assert fake.received_password == "override-pw"
