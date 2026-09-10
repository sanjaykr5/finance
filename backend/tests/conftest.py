import pytest


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
