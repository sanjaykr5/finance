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


def test_update_account_with_no_fields_leaves_password_untouched(conn):
    account = register_account(conn, "bank", "SBI", password="keepme")
    updated = update_account(conn, account["id"])
    assert updated["has_password"] is True
    assert get_decrypted_password(conn, account["id"]) == "keepme"


def test_update_account_unknown_id_returns_none(conn):
    assert update_account(conn, 999999, password="x") is None


def test_migrate_drops_legacy_nickname_and_account_last4_columns():
    """A pre-existing database that still has the retired `nickname` and
    `account_last4` columns on `accounts` gets them dropped on bootstrap,
    without losing the rest of the row.
    """
    import duckdb

    from app import db as db_module

    legacy_conn = duckdb.connect(str(db_module.DB_PATH))
    legacy_conn.execute(
        """
        CREATE TABLE accounts (
            id BIGINT PRIMARY KEY,
            kind VARCHAR NOT NULL,
            provider VARCHAR NOT NULL,
            nickname VARCHAR,
            account_last4 VARCHAR,
            table_name VARCHAR UNIQUE NOT NULL,
            parser VARCHAR,
            label VARCHAR NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
        """
    )
    legacy_conn.execute(
        """
        INSERT INTO accounts (id, kind, provider, nickname, table_name, parser, label)
        VALUES (1, 'upi', 'PhonePe', 'My wallet', 'upi_phonepe_1', 'phonepe', 'PhonePe')
        """
    )
    # `all_transactions` is rebuilt on every bootstrap and unions each
    # account's own table, so the referenced physical table needs to exist.
    legacy_conn.execute(
        """
        CREATE TABLE "upi_phonepe_1" (
            id BIGINT PRIMARY KEY, txn_date DATE, description VARCHAR,
            amount DECIMAL(12,2), currency VARCHAR, txn_type VARCHAR,
            account_last4 VARCHAR, instrument VARCHAR, txn_ref VARCHAR,
            txn_time VARCHAR, transaction_id VARCHAR, source_file VARCHAR,
            raw JSON, dedup_hash VARCHAR, imported_at TIMESTAMP
        )
        """
    )
    legacy_conn.close()

    conn = db_module.get_conn()
    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'accounts'"
        ).fetchall()
    }
    assert "nickname" not in cols
    assert "account_last4" not in cols
    assert conn.execute("SELECT provider FROM accounts WHERE id = 1").fetchone()[0] == "PhonePe"
