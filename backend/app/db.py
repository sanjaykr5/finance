import hashlib
import json
import re
import threading
from decimal import Decimal
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "expense.duckdb"

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def get_conn() -> duckdb.DuckDBPyConnection:
    """Return a per-call DuckDB cursor.

    DuckDB connections are not thread-safe for concurrent use, but `cursor()`
    gives each caller its own connection-like handle that shares the
    underlying database. FastAPI runs non-async routes in a threadpool, so
    handing out one cursor per request keeps queries from colliding.
    """
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                _conn = duckdb.connect(str(DB_PATH))
                _bootstrap(_conn)
    return _conn.cursor()


# Every account's transactions live in their own physical table (one per
# bank account / credit card / UPI app, registered via POST /api/accounts),
# all sharing this same column shape. `all_transactions` (rebuilt below)
# UNIONs them all so the rest of the app can keep querying one thing.
_ACCOUNT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS "{table_name}" (
    id BIGINT PRIMARY KEY,
    txn_date DATE NOT NULL,
    description VARCHAR NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    currency VARCHAR DEFAULT 'INR',
    txn_type VARCHAR,
    account_last4 VARCHAR,
    instrument VARCHAR,
    txn_ref VARCHAR,
    txn_time VARCHAR,
    transaction_id VARCHAR,
    source_file VARCHAR,
    raw JSON,
    -- Not UNIQUE: two genuinely distinct transactions in the same
    -- statement can legitimately share a hash (same merchant, same
    -- amount, same day, no reference number to tell them apart —
    -- HSBC in particular). Duplicate detection is handled in
    -- application code instead, see routes/upload.py.
    dedup_hash VARCHAR,
    imported_at TIMESTAMP DEFAULT now()
);
"""

_ACCOUNT_TABLE_COLUMNS = (
    "id, txn_date, description, amount, currency, txn_type, account_last4, "
    "instrument, txn_ref, txn_time, transaction_id, source_file, raw, "
    "dedup_hash, imported_at"
)


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id BIGINT PRIMARY KEY,
            name VARCHAR UNIQUE NOT NULL,
            color VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_tags (
            transaction_id BIGINT PRIMARY KEY,
            tag_id BIGINT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_rules (
            id BIGINT PRIMARY KEY,
            tag_id BIGINT NOT NULL,
            match_type VARCHAR NOT NULL,
            pattern VARCHAR NOT NULL,
            priority INTEGER DEFAULT 0
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded_files (
            id BIGINT PRIMARY KEY,
            filename VARCHAR,
            sha256 VARCHAR UNIQUE,
            account_id BIGINT,
            parser_used VARCHAR,
            rows_parsed INTEGER,
            rows_inserted INTEGER,
            uploaded_at TIMESTAMP DEFAULT now()
        );
        """
    )
    for seq in ("seq_txn", "seq_tag", "seq_rule", "seq_file", "seq_account"):
        conn.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq} START 1;")

    _migrate_legacy_transactions_table(conn)
    _rebuild_all_transactions_view(conn)


def _slugify(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return slug or "account"


_TABLE_PREFIX = {"bank": "bank", "credit_card": "card", "upi": "upi"}


def _next_table_name(conn: duckdb.DuckDBPyConnection, kind: str, provider: str) -> str:
    base = f"{_TABLE_PREFIX[kind]}_{_slugify(provider)}"
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM accounts WHERE table_name LIKE ?", [f"{base}_%"]
        ).fetchall()
    }
    n = 1
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def _default_label(kind: str, provider: str) -> str:
    if kind == "credit_card":
        return f"{provider} Credit Card"
    if kind == "bank":
        return f"{provider} Bank"
    return provider  # upi


_ACCOUNT_COLUMNS = (
    "id", "kind", "provider", "nickname", "account_last4", "table_name",
    "parser", "label",
)


def register_account(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    provider: str,
    nickname: str | None = None,
    account_last4: str | None = None,
    parser: str | None = None,
) -> dict:
    """Create a new account's physical table, register it, and rebuild the
    union view. Returns the new `accounts` row as a dict (includes
    `table_name`; callers that don't want to expose that internal detail
    should pop it before returning to a client).
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
    _rebuild_all_transactions_view(conn)
    return dict(zip(_ACCOUNT_COLUMNS, row))


def drop_account(conn: duckdb.DuckDBPyConnection, account_id: int) -> bool:
    """Delete an account, its table, and its transactions' tag assignments.
    Returns False if no such account exists.
    """
    row = conn.execute(
        "SELECT table_name FROM accounts WHERE id = ?", [account_id]
    ).fetchone()
    if not row:
        return False
    (table_name,) = row
    conn.execute(
        f'DELETE FROM transaction_tags WHERE transaction_id IN (SELECT id FROM "{table_name}")'
    )
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute("DELETE FROM accounts WHERE id = ?", [account_id])
    _rebuild_all_transactions_view(conn)
    return True


def table_name_for_txn(conn: duckdb.DuckDBPyConnection, txn_id: int) -> str | None:
    """Which account table a transaction id currently lives in (ids are
    globally unique, drawn from the shared seq_txn sequence, so this is
    unambiguous). None if the id doesn't exist in any account table.
    """
    row = conn.execute(
        "SELECT table_name FROM all_transactions WHERE id = ?", [txn_id]
    ).fetchone()
    return row[0] if row else None


def _rebuild_all_transactions_view(conn: duckdb.DuckDBPyConnection) -> None:
    """(Re)create the `all_transactions` view as a UNION ALL of every
    registered account's table. Called after every account add/remove so
    the rest of the app can always query one name regardless of how many
    accounts are registered.
    """
    conn.execute("DROP VIEW IF EXISTS all_transactions")
    accounts = conn.execute(
        "SELECT id, table_name, kind, label FROM accounts ORDER BY id"
    ).fetchall()

    if not accounts:
        # Shape-only, zero-row view so routes can query all_transactions
        # unconditionally even before any account is registered.
        conn.execute(
            """
            CREATE VIEW all_transactions AS
            SELECT
                NULL::BIGINT AS id, NULL::DATE AS txn_date,
                NULL::VARCHAR AS description, NULL::DECIMAL(12,2) AS amount,
                NULL::VARCHAR AS currency, NULL::VARCHAR AS txn_type,
                NULL::VARCHAR AS account_last4, NULL::VARCHAR AS instrument,
                NULL::VARCHAR AS txn_ref, NULL::VARCHAR AS txn_time,
                NULL::VARCHAR AS transaction_id, NULL::VARCHAR AS source_file,
                NULL::JSON AS raw, NULL::VARCHAR AS dedup_hash,
                NULL::TIMESTAMP AS imported_at, NULL::BIGINT AS account_id,
                NULL::VARCHAR AS account_kind, NULL::VARCHAR AS source,
                NULL::VARCHAR AS table_name
            WHERE FALSE
            """
        )
        return

    branches = []
    for acc_id, table_name, kind, label in accounts:
        safe_label = label.replace("'", "''")
        branches.append(
            f"""
            SELECT {_ACCOUNT_TABLE_COLUMNS},
                   {acc_id} AS account_id,
                   '{kind}' AS account_kind,
                   '{safe_label}' AS source,
                   '{table_name}' AS table_name
            FROM "{table_name}"
            """
        )
    conn.execute("CREATE VIEW all_transactions AS " + " UNION ALL ".join(branches))


# Sources the app used to write into a single `transactions` table, back
# before per-account tables existed, mapped to what they'd be registered as
# today. Anything not listed here falls back to a generic 'bank' account
# named after its old source string, with no parser assigned.
_LEGACY_SOURCE_MAP = {
    "HSBC Credit Card": ("credit_card", "HSBC", "hsbc_credit"),
    "HDFC Credit Card": ("credit_card", "HDFC", "hdfc_credit"),
    "Phonepe": ("upi", "PhonePe", "phonepe"),
}


def _migrate_legacy_transactions_table(conn: duckdb.DuckDBPyConnection) -> None:
    """One-time upgrade path from the single-`transactions`-table schema:
    split each distinct `source` into its own registered account + table,
    preserving row ids (so existing `transaction_tags` assignments keep
    resolving), then drop the old table. No-ops once already migrated,
    since the old table won't exist any more.
    """
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'transactions'"
    ).fetchone()
    if not exists:
        return

    sources = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source FROM transactions ORDER BY source"
        ).fetchall()
    ]
    for source in sources:
        kind, provider, parser = _LEGACY_SOURCE_MAP.get(source, ("bank", source, None))
        account = register_account(conn, kind, provider, parser=parser)
        table_name = account["table_name"]
        rows = conn.execute(
            """
            SELECT id, txn_date, description, amount, currency, txn_type,
                   account_last4, instrument, txn_ref, txn_time,
                   transaction_id, source_file, raw, imported_at
            FROM transactions WHERE source = ?
            """,
            [source],
        ).fetchall()
        for r in rows:
            (
                id_, txn_date, description, amount, currency, txn_type,
                account_last4, instrument, txn_ref, txn_time, transaction_id,
                source_file, raw, imported_at,
            ) = r
            if raw is not None and not isinstance(raw, str):
                raw = json.dumps(raw, default=str)
            dedup = dedup_hash(txn_date, amount, description, account_last4, txn_ref)
            conn.execute(
                f"""
                INSERT INTO "{table_name}"
                  (id, txn_date, description, amount, currency, txn_type,
                   account_last4, instrument, txn_ref, txn_time,
                   transaction_id, source_file, raw, dedup_hash, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    id_, txn_date, description, amount, currency, txn_type,
                    account_last4, instrument, txn_ref, txn_time,
                    transaction_id, source_file, raw, dedup, imported_at,
                ],
            )
    conn.execute("DROP TABLE transactions")


def dedup_hash(
    txn_date, amount: Decimal, description: str,
    account_last4: str | None, txn_ref: str | None,
) -> str:
    """Canonical dedup key, shared by every insert path. Scoped implicitly
    to one account's table (each account has its own table, so there's no
    need to fold a `source` discriminator into the hash any more).

    Amount is quantized to 2dp so the hash doesn't depend on how many
    decimal digits a given statement happened to print (e.g. "500" vs
    "500.00" for the same value).
    """
    amt = Decimal(amount).quantize(Decimal("0.01"))
    h_input = f"{txn_date}|{amt}|{description}|{account_last4 or ''}|{txn_ref or ''}"
    return hashlib.sha256(h_input.encode()).hexdigest()
