# Account Admin & Staged Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Accounts admin page (add/edit/delete bank/credit-card/UPI accounts, each with an optional encrypted statement password) and replace the upload flow's immediate-insert behavior with a parse → preview → confirm flow.

**Architecture:** Backend: a new `crypto.py` (Fernet symmetric encryption, key in a local gitignored file) backs a new `accounts.encrypted_password` column; `routes/accounts.py` gains a PATCH endpoint; `routes/upload.py` is split into `POST /upload/parse` (parses into an in-memory, token-keyed staging store, no DB writes) and `POST /upload/confirm` (inserts only the rows the client selects, single-use token). Frontend: a new `pages/Accounts.tsx` CRUD page reachable from the sidebar, and `pages/Upload.tsx` becomes a three-stage (`form → previewing → done`) flow driven by those two endpoints.

**Tech Stack:** FastAPI, DuckDB, `cryptography` (Fernet), pytest + `fastapi.testclient.TestClient` (new — no test suite exists yet), React + TypeScript + Vite, Radix/shadcn-style UI primitives already in `frontend/src/components/ui`.

**Spec:** `docs/superpowers/specs/2026-09-09-account-admin-and-staged-upload-design.md`

## Global Constraints

- The real `backend/expense.duckdb` and `backend/.secret.key` must never be touched by a test run — every test runs against an isolated tmp-path DB and key file (see Task 1).
- The encrypted password value never leaves `db.py` — routes and API responses only ever see a derived `has_password: bool`.
- `kind`/`provider` are immutable after account creation (baked into the account's physical table name and parser assignment) — `PATCH /accounts/{id}` does not accept them.
- Duplicate-flagged rows in the upload preview are shown, pre-unchecked, and still user-selectable — never hidden, never forced-excluded.
- Do not modify the existing `Account` type in `frontend/src/api.ts` — it's a separate, already-in-use shape for `TransactionsView`'s (currently broken — see Task 6 note) account filter. The new `accounts`-table entity gets its own type, `RegisteredAccount`.
- `cryptography` (48.0.0) is already present in `backend/.venv` as a transitive dependency; pin it directly in `requirements.txt` since it's now a direct dependency.

---

### Task 1: Backend test infrastructure

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/pytest.ini`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_smoke.py`

**Interfaces:**
- Produces: pytest fixtures `conn` (a bootstrapped `duckdb.DuckDBPyConnection` cursor backed by a tmp-path DB) and `client` (a `fastapi.testclient.TestClient` wired to the same isolated DB), both defined in `backend/tests/conftest.py`. Every later backend task's tests use one or both of these.

- [ ] **Step 1: Write `backend/requirements-dev.txt`**

```
-r requirements.txt
pytest==8.4.2
httpx==0.28.1
```

- [ ] **Step 2: Install dev dependencies**

Run: `cd backend && source .venv/bin/activate && pip install -r requirements-dev.txt`
Expected: `pytest` and `httpx` install successfully (both currently missing from the venv).

- [ ] **Step 3: Write `backend/pytest.ini`**

```ini
[pytest]
pythonpath = .
```

This lets test files do `from app import db` when pytest is run from `backend/` (matching `run.sh`'s working directory convention).

- [ ] **Step 4: Write `backend/tests/conftest.py`**

```python
import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets a throwaway DuckDB file — never the real
    backend/expense.duckdb. Applies to every test automatically, whether or
    not it explicitly requests `conn` or `client`.
    """
    from app import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.duckdb")
    monkeypatch.setattr(db_module, "_conn", None)
    yield
    db_module._conn = None


@pytest.fixture
def conn():
    """A fresh, bootstrapped DuckDB cursor for the current test."""
    from app.db import get_conn

    return get_conn()


@pytest.fixture
def client():
    """A FastAPI TestClient wired to the same isolated DB as `conn`."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
```

- [ ] **Step 5: Write `backend/tests/test_smoke.py`**

```python
from pathlib import Path

from app import db as db_module


def test_get_conn_uses_isolated_tmp_db(tmp_path):
    db_module.get_conn()
    assert Path(db_module.DB_PATH).parent == tmp_path
    assert Path(db_module.DB_PATH).exists()

    real_db = Path(__file__).resolve().parent.parent / "expense.duckdb"
    assert db_module.DB_PATH != real_db


def test_app_boots_and_bootstraps_accounts_table(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "service": "expense-tracker"}

    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert r.json() == []
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && source .venv/bin/activate && pytest -v`
Expected: both tests PASS.

- [ ] **Step 7: Confirm the real DB/key files are untouched**

Run: `ls -la backend/expense.duckdb && ! test -f backend/.secret.key`
Expected: `expense.duckdb`'s mtime is unchanged from before Step 6 ran, and `.secret.key` does not exist.

- [ ] **Step 8: Commit**

```bash
git add backend/requirements-dev.txt backend/pytest.ini backend/tests/conftest.py backend/tests/test_smoke.py
git commit -m "test: add isolated pytest infrastructure for the backend"
```

---

### Task 2: Password encryption module

**Files:**
- Create: `backend/app/crypto.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/.gitignore`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/test_crypto.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `crypto.encrypt_password(plaintext: str) -> str`, `crypto.decrypt_password(ciphertext: str) -> str`, module attributes `crypto._KEY_PATH: Path` and `crypto._fernet: Fernet | None` (both used by the test-isolation fixture and directly by `test_crypto.py`). Task 3 (`db.py`) imports `from . import crypto` and calls these two functions.

- [ ] **Step 1: Write the failing test — `backend/tests/test_crypto.py`**

```python
from app import crypto


def test_encrypt_decrypt_round_trip():
    ciphertext = crypto.encrypt_password("hunter2")
    assert ciphertext != "hunter2"
    assert crypto.decrypt_password(ciphertext) == "hunter2"


def test_key_file_created_with_owner_only_permissions():
    crypto.encrypt_password("anything")
    assert crypto._KEY_PATH.exists()
    mode = crypto._KEY_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_key_persists_across_fresh_fernet_instances():
    ciphertext = crypto.encrypt_password("same-key-please")
    crypto._fernet = None  # force re-reading the key from disk
    assert crypto.decrypt_password(ciphertext) == "same-key-please"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crypto'`

- [ ] **Step 3: Write `backend/app/crypto.py`**

```python
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_PATH = Path(__file__).resolve().parent.parent / ".secret.key"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not _KEY_PATH.exists():
            _KEY_PATH.write_bytes(Fernet.generate_key())
            _KEY_PATH.chmod(0o600)
        _fernet = Fernet(_KEY_PATH.read_bytes())
    return _fernet


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 4: Extend the isolation fixture in `backend/tests/conftest.py`**

Modify the `_isolated_db` fixture so every test also gets its own throwaway key file — never the real `backend/.secret.key`:

```python
@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets a throwaway DuckDB file and Fernet key file — never
    the real backend/expense.duckdb or backend/.secret.key. Applies to every
    test automatically, whether or not it explicitly requests `conn` or
    `client`.
    """
    from app import crypto as crypto_module
    from app import db as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.duckdb")
    monkeypatch.setattr(db_module, "_conn", None)
    monkeypatch.setattr(crypto_module, "_KEY_PATH", tmp_path / "test.secret.key")
    monkeypatch.setattr(crypto_module, "_fernet", None)
    yield
    db_module._conn = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest -v`
Expected: all tests (smoke + crypto) PASS.

- [ ] **Step 6: Add `cryptography` to `backend/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
duckdb==1.1.3
pdfplumber==0.11.4
pandas==2.2.3
python-multipart==0.0.18
pydantic==2.10.4
cryptography==48.0.0
```

- [ ] **Step 7: Add the key file to `backend/.gitignore`**

```
__pycache__
*.pyc
.venv
expense.duckdb
expense.duckdb.wal
.secret.key
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/crypto.py backend/tests/conftest.py backend/tests/test_crypto.py backend/requirements.txt backend/.gitignore
git commit -m "feat: add Fernet-based password encryption module"
```

---

### Task 3: `db.py` — password column, `register_account`/`update_account`

**Files:**
- Modify: `backend/app/db.py`
- Create: `backend/tests/test_db_accounts.py`

**Interfaces:**
- Consumes: `crypto.encrypt_password`, `crypto.decrypt_password` (Task 2).
- Produces: `register_account(..., password: str | None = None) -> dict` (dict now includes `has_password: bool`), `update_account(conn, account_id: int, **fields) -> dict | None` (accepts `nickname`, `account_last4`, `password`; returns `None` for an unknown id), `get_decrypted_password(conn, account_id: int) -> str | None`. `routes/accounts.py` (Task 4) and `routes/upload.py` (Task 5) both import from this module.

- [ ] **Step 1: Write the failing tests — `backend/tests/test_db_accounts.py`**

```python
from app.db import get_decrypted_password, register_account, update_account


def test_register_account_without_password_has_no_password(conn):
    account = register_account(conn, "upi", "PhonePe")
    assert account["has_password"] is False
    assert get_decrypted_password(conn, account["id"]) is None


def test_register_account_with_password_is_retrievable(conn):
    account = register_account(conn, "credit_card", "HSBC", password="s3cr3t")
    assert account["has_password"] is True
    assert "password" not in account
    assert "encrypted_password" not in account
    assert get_decrypted_password(conn, account["id"]) == "s3cr3t"


def test_update_account_sets_password(conn):
    account = register_account(conn, "credit_card", "HDFC")
    updated = update_account(conn, account["id"], password="new-pass")
    assert updated["has_password"] is True
    assert get_decrypted_password(conn, account["id"]) == "new-pass"


def test_update_account_clears_password_with_empty_string(conn):
    account = register_account(conn, "credit_card", "HDFC", password="old-pass")
    updated = update_account(conn, account["id"], password="")
    assert updated["has_password"] is False
    assert get_decrypted_password(conn, account["id"]) is None


def test_update_account_partial_update_leaves_password_untouched(conn):
    account = register_account(conn, "bank", "SBI", password="keepme")
    updated = update_account(conn, account["id"], nickname="Salary account")
    assert updated["nickname"] == "Salary account"
    assert updated["has_password"] is True
    assert get_decrypted_password(conn, account["id"]) == "keepme"


def test_update_account_unknown_id_returns_none(conn):
    assert update_account(conn, 999999, nickname="x") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_db_accounts.py -v`
Expected: FAIL — `register_account() got an unexpected keyword argument 'password'` (and `update_account`/`get_decrypted_password` don't exist yet).

- [ ] **Step 3: Add the import and the column migration in `backend/app/db.py`**

Add near the top, with the other imports:

```python
from . import crypto
```

Add a new migration function (place it directly above `_migrate_legacy_transactions_table`, i.e. after `_next_table_name`/`_default_label`/`_ACCOUNT_COLUMNS` and before `register_account` — or anywhere above `_bootstrap`'s call site is not required since Python resolves it at call time, but grouping it with the other migration helper keeps things easy to find):

```python
def _migrate_accounts_password_column(conn: duckdb.DuckDBPyConnection) -> None:
    """One-time upgrade: add the encrypted_password column to `accounts` if
    it doesn't exist yet. DuckDB's ALTER TABLE ADD COLUMN has no IF NOT
    EXISTS clause, so check information_schema first — same pattern as
    _migrate_legacy_transactions_table.
    """
    exists = conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounts' AND column_name = 'encrypted_password'
        """
    ).fetchone()
    if not exists:
        conn.execute("ALTER TABLE accounts ADD COLUMN encrypted_password VARCHAR")
```

In `_bootstrap`, call it immediately after the `accounts` table is created (before the `tags` table is created):

```python
def _bootstrap(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id BIGINT PRIMARY KEY,
            kind VARCHAR NOT NULL,        -- 'bank' | 'credit_card' | 'upi'
            provider VARCHAR NOT NULL,    -- 'HDFC', 'HSBC', 'PhonePe', ...
            nickname VARCHAR,
            account_last4 VARCHAR,
            table_name VARCHAR UNIQUE NOT NULL,
            parser VARCHAR,                -- key into parsers.detect.PARSERS_BY_KEY, or NULL
            label VARCHAR NOT NULL,        -- display label, e.g. 'HSBC Credit Card'
            created_at TIMESTAMP DEFAULT now()
        );
        """
    )
    _migrate_accounts_password_column(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
```

(the rest of `_bootstrap` is unchanged).

- [ ] **Step 4: Update `register_account`**

Replace the existing function:

```python
def register_account(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    provider: str,
    nickname: str | None = None,
    account_last4: str | None = None,
    parser: str | None = None,
    password: str | None = None,
) -> dict:
    """Create a new account's physical table, register it, and rebuild the
    union view. Returns the new `accounts` row as a dict (includes
    `table_name`; callers that don't want to expose that internal detail
    should pop it before returning to a client) plus `has_password`.

    `password`, if given, is encrypted before being stored — see
    `crypto.py`. It never appears in the returned dict.
    """
    table_name = _next_table_name(conn, kind, provider)
    label = _default_label(kind, provider)
    conn.execute(_ACCOUNT_TABLE_DDL.format(table_name=table_name))
    row = conn.execute(
        f"""
        INSERT INTO accounts (id, kind, provider, nickname, account_last4, table_name, parser, label)
        VALUES (nextval('seq_account'), ?, ?, ?, ?, ?, ?, ?)
        RETURNING {", ".join(_ACCOUNT_COLUMNS)}
        """,
        [kind, provider, nickname, account_last4, table_name, parser, label],
    ).fetchone()
    if password:
        conn.execute(
            "UPDATE accounts SET encrypted_password = ? WHERE id = ?",
            [crypto.encrypt_password(password), row[0]],
        )
    _rebuild_all_transactions_view(conn)
    result = dict(zip(_ACCOUNT_COLUMNS, row))
    result["has_password"] = bool(password)
    return result
```

- [ ] **Step 5: Add `update_account` and `get_decrypted_password`**

Add these two new functions directly after `register_account`:

```python
def update_account(conn: duckdb.DuckDBPyConnection, account_id: int, **fields) -> dict | None:
    """Partially update an existing account. `fields` may include any of
    `nickname`, `account_last4`, `password` — only keys actually passed are
    written (the route layer uses Pydantic's `exclude_unset` to build this
    dict, so an omitted field is left alone). `password=""` clears the
    stored password; a non-empty `password` is encrypted and stored.
    `kind`/`provider` are intentionally not accepted here — both are baked
    into the account's physical table name and parser assignment at
    creation time. Returns the refreshed account dict (matching
    `register_account`'s shape) or `None` if the id doesn't exist.
    """
    exists = conn.execute("SELECT 1 FROM accounts WHERE id = ?", [account_id]).fetchone()
    if not exists:
        return None

    sets: list[str] = []
    params: list = []
    if "nickname" in fields:
        sets.append("nickname = ?")
        params.append(fields["nickname"])
    if "account_last4" in fields:
        sets.append("account_last4 = ?")
        params.append(fields["account_last4"])
    if "password" in fields:
        password = fields["password"]
        sets.append("encrypted_password = ?")
        params.append(crypto.encrypt_password(password) if password else None)

    if sets:
        params.append(account_id)
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?", params)

    row = conn.execute(
        f"""
        SELECT {", ".join(_ACCOUNT_COLUMNS)}, encrypted_password IS NOT NULL AS has_password
        FROM accounts WHERE id = ?
        """,
        [account_id],
    ).fetchone()
    return dict(zip(_ACCOUNT_COLUMNS + ("has_password",), row))


def get_decrypted_password(conn: duckdb.DuckDBPyConnection, account_id: int) -> str | None:
    row = conn.execute(
        "SELECT encrypted_password FROM accounts WHERE id = ?", [account_id]
    ).fetchone()
    if not row or row[0] is None:
        return None
    return crypto.decrypt_password(row[0])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/tests/test_db_accounts.py
git commit -m "feat: store encrypted account passwords in db.py"
```

---

### Task 4: `routes/accounts.py` — password on create, `has_password`, PATCH

**Files:**
- Modify: `backend/app/routes/accounts.py`
- Create: `backend/tests/test_routes_accounts.py`

**Interfaces:**
- Consumes: `register_account`, `update_account` (Task 3).
- Produces: `POST /api/accounts` accepts `password`; `GET /api/accounts` rows include `has_password`; new `PATCH /api/accounts/{id}`. Task 8 (`Accounts.tsx`) and Task 9 (`Upload.tsx`) both call these routes.

- [ ] **Step 1: Write the failing tests — `backend/tests/test_routes_accounts.py`**

```python
def test_create_account_without_password(client):
    r = client.post("/api/accounts", json={"kind": "upi", "provider": "PhonePe"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_password"] is False
    assert "table_name" not in body
    assert "password" not in body


def test_create_account_with_password_not_echoed(client):
    r = client.post(
        "/api/accounts",
        json={"kind": "credit_card", "provider": "HSBC", "password": "s3cr3t"},
    )
    body = r.json()
    assert body["has_password"] is True
    assert "password" not in body
    assert "encrypted_password" not in body


def test_list_accounts_reports_has_password(client):
    client.post("/api/accounts", json={"kind": "bank", "provider": "SBI", "password": "x"})
    client.post("/api/accounts", json={"kind": "upi", "provider": "GPay"})
    r = client.get("/api/accounts")
    by_provider = {a["provider"]: a for a in r.json()}
    assert by_provider["SBI"]["has_password"] is True
    assert by_provider["GPay"]["has_password"] is False


def test_patch_account_updates_nickname_only(client):
    created = client.post(
        "/api/accounts", json={"kind": "bank", "provider": "ICICI", "password": "keep-me"}
    ).json()
    r = client.patch(f"/api/accounts/{created['id']}", json={"nickname": "Joint account"})
    assert r.status_code == 200
    body = r.json()
    assert body["nickname"] == "Joint account"
    assert body["has_password"] is True  # untouched by this PATCH


def test_patch_account_clears_password(client):
    created = client.post(
        "/api/accounts", json={"kind": "bank", "provider": "Axis", "password": "old"}
    ).json()
    r = client.patch(f"/api/accounts/{created['id']}", json={"password": ""})
    assert r.status_code == 200
    assert r.json()["has_password"] is False


def test_patch_unknown_account_404s(client):
    r = client.patch("/api/accounts/999999", json={"nickname": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_routes_accounts.py -v`
Expected: FAIL — `has_password` missing from responses, and `PATCH /api/accounts/{id}` 404s (route doesn't exist).

- [ ] **Step 3: Rewrite `backend/app/routes/accounts.py`**

```python
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import drop_account, get_conn, register_account, update_account
from ..parsers.detect import resolve_parser_key

router = APIRouter()

VALID_KINDS = ("bank", "credit_card", "upi")


class AccountCreate(BaseModel):
    kind: str  # 'bank' | 'credit_card' | 'upi'
    provider: str
    nickname: str | None = None
    account_last4: str | None = None
    password: str | None = None


class AccountUpdate(BaseModel):
    nickname: str | None = None
    account_last4: str | None = None
    password: str | None = None


@router.get("/accounts")
def list_accounts(kind: list[str] | None = Query(None)):
    conn = get_conn()
    where_sql, params = "", []
    if kind:
        where_sql = " WHERE kind IN (" + ",".join(["?"] * len(kind)) + ")"
        params = list(kind)
    rows = conn.execute(
        f"""
        SELECT id, kind, provider, nickname, account_last4, label, parser,
               encrypted_password IS NOT NULL AS has_password
        FROM accounts
        {where_sql}
        ORDER BY kind, provider, id
        """,
        params,
    ).fetchall()
    cols = [
        "id", "kind", "provider", "nickname", "account_last4", "label",
        "parser", "has_password",
    ]
    return [dict(zip(cols, r)) for r in rows]


@router.post("/accounts")
def create_account(a: AccountCreate):
    if a.kind not in VALID_KINDS:
        raise HTTPException(400, f"kind must be one of {VALID_KINDS}")
    if not a.provider.strip():
        raise HTTPException(400, "provider is required")
    conn = get_conn()
    account = register_account(
        conn,
        kind=a.kind,
        provider=a.provider.strip(),
        nickname=(a.nickname or None),
        account_last4=(a.account_last4 or None),
        parser=resolve_parser_key(a.kind, a.provider),
        password=(a.password or None),
    )
    account.pop("table_name", None)  # internal detail, not part of the API
    return account


@router.patch("/accounts/{account_id}")
def update_account_route(account_id: int, a: AccountUpdate):
    conn = get_conn()
    fields = a.model_dump(exclude_unset=True)
    account = update_account(conn, account_id, **fields)
    if account is None:
        raise HTTPException(404, "Unknown account")
    account.pop("table_name", None)
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int):
    conn = get_conn()
    if not drop_account(conn, account_id):
        raise HTTPException(404, "Unknown account")
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/accounts.py backend/tests/test_routes_accounts.py
git commit -m "feat: add PATCH /accounts and has_password to the accounts API"
```

---

### Task 5: `routes/upload.py` — parse/confirm/cancel

**Files:**
- Modify: `backend/app/routes/upload.py`
- Create: `backend/tests/test_routes_upload.py`

**Interfaces:**
- Consumes: `get_decrypted_password` (Task 3), `PARSERS_BY_KEY` (existing), `dedup_hash`/`get_conn` (existing), `apply_rules_to_transaction_ids` (existing).
- Produces: `POST /api/upload/parse` → `{"status": "duplicate_file", "message": str}` or `{"status": "ok", "upload_token": str, "parser": str, "rows": [{"index", "txn_date", "description", "amount", "txn_type", "account_last4", "instrument", "txn_ref", "txn_time", "transaction_id", "duplicate"}]}`. `POST /api/upload/confirm` (body `{"upload_token": str, "selected_indices": [int]}`) → `{"parser", "parsed", "inserted", "skipped_duplicates", "auto_tagged"}` (same shape the old `POST /upload` returned). `DELETE /api/upload/parse/{token}` → `{"ok": true}`. The old `POST /upload` is removed. Task 9 (`Upload.tsx`) is the sole consumer of all three.

- [ ] **Step 1: Write the failing tests — `backend/tests/test_routes_upload.py`**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_routes_upload.py -v`
Expected: FAIL — `404 Not Found` for `/api/upload/parse` and `/api/upload/confirm` (routes don't exist yet).

- [ ] **Step 3: Rewrite `backend/app/routes/upload.py`**

```python
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import dedup_hash, get_conn, get_decrypted_password
from ..parsers.base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError
from ..parsers.detect import PARSERS_BY_KEY
from .tags import apply_rules_to_transaction_ids

router = APIRouter()

_STAGING_TTL_SECONDS = 30 * 60


@dataclass
class StagedUpload:
    account_id: int
    table_name: str
    label: str
    filename: str
    sha256: str
    rows: list[ParsedTransaction]
    dedup_hashes: list[str]  # parallel to rows
    created_at: float = field(default_factory=time.monotonic)


_staging: dict[str, StagedUpload] = {}
_staging_lock = Lock()


def _sweep_staging() -> None:
    """Drop abandoned previews (parsed but never confirmed or cancelled)
    older than the TTL, so memory doesn't grow unbounded. Opportunistic —
    run at the top of every parse call rather than on a background thread,
    which is enough for a single-user local app.
    """
    cutoff = time.monotonic() - _STAGING_TTL_SECONDS
    with _staging_lock:
        expired = [t for t, s in _staging.items() if s.created_at < cutoff]
        for t in expired:
            del _staging[t]


def _row_to_preview(index: int, t: ParsedTransaction, duplicate: bool) -> dict:
    return {
        "index": index,
        "txn_date": str(t.txn_date),
        "description": t.description,
        "amount": str(t.amount),
        "txn_type": t.txn_type,
        "account_last4": t.account_last4,
        "instrument": t.instrument,
        "txn_ref": t.txn_ref,
        "txn_time": t.txn_time,
        "transaction_id": t.transaction_id,
        "duplicate": duplicate,
    }


class ConfirmUpload(BaseModel):
    upload_token: str
    selected_indices: list[int]


@router.post("/upload/parse")
async def parse_upload(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    password: str | None = Form(None),
):
    _sweep_staging()
    conn = get_conn()

    account = conn.execute(
        "SELECT table_name, parser, label FROM accounts WHERE id = ?", [account_id]
    ).fetchone()
    if not account:
        raise HTTPException(400, f"Unknown account_id {account_id}")
    table_name, parser_key, label = account
    parser = PARSERS_BY_KEY.get(parser_key) if parser_key else None
    if parser is None:
        raise HTTPException(
            400,
            f"No parser is configured yet for '{label}'. Share a sample "
            "statement to get one added, then try again.",
        )

    content = await file.read()
    sha = hashlib.sha256(content).hexdigest()

    existing = conn.execute(
        "SELECT id FROM uploaded_files WHERE sha256 = ?", [sha]
    ).fetchone()
    if existing:
        return {
            "status": "duplicate_file",
            "message": "This exact file was already uploaded.",
        }

    effective_password = password or get_decrypted_password(conn, account_id)

    try:
        parsed_txns = parser.parse(content, effective_password)
    except PasswordRequiredError as e:
        raise HTTPException(400, f"PDF password required or incorrect: {e}")
    except UnsupportedFileError as e:
        raise HTTPException(400, str(e))

    # Snapshot of hashes already committed to this account's table — same
    # semantics as the old single-shot upload: a hash is only a duplicate
    # against rows already in the DB, never against a sibling row in this
    # same batch (two genuinely distinct transactions can share a hash).
    existing_hashes = {
        r[0]
        for r in conn.execute(f'SELECT dedup_hash FROM "{table_name}"').fetchall()
    }

    dedup_hashes = []
    duplicates = []
    for t in parsed_txns:
        h = dedup_hash(t.txn_date, t.amount, t.description, t.account_last4, t.txn_ref)
        dedup_hashes.append(h)
        duplicates.append(h in existing_hashes)

    token = uuid.uuid4().hex
    with _staging_lock:
        _staging[token] = StagedUpload(
            account_id=account_id,
            table_name=table_name,
            label=label,
            filename=file.filename,
            sha256=sha,
            rows=parsed_txns,
            dedup_hashes=dedup_hashes,
        )

    return {
        "status": "ok",
        "upload_token": token,
        "parser": label,
        "rows": [
            _row_to_preview(i, t, d)
            for i, (t, d) in enumerate(zip(parsed_txns, duplicates))
        ],
    }


@router.post("/upload/confirm")
def confirm_upload(body: ConfirmUpload):
    with _staging_lock:
        staged = _staging.pop(body.upload_token, None)
    if staged is None:
        raise HTTPException(
            404, "Upload session expired or already confirmed — re-parse the file."
        )

    selected_indices = sorted(set(body.selected_indices))
    if any(i < 0 or i >= len(staged.rows) for i in selected_indices):
        raise HTTPException(400, "selected_indices contains an out-of-range index")

    conn = get_conn()
    inserted_ids: list[int] = []
    for i in selected_indices:
        t = staged.rows[i]
        row = conn.execute(
            f"""
            INSERT INTO "{staged.table_name}"
              (id, txn_date, description, amount, txn_type,
               account_last4, instrument, txn_ref, txn_time, transaction_id,
               source_file, raw, dedup_hash)
            VALUES (nextval('seq_txn'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            [
                t.txn_date,
                t.description,
                str(t.amount),
                t.txn_type,
                t.account_last4,
                t.instrument,
                t.txn_ref,
                t.txn_time,
                t.transaction_id,
                staged.filename,
                json.dumps(t.raw, default=str),
                staged.dedup_hashes[i],
            ],
        ).fetchone()
        inserted_ids.append(row[0])

    conn.execute(
        """
        INSERT INTO uploaded_files
          (id, filename, sha256, account_id, parser_used, rows_parsed, rows_inserted)
        VALUES (nextval('seq_file'), ?, ?, ?, ?, ?, ?)
        """,
        [
            staged.filename, staged.sha256, staged.account_id, staged.label,
            len(staged.rows), len(inserted_ids),
        ],
    )

    auto_tagged = apply_rules_to_transaction_ids(inserted_ids)

    return {
        "parser": staged.label,
        "parsed": len(staged.rows),
        "inserted": len(inserted_ids),
        "skipped_duplicates": len(staged.rows) - len(inserted_ids),
        "auto_tagged": auto_tagged,
    }


@router.delete("/upload/parse/{token}")
def cancel_upload(token: str):
    with _staging_lock:
        _staging.pop(token, None)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/upload.py backend/tests/test_routes_upload.py
git commit -m "feat: split upload into parse/confirm/cancel with server-side staging"
```

---

### Task 6: `api.ts` — types and `upload()` for the new flow

**Files:**
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: nothing (leaf task, no backend dependency to run against yet — verified by type-checking only).
- Produces: `RegisteredAccount` type, `ParsedRow` type, `ParseResult` union type, and `api.upload<T>(path, file, fields?: Record<string, string | undefined>)`. Tasks 8 and 9 both import these.

Note: the existing `Account` type (`{ instrument, account_last4 }`) is left untouched. It's used by `TransactionsView.tsx` for a `GET /transactions/accounts` call that has no matching backend route today (a pre-existing bug, out of scope here) — it is a different concept from the `accounts` table entity this plan adds, so the new type gets its own name, `RegisteredAccount`, to avoid confusing the two.

- [ ] **Step 1: Widen `api.upload`'s signature**

In `frontend/src/api.ts`, replace:

```ts
  upload: async <T>(path: string, file: File, password?: string): Promise<T> => {
    const fd = new FormData()
    fd.append('file', file)
    if (password) fd.append('password', password)
    const r = await fetch(BASE + path, { method: 'POST', body: fd })
    if (!r.ok) {
      const txt = await r.text()
      throw new Error(`${r.status}: ${txt}`)
    }
    return r.json() as Promise<T>
  },
```

with:

```ts
  upload: async <T>(
    path: string,
    file: File,
    fields?: Record<string, string | undefined>
  ): Promise<T> => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields ?? {})) {
      if (v) fd.append(k, v)
    }
    const r = await fetch(BASE + path, { method: 'POST', body: fd })
    if (!r.ok) {
      const txt = await r.text()
      throw new Error(`${r.status}: ${txt}`)
    }
    return r.json() as Promise<T>
  },
```

- [ ] **Step 2: Add the new types**

Add near the bottom of `frontend/src/api.ts`, after the existing `UploadResult` type:

```ts
export type RegisteredAccount = {
  id: number
  kind: 'bank' | 'credit_card' | 'upi'
  provider: string
  nickname: string | null
  account_last4: string | null
  label: string
  parser: string | null
  has_password: boolean
}

export type ParsedRow = {
  index: number
  txn_date: string
  description: string
  amount: string
  txn_type: string | null
  account_last4: string | null
  instrument: string | null
  txn_ref: string | null
  txn_time: string | null
  transaction_id: string | null
  duplicate: boolean
}

export type ParseResult =
  | { status: 'duplicate_file'; message: string }
  | { status: 'ok'; upload_token: string; parser: string; rows: ParsedRow[] }
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`
Expected: succeeds (no other file references the old 3-arg `api.upload(path, file, password)` call yet — `Upload.tsx` still does today, which Task 9 rewrites; if `build` fails here on that call site, that's expected and resolved by Task 9, not this task — confirm the failure is *only* in `Upload.tsx`'s existing `api.upload` call before moving on).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat: add RegisteredAccount/ParsedRow types and widen api.upload()"
```

---

### Task 7: Sidebar + routing for the Accounts page

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/pages/Accounts.tsx` (placeholder — filled in fully by Task 8; created here so the route has something to render and the build stays green)

**Interfaces:**
- Produces: route `/accounts`, sidebar link to it. Task 8 fills in `Accounts.tsx`'s real content.

- [ ] **Step 1: Add the nav link in `frontend/src/components/Sidebar.tsx`**

```tsx
import { NavLink } from 'react-router-dom'
import {
  CreditCard,
  Landmark,
  LayoutDashboard,
  Receipt,
  Tags as TagsIcon,
  Upload as UploadIcon,
  Wallet,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const links = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/transactions', label: 'Transactions', icon: Receipt },
  { to: '/credit-cards', label: 'Credit Cards', icon: CreditCard },
  { to: '/accounts', label: 'Accounts', icon: Landmark },
  { to: '/upload', label: 'Upload', icon: UploadIcon },
  { to: '/tags', label: 'Tags & Rules', icon: TagsIcon },
]
```

(rest of the file unchanged)

- [ ] **Step 2: Add the route in `frontend/src/App.tsx`**

```tsx
import { Navigate, Route, Routes } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import Dashboard from '@/pages/Dashboard'
import Upload from '@/pages/Upload'
import Transactions from '@/pages/Transactions'
import CreditCards from '@/pages/CreditCards'
import Accounts from '@/pages/Accounts'
import Tags from '@/pages/Tags'

export default function App() {
  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl px-6 py-8 lg:px-10">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/credit-cards" element={<CreditCards />} />
            <Route path="/tags" element={<Tags />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
```

- [ ] **Step 3: Create a placeholder `frontend/src/pages/Accounts.tsx`**

```tsx
export default function AccountsPage() {
  return <div>TODO: Task 8 fills this in</div>
}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && npm run build`
Expected: still fails only on `Upload.tsx`'s old `api.upload` call (Task 9 fixes that) — no new errors from Sidebar/App/Accounts.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/App.tsx frontend/src/pages/Accounts.tsx
git commit -m "feat: wire up the Accounts route and sidebar link"
```

---

### Task 8: `pages/Accounts.tsx` — full admin CRUD UI

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx` (replacing the Task 7 placeholder)

**Interfaces:**
- Consumes: `RegisteredAccount` type (Task 6), `GET/POST/PATCH/DELETE /api/accounts` (Task 4).

- [ ] **Step 1: Write the full page**

```tsx
import { useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { api, RegisteredAccount } from '@/api'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const KINDS = [
  { value: 'bank', label: 'Bank account' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'upi', label: 'UPI app' },
] as const

type Kind = (typeof KINDS)[number]['value']

type FormState = {
  kind: Kind
  provider: string
  nickname: string
  account_last4: string
  password: string
  clearPassword: boolean
}

const EMPTY_FORM: FormState = {
  kind: 'bank',
  provider: '',
  nickname: '',
  account_last4: '',
  password: '',
  clearPassword: false,
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setAccounts(await api.get<RegisteredAccount[]>('/accounts'))
  }

  useEffect(() => {
    load()
  }, [])

  function startAdd() {
    setForm(EMPTY_FORM)
    setEditingId(null)
    setError('')
    setShowForm(true)
  }

  function startEdit(a: RegisteredAccount) {
    setForm({
      kind: a.kind,
      provider: a.provider,
      nickname: a.nickname ?? '',
      account_last4: a.account_last4 ?? '',
      password: '',
      clearPassword: false,
    })
    setEditingId(a.id)
    setError('')
    setShowForm(true)
  }

  function cancelForm() {
    setShowForm(false)
    setEditingId(null)
  }

  async function submit() {
    setError('')
    try {
      if (editingId === null) {
        if (!form.provider.trim()) {
          setError('Provider is required.')
          return
        }
        await api.post('/accounts', {
          kind: form.kind,
          provider: form.provider.trim(),
          nickname: form.nickname || null,
          account_last4: form.account_last4 || null,
          password: form.password || null,
        })
      } else {
        await api.patch(`/accounts/${editingId}`, {
          nickname: form.nickname || null,
          account_last4: form.account_last4 || null,
          ...(form.clearPassword
            ? { password: '' }
            : form.password
              ? { password: form.password }
              : {}),
        })
      }
      setShowForm(false)
      setEditingId(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove(a: RegisteredAccount) {
    if (!confirm(`Delete "${a.label}" and all of its transactions?`)) return
    await api.del(`/accounts/${a.id}`)
    load()
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Accounts</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Register the banks, credit cards, and UPI apps you'll upload
            statements for.
          </p>
        </div>
        {!showForm && (
          <Button onClick={startAdd}>
            <Plus className="h-4 w-4" /> Add account
          </Button>
        )}
      </div>

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle>{editingId === null ? 'Add account' : 'Edit account'}</CardTitle>
            <CardDescription>
              {editingId === null
                ? "Kind and provider can't be changed later — delete and re-add if you get them wrong."
                : 'Kind and provider are fixed for an existing account.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <Label className="text-xs text-muted-foreground">Kind</Label>
                {editingId === null ? (
                  <Select
                    value={form.kind}
                    onValueChange={(v) => setForm({ ...form, kind: v as Kind })}
                  >
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {KINDS.map((k) => (
                        <SelectItem key={k.value} value={k.value}>
                          {k.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    className="mt-1"
                    value={KINDS.find((k) => k.value === form.kind)?.label ?? form.kind}
                    disabled
                  />
                )}
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Provider</Label>
                <Input
                  className="mt-1"
                  value={form.provider}
                  disabled={editingId !== null}
                  onChange={(e) => setForm({ ...form, provider: e.target.value })}
                  placeholder="e.g. HDFC, HSBC, PhonePe"
                />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Nickname (optional)</Label>
                <Input
                  className="mt-1"
                  value={form.nickname}
                  onChange={(e) => setForm({ ...form, nickname: e.target.value })}
                />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Last 4 digits (optional)</Label>
                <Input
                  className="mt-1"
                  value={form.account_last4}
                  onChange={(e) => setForm({ ...form, account_last4: e.target.value })}
                  maxLength={4}
                />
              </div>
              <div className="md:col-span-2">
                <Label className="text-xs text-muted-foreground">
                  Statement password (optional)
                </Label>
                <Input
                  className="mt-1"
                  type="password"
                  autoComplete="off"
                  value={form.password}
                  disabled={form.clearPassword}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  placeholder={
                    editingId === null
                      ? 'optional'
                      : '•••••• (leave blank to keep the saved password)'
                  }
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Only needed if the statement PDF is locked. Stored
                  encrypted, never shown again.
                </p>
                {editingId !== null && (
                  <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={form.clearPassword}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          clearPassword: e.target.checked,
                          password: '',
                        })
                      }
                    />
                    Clear saved password
                  </label>
                )}
              </div>
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button onClick={submit}>
                {editingId === null ? 'Add account' : 'Save changes'}
              </Button>
              <Button variant="outline" onClick={cancelForm}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden p-0">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead>Kind</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Nickname</TableHead>
              <TableHead>Last 4</TableHead>
              <TableHead>Parser</TableHead>
              <TableHead>Password</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accounts.map((a) => (
              <TableRow key={a.id}>
                <TableCell>
                  <Badge variant="outline" className="font-normal">
                    {a.kind}
                  </Badge>
                </TableCell>
                <TableCell className="font-medium">{a.provider}</TableCell>
                <TableCell className="text-muted-foreground">{a.nickname ?? '—'}</TableCell>
                <TableCell className="text-muted-foreground">
                  {a.account_last4 ?? '—'}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {a.parser ?? 'Not configured'}
                </TableCell>
                <TableCell>
                  {a.has_password ? (
                    <Badge variant="success">Saved</Badge>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="sm" onClick={() => startEdit(a)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => remove(a)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {accounts.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="h-32 text-center text-sm text-muted-foreground">
                  No accounts yet — add one above.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: still fails only on `Upload.tsx`'s old `api.upload` call (Task 9 fixes that) — no errors from `Accounts.tsx`.

- [ ] **Step 3: Manual verification**

Use the `run` skill to start both backend (`backend/run.sh`) and frontend (`npm run dev` in `frontend/`) dev servers, then in the browser:
1. Go to `/accounts`. Confirm the empty state shows.
2. Add a bank account with a password. Confirm it appears in the table with a "Saved" password badge and the correct kind/provider.
3. Edit it: change the nickname, leave the password field blank, save. Confirm the nickname changed and the password badge still shows "Saved".
4. Edit it again, check "Clear saved password", save. Confirm the badge now shows "—".
5. Delete the account (confirm dialog appears). Confirm it's gone from the table.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat: build the Accounts admin page"
```

---

### Task 9: `pages/Upload.tsx` — staged parse/preview/confirm flow

**Files:**
- Modify: `frontend/src/pages/Upload.tsx`

**Interfaces:**
- Consumes: `RegisteredAccount`, `ParsedRow`, `ParseResult` types and the widened `api.upload()` (Task 6); `POST /api/upload/parse`, `POST /api/upload/confirm`, `DELETE /api/upload/parse/{token}` (Task 5); `GET /api/accounts` (Task 4).

- [ ] **Step 1: Write the full page**

```tsx
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  FileText,
  Loader2,
  Upload as UploadIcon,
  XCircle,
} from 'lucide-react'
import {
  api,
  ParsedRow,
  ParseResult,
  RegisteredAccount,
  UploadResult,
} from '@/api'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn, formatCurrency, formatDate } from '@/lib/utils'

type Stage = 'form' | 'previewing' | 'done'
type PreviewRow = ParsedRow & { selected: boolean }

export default function Upload() {
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([])
  const [accountId, setAccountId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [duplicateMessage, setDuplicateMessage] = useState('')

  const [stage, setStage] = useState<Stage>('form')
  const [uploadToken, setUploadToken] = useState<string | null>(null)
  const [parserLabel, setParserLabel] = useState('')
  const [rows, setRows] = useState<PreviewRow[]>([])
  const [result, setResult] = useState<UploadResult | null>(null)

  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useNavigate()

  useEffect(() => {
    api.get<RegisteredAccount[]>('/accounts').then(setAccounts)
  }, [])

  async function handleParse() {
    if (!file || !accountId) return
    setBusy(true)
    setError('')
    setDuplicateMessage('')
    try {
      const r = await api.upload<ParseResult>('/upload/parse', file, {
        account_id: accountId,
        password: password || undefined,
      })
      if (r.status === 'duplicate_file') {
        setDuplicateMessage(r.message)
        return
      }
      setUploadToken(r.upload_token)
      setParserLabel(r.parser)
      setRows(r.rows.map((row) => ({ ...row, selected: !row.duplicate })))
      setStage('previewing')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function toggleRow(index: number) {
    setRows((rs) =>
      rs.map((r) => (r.index === index ? { ...r, selected: !r.selected } : r))
    )
  }

  async function handleConfirm() {
    if (!uploadToken) return
    setBusy(true)
    setError('')
    try {
      const selected_indices = rows.filter((r) => r.selected).map((r) => r.index)
      const r = await api.post<UploadResult>('/upload/confirm', {
        upload_token: uploadToken,
        selected_indices,
      })
      setResult(r)
      setStage('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    if (uploadToken) {
      api.del(`/upload/parse/${uploadToken}`).catch(() => {})
    }
    resetToForm()
  }

  function resetToForm() {
    setStage('form')
    setUploadToken(null)
    setRows([])
    setFile(null)
    setPassword('')
    setResult(null)
    setDuplicateMessage('')
    if (inputRef.current) inputRef.current.value = ''
  }

  const selectedCount = rows.filter((r) => r.selected).length
  const duplicateCount = rows.filter((r) => r.duplicate).length

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Upload statement</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Parse a statement, review the transactions, then confirm to store
          them.
        </p>
      </div>

      {stage === 'form' && (
        <Card>
          <CardHeader>
            <CardTitle>File</CardTitle>
            <CardDescription>
              PDF or CSV, matched against the account you pick below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div>
              <Label className="text-xs text-muted-foreground">Account</Label>
              {accounts.length === 0 ? (
                <p className="mt-1 text-sm text-muted-foreground">
                  No accounts yet —{' '}
                  <a href="/accounts" className="underline">
                    add one first
                  </a>
                  .
                </p>
              ) : (
                <Select value={accountId} onValueChange={setAccountId}>
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="Select account…" />
                  </SelectTrigger>
                  <SelectContent>
                    {accounts.map((a) => (
                      <SelectItem key={a.id} value={String(a.id)}>
                        {a.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            <div
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                const f = e.dataTransfer.files?.[0]
                if (f) setFile(f)
              }}
              onClick={() => inputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors',
                dragOver
                  ? 'border-primary bg-accent'
                  : 'border-border hover:border-primary/40 hover:bg-accent/40'
              )}
            >
              <div className="rounded-full bg-secondary p-3 text-secondary-foreground">
                <UploadIcon className="h-5 w-5" />
              </div>
              <div className="text-sm font-medium">Drop file here or click to browse</div>
              <div className="text-xs text-muted-foreground">PDF or CSV, up to ~25 MB</div>
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.csv"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="hidden"
              />
            </div>

            {file && (
              <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-3 py-2 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate font-medium">{file.name}</span>
                  <Badge variant="outline" className="shrink-0">
                    {Math.round(file.size / 1024)} KB
                  </Badge>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setFile(null)} disabled={busy}>
                  Remove
                </Button>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="password">Password (only if different from the saved one)</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="optional"
                autoComplete="off"
              />
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={handleParse} disabled={!file || !accountId || busy}>
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Parsing…
                  </>
                ) : (
                  <>
                    <UploadIcon className="h-4 w-4" /> Upload & parse
                  </>
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {duplicateMessage && (
        <Alert>
          <AlertTitle>Already uploaded</AlertTitle>
          <AlertDescription>{duplicateMessage}</AlertDescription>
        </Alert>
      )}

      {stage === 'previewing' && (
        <Card>
          <CardHeader>
            <CardTitle>Review parsed transactions</CardTitle>
            <CardDescription>
              Parser: {parserLabel} · {rows.length} parsed ·{' '}
              {rows.length - duplicateCount} new · {duplicateCount} duplicate
              {duplicateCount === 1 ? '' : 's'}. Uncheck anything you don't
              want to import.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead />
                    <TableHead>Date</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Instrument</TableHead>
                    <TableHead>Ref</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={r.index}>
                      <TableCell>
                        <input
                          type="checkbox"
                          role="checkbox"
                          checked={r.selected}
                          onChange={() => toggleRow(r.index)}
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDate(r.txn_date)}
                      </TableCell>
                      <TableCell className="max-w-xs truncate font-medium">
                        {r.description}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'whitespace-nowrap text-right font-semibold tabular-nums',
                          Number(r.amount) < 0 ? 'text-destructive' : 'text-emerald-600'
                        )}
                      >
                        {formatCurrency(Number(r.amount), 'INR')}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {r.txn_type ?? '—'}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {r.instrument ?? r.account_last4 ?? '—'}
                      </TableCell>
                      <TableCell className="max-w-[10rem] truncate font-mono text-xs text-muted-foreground">
                        {r.txn_ref ?? '—'}
                      </TableCell>
                      <TableCell>
                        {r.duplicate && <Badge variant="secondary">Duplicate</Badge>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex items-center gap-3">
              <Button onClick={handleConfirm} disabled={selectedCount === 0 || busy}>
                {busy ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" /> Confirming…
                  </>
                ) : (
                  `Confirm & import ${selectedCount}`
                )}
              </Button>
              <Button variant="outline" onClick={handleCancel} disabled={busy}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {error && (
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>Upload failed</AlertTitle>
          <AlertDescription className="whitespace-pre-wrap">{error}</AlertDescription>
        </Alert>
      )}

      {stage === 'done' && result && (
        <Alert variant="success">
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>File processed</AlertTitle>
          <AlertDescription>
            <div className="mt-1 flex flex-wrap gap-2">
              <Badge variant="outline">Parser: {result.parser ?? '—'}</Badge>
              <Badge variant="outline">Parsed: {result.parsed}</Badge>
              <Badge variant="outline">Inserted: {result.inserted}</Badge>
              <Badge variant="outline">Duplicates: {result.skipped_duplicates}</Badge>
              {typeof result.auto_tagged === 'number' && (
                <Badge variant="outline">Auto-tagged: {result.auto_tagged}</Badge>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <Button variant="outline" size="sm" onClick={() => nav('/transactions')}>
                View transactions →
              </Button>
              <Button variant="outline" size="sm" onClick={resetToForm}>
                Upload another
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npm run build`
Expected: succeeds with no errors.

- [ ] **Step 3: Manual verification**

Use the `run` skill to start both dev servers, then in the browser:
1. Go to `/accounts`, add a UPI account for provider "PhonePe" (auto-assigns the `phonepe` parser, no password needed).
2. Go to `/upload`, select that account, upload a real (or the test fixture) PhonePe CSV. Confirm the preview table appears with the right rows, none pre-checked as duplicate the first time.
3. Click **Confirm & import N**. Confirm the "File processed" summary appears and `/transactions` shows the new rows.
4. Upload the exact same file again. Confirm it shows the "Already uploaded" message with no preview table.
5. Add a credit-card account with a stored password, upload one of its statements without typing a password in the Upload form, and confirm it parses successfully using the saved password.
6. On a fresh parse, click **Cancel** instead of confirming; confirm returning to `/upload` shows the empty form again and nothing new appears in `/transactions`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Upload.tsx
git commit -m "feat: redesign Upload as a parse/preview/confirm flow"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (data model/security) → Tasks 2–3. §2 (backend API) → Tasks 4–5. §3 (frontend) → Tasks 6–9. §4 (error handling) → covered inline in Task 5 (400s/404s) and Task 9 (error/duplicate banners). §5 (testing) → Tasks 1–5 for backend automated tests; Tasks 8–9 use manual verification since no frontend test runner exists in this repo (confirmed via `package.json` — no vitest/jest).
- **Type consistency checked:** `RegisteredAccount`/`ParsedRow`/`ParseResult` (Task 6) match exactly what Tasks 4–5's endpoints return and what Tasks 8–9 consume. `has_password` is produced identically by `register_account`, `update_account`, and `list_accounts`'s SQL (Task 3–4). `StagedUpload`/`ConfirmUpload` field names in Task 5 match the JSON shapes documented in that task's Interfaces block and consumed in Task 9.
- **No placeholders remain** except the intentional one-line `Accounts.tsx` stub in Task 7, which Task 8 immediately replaces in full — this exists only so the app keeps building between those two commits.
