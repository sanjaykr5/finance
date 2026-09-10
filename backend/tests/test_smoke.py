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
