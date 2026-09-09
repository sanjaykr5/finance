# Account admin page + staged (preview-before-commit) upload

Status: approved
Date: 2026-09-09

## Problem

Accounts (bank/credit card/UPI app) can currently only be registered via a
raw `POST /accounts` call — there's no UI for it. The upload flow inserts
parsed transactions into the database immediately, with no chance to review
what was parsed first. Statement passwords must be re-typed on every upload
because nothing persists them.

This redesigns both:

1. A new **Accounts** admin page for creating/editing/deleting accounts,
   including an optional statement password, stored encrypted.
2. A **staged upload** flow: parse first, show the user every parsed row,
   then commit only on explicit confirmation.

## Decisions made during brainstorming

- **Password storage**: encrypt at rest (Fernet), key in a local gitignored
  key file — not plaintext, not OS Keychain (would tie the DB to one Mac),
  not "don't persist" (defeats the point of the dropdown/no-retyping flow).
- **Accounts page scope**: list + add + edit + delete (not add-only).
- **Preview staging**: server-side, keyed by a short-lived upload token —
  not a stateless round-trip that reships every parsed row back on confirm.
- **Duplicate rows in preview**: shown, badged "Duplicate", pre-unchecked,
  but still user-selectable (not hidden, not forced-excluded).

## 1. Data model & security

### `accounts` table migration

Add one nullable column. `_bootstrap()` in `backend/app/db.py` already
contains a legacy-migration precedent (`_migrate_legacy_transactions_table`)
that checks `information_schema` before acting — follow the same shape,
since DuckDB's `ALTER TABLE ADD COLUMN` has no `IF NOT EXISTS` clause:

```python
def _migrate_accounts_password_column(conn) -> None:
    exists = conn.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'accounts' AND column_name = 'encrypted_password'
        """
    ).fetchone()
    if not exists:
        conn.execute("ALTER TABLE accounts ADD COLUMN encrypted_password VARCHAR")
```

Call this from `_bootstrap()` right after the `accounts` table is created
(before `_migrate_legacy_transactions_table`, so the column exists before
anything else touches `accounts`).

### Encryption module: `backend/app/crypto.py` (new)

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

- Add `cryptography` to `backend/requirements.txt`.
- Add `.secret.key` to `backend/.gitignore` (alongside `expense.duckdb`).
- The encrypted value never leaves `db.py`. Nothing in `routes/` or the API
  response layer ever sees `encrypted_password` — only a derived
  `has_password: bool`.

### `db.py` changes

- `register_account(conn, kind, provider, nickname=None, account_last4=None,
  parser=None, password=None)`: after the existing `INSERT ... RETURNING`,
  if `password` is truthy, run a follow-up
  `UPDATE accounts SET encrypted_password = ? WHERE id = ?` with the
  encrypted value. `_ACCOUNT_COLUMNS` (and thus the returned dict) is
  unchanged — password never flows through it.
- New `update_account(conn, account_id, **fields) -> dict | None`: accepts
  any of `nickname`, `account_last4`, `password` as keyword args; only
  fields actually passed (via the route's `exclude_unset`) get written.
  `password=""` clears the column (`NULL`); a non-empty value gets
  encrypted and stored. Returns the refreshed account dict (via
  `_ACCOUNT_COLUMNS`) or `None` if the id doesn't exist.
- New `get_decrypted_password(conn, account_id) -> str | None`: fetches
  `encrypted_password`, returns `None` if absent, else
  `crypto.decrypt_password(...)`.
- `has_password` for the API layer is derived, never read off a stale
  `_ACCOUNT_COLUMNS` row:
  - `list_accounts` query adds `encrypted_password IS NOT NULL AS
    has_password` directly (a fresh read, one per row).
  - `register_account` returns `has_password = bool(password)` directly —
    it just wrote (or didn't write) that value in this same call, so no
    extra read is needed.
  - `update_account` re-`SELECT`s `encrypted_password IS NOT NULL` after
    applying whatever subset of fields was passed, since the final state
    depends on whether this call set, cleared, or left the password alone.

## 2. Backend API

### `routes/accounts.py`

- `AccountCreate` gains `password: str | None = None`.
- `create_account` passes `password=a.password` through to
  `register_account`.
- `list_accounts` response includes `has_password: bool` per row (in place
  of ever including the encrypted value).
- New:
  ```python
  class AccountUpdate(BaseModel):
      nickname: str | None = None
      account_last4: str | None = None
      password: str | None = None

  @router.patch("/accounts/{account_id}")
  def update_account_route(account_id: int, a: AccountUpdate):
      conn = get_conn()
      fields = a.model_dump(exclude_unset=True)
      account = update_account(conn, account_id, **fields)
      if account is None:
          raise HTTPException(404, "Unknown account")
      account.pop("table_name", None)
      return account
  ```
  `kind`/`provider` are intentionally not editable — both are baked into
  the account's physical table name and parser assignment at creation time;
  changing either would orphan that mapping. To fix a wrong kind/provider,
  delete and re-add the account.
- `delete_account` unchanged.

### `routes/upload.py` — parse/confirm split

Replaces the current single `POST /upload` handler. Staging store:

```python
import time, uuid
from dataclasses import dataclass, field
from threading import Lock

@dataclass
class StagedUpload:
    account_id: int
    table_name: str
    label: str
    filename: str
    sha256: str
    rows: list[ParsedTransaction]
    dedup_hashes: list[str]      # parallel to rows
    duplicates: list[bool]       # parallel to rows
    created_at: float = field(default_factory=time.monotonic)

_staging: dict[str, StagedUpload] = {}
_staging_lock = Lock()
_STAGING_TTL_SECONDS = 30 * 60

def _sweep_staging() -> None:
    cutoff = time.monotonic() - _STAGING_TTL_SECONDS
    with _staging_lock:
        expired = [t for t, s in _staging.items() if s.created_at < cutoff]
        for t in expired:
            del _staging[t]
```

`_sweep_staging()` runs at the top of `POST /upload/parse` (cheap,
opportunistic — no background thread needed for a single-user local app).

**`POST /upload/parse`** (multipart: `file`, `account_id`, `password?`)
1. Look up the account (`table_name`, `parser`, `label`) — 400 if unknown
   account_id or no parser configured, same messages as today.
2. `sha256` the content; if `uploaded_files` already has this sha, return
   `{"status": "duplicate_file", "message": "This exact file was already
   uploaded."}` with no `upload_token` — frontend shows this and stops,
   exactly like today's early-return case.
3. Password precedence: form `password` if given, else
   `get_decrypted_password(conn, account_id)`, else `None`.
4. `parser.parse(content, password)` → same `PasswordRequiredError` /
   `UnsupportedFileError` → 400 handling as today.
5. Query `existing_hashes` from `table_name` (same query as today's insert
   loop). For each parsed row compute `dedup_hash(...)` and whether it's in
   `existing_hashes` → per-row `duplicate: bool`.
6. Store a `StagedUpload` under a fresh `uuid4().hex` token.
7. Response:
   ```json
   {
     "upload_token": "…",
     "parser": "HSBC Credit Card",
     "rows": [
       {"index": 0, "txn_date": "...", "description": "...", "amount": "...",
        "txn_type": "...", "account_last4": "...", "instrument": "...",
        "txn_ref": "...", "txn_time": "...", "transaction_id": "...",
        "duplicate": false},
       ...
     ]
   }
   ```
   (`raw` is intentionally omitted from the response — it stays server-side
   on the staged row for the eventual insert; no reason to ship it to the
   browser just to preview.)

**`POST /upload/confirm`** (JSON body: `{upload_token, selected_indices:
[int]}`)
1. `_staging.pop(upload_token, None)` (lock-guarded) — a missing token
   (never issued, already confirmed, or swept for TTL) → 404
   `"Upload session expired or already confirmed — re-parse the file."`
   Popping-on-lookup makes the token single-use, which is what prevents a
   double-click (or a stale second tab) from inserting twice.
2. For each index in `selected_indices`, insert the corresponding row from
   `staged.rows` using the exact insert statement `upload.py` uses today.
   Indices not in `selected_indices` are simply not inserted (this is how
   pre-unchecked duplicates — or anything else the user unchecked — get
   skipped; no separate "is this a duplicate" re-check is needed since the
   client only sends indices it wants inserted).
3. Insert one `uploaded_files` row: `rows_parsed = len(staged.rows)`,
   `rows_inserted = len(selected_indices)`.
4. `apply_rules_to_transaction_ids(inserted_ids)`.
5. Response: `{"parser": staged.label, "parsed": len(staged.rows),
   "inserted": len(selected_indices), "skipped_duplicates":
   len(staged.rows) - len(selected_indices), "auto_tagged": ...}` — same
   shape `UploadResult` already expects, so the existing success panel
   needs no changes.

**`DELETE /upload/parse/{token}`** (new) — best-effort
`_staging.pop(token, None)`; always returns `{"ok": true}` whether or not
the token existed (cancelling twice, or cancelling an already-expired
token, is not an error).

The old direct-insert `POST /upload` is removed — it's fully superseded by
`parse` + `confirm`, and keeping both would mean two insert code paths to
maintain.

## 3. Frontend

### `api.ts`

- Extend `upload()` to accept arbitrary extra form fields instead of only
  `password`:
  ```ts
  upload: async <T>(path: string, file: File, fields?: Record<string, string>): Promise<T> => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(fields ?? {})) fd.append(k, v)
    ...
  }
  ```
- Fix the `Account` type (currently `{instrument, account_last4}`, unused
  and wrong) to match what `GET /accounts` returns:
  ```ts
  export type Account = {
    id: number
    kind: 'bank' | 'credit_card' | 'upi'
    provider: string
    nickname: string | null
    account_last4: string | null
    label: string
    parser: string | null
    has_password: boolean
  }
  ```
- New types: `ParsedRow` (mirrors the `/upload/parse` row shape, plus a
  client-only `selected: boolean`), `ParseResult { upload_token, parser,
  rows: ParsedRow[] } | { status: 'duplicate_file', message: string }`.

### `pages/Accounts.tsx` (new)

- Sidebar gets a new entry above "Upload": `{ to: '/accounts', label:
  'Accounts', icon: Landmark }` (or similar `lucide-react` icon) in
  `Sidebar.tsx`'s `links` array. New route in `App.tsx`.
- Table: kind badge, provider, nickname, last4, label, "Parser: X / Not
  configured", "Password: ✓ saved / —", Edit/Delete icon buttons.
- "Add account" opens a form (reuse the `Card`/`Input`/`Label`/`Select`
  primitives already in `components/ui`): kind select (`bank` /
  `credit_card` / `upi`), provider text input, nickname (optional), last4
  (optional), password (optional, `type="password"`, helper text: "Only
  needed if the statement PDF is locked. Stored encrypted, never shown
  again."). Submit → `POST /accounts`.
- Edit reopens the same form pre-filled, with kind/provider rendered
  read-only (not disabled form fields — genuinely not part of the PATCH
  payload) and a note why. Password field starts blank with placeholder
  "•••••• (leave blank to keep the saved password)"; a small "Clear saved
  password" link sets it to be sent as `""`. Submit → `PATCH
  /accounts/{id}` with only changed fields.
- Delete → confirmation (reuse whatever confirm pattern `Tags.tsx` uses for
  `deleteTag`) → `DELETE /accounts/{id}`.

### `pages/Upload.tsx` redesign

State machine: `form → previewing → done`.

- **`form`**: file dropzone (unchanged) + account `<Select>` (populated
  from `GET /accounts`; empty state: "No accounts yet — add one in
  Accounts" linking to `/accounts`) + optional "Password (only if
  different from the saved one)" input. Button "Upload & parse" →
  `api.upload('/upload/parse', file, {account_id, password})`.
  - If the response is `{status: 'duplicate_file', ...}`, show the same
    "already uploaded" messaging as today and stay in `form`.
  - Otherwise store `{upload_token, rows}`, initialize each row's
    `selected = !duplicate`, move to `previewing`.
- **`previewing`**: summary line ("N parsed · N new · N duplicates"), a
  table (checkbox · date · description · amount · type · instrument/last4
  · ref, "Duplicate" badge on flagged rows), row checkboxes toggle
  `selected`. Two buttons:
  - **Confirm** (disabled if no row is `selected`) → `api.post
    ('/upload/confirm', {upload_token, selected_indices})` → on success,
    store the `UploadResult` and move to `done`.
  - **Cancel** → best-effort `api.del('/upload/parse/' + upload_token)`,
    discard local state, return to `form`.
- **`done`**: today's existing success `Alert` (parser/parsed/inserted/
  duplicates/auto-tagged badges + "View transactions →"), plus a way to
  start another upload (reset to `form`).

## 4. Error handling & edge cases

- Unknown account / no parser configured / wrong password / unsupported
  file: unchanged 400s, all surfaced at the `parse` step, before any
  preview/token exists.
- Confirming an unknown/expired/already-used token → 404 with a message
  telling the user to re-parse; frontend shows this as an error and drops
  back to `form` (the staged data is gone either way).
- Cancelling a token that's already expired or already confirmed is a
  no-op, not an error.
- Zero rows selected in the preview → Confirm is disabled client-side (no
  need for a server-side empty-selection error path).

## 5. Testing

- Backend: check for an existing test suite/framework first. If one
  exists, extend it; otherwise add focused `pytest` coverage for:
  - `crypto.py` encrypt/decrypt round-trip, and that the key file is
    created with `0600` perms on first use.
  - `PATCH /accounts/{id}`: partial updates via `exclude_unset`, password
    clearing via `""`, kind/provider rejected/ignored if sent.
  - `/upload/parse` → `/upload/confirm` happy path (inserts land, dedup
    hashes match, `uploaded_files` row is written, auto-tagging runs).
  - Duplicate-file short-circuit (no token issued).
  - Confirming a token twice → second call 404s, no double-insert.
  - Confirming with a subset of `selected_indices` → only those rows land.
- Frontend: no existing test setup to extend — verify manually via the
  `run` skill once implemented (add an account with a password, upload a
  matching statement with no password typed, confirm the preview inserts
  the expected rows, re-upload the same file and confirm duplicates are
  flagged and pre-unchecked).
