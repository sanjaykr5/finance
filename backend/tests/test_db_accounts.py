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
