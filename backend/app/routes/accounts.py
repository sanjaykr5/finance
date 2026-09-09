from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import drop_account, get_conn, register_account
from ..parsers.detect import resolve_parser_key

router = APIRouter()

VALID_KINDS = ("bank", "credit_card", "upi")


class AccountCreate(BaseModel):
    kind: str  # 'bank' | 'credit_card' | 'upi'
    provider: str
    nickname: str | None = None
    account_last4: str | None = None


@router.get("/accounts")
def list_accounts(kind: list[str] | None = Query(None)):
    conn = get_conn()
    where_sql, params = "", []
    if kind:
        where_sql = " WHERE kind IN (" + ",".join(["?"] * len(kind)) + ")"
        params = list(kind)
    rows = conn.execute(
        f"""
        SELECT id, kind, provider, nickname, account_last4, label, parser
        FROM accounts
        {where_sql}
        ORDER BY kind, provider, id
        """,
        params,
    ).fetchall()
    cols = ["id", "kind", "provider", "nickname", "account_last4", "label", "parser"]
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
    )
    account.pop("table_name", None)  # internal detail, not part of the API
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int):
    conn = get_conn()
    if not drop_account(conn, account_id):
        raise HTTPException(404, "Unknown account")
    return {"ok": True}
