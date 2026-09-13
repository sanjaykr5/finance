from datetime import date

from fastapi import APIRouter, Query

from ..db import get_conn, table_name_for_txn, upsert_transaction_meta
from ..schemas import TransactionUpdate

router = APIRouter()


def _build_filter(
    from_: date | None, to: date | None, tag: int | None, q: str | None,
    account: int | None = None, kind: list[str] | None = None,
    source: list[str] | None = None,
) -> tuple[str, list]:
    where: list[str] = []
    params: list = []
    if from_:
        where.append("t.txn_date >= ?")
        params.append(from_)
    if to:
        where.append("t.txn_date <= ?")
        params.append(to)
    if tag:
        where.append("tt.tag_id = ?")
        params.append(tag)
    if q:
        where.append("LOWER(t.description) LIKE ?")
        params.append(f"%{q.lower()}%")
    if account:
        where.append("t.account_id = ?")
        params.append(account)
    if kind:
        where.append("t.account_kind IN (" + ",".join(["?"] * len(kind)) + ")")
        params.extend(kind)
    if source:
        where.append("t.source IN (" + ",".join(["?"] * len(source)) + ")")
        params.extend(source)
    return (" WHERE " + " AND ".join(where)) if where else "", params


@router.get("/transactions")
def list_transactions(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    tag: int | None = None,
    q: str | None = None,
    account: int | None = None,
    kind: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
    limit: int = 200,
    offset: int = 0,
):
    conn = get_conn()
    where_sql, params = _build_filter(from_, to, tag, q, account, kind, source)
    sql = (
        """
        SELECT t.id, t.txn_date, t.description, t.amount, t.currency, t.source,
               t.txn_type, t.account_last4, t.instrument, t.txn_ref,
               t.txn_time, t.transaction_id, t.source_file, t.imported_at,
               t.account_id, tt.tag_id, tg.name AS tag_name, tg.color AS tag_color,
               tm.notes, COALESCE(tm.audited, FALSE) AS audited
        FROM all_transactions t
        LEFT JOIN transaction_tags tt ON tt.transaction_id = t.id
        LEFT JOIN tags tg ON tg.id = tt.tag_id
        LEFT JOIN transaction_meta tm ON tm.transaction_id = t.id
        """
        + where_sql
        + " ORDER BY t.txn_date DESC, t.id DESC LIMIT ? OFFSET ?"
    )
    params = params + [limit, offset]

    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        if d.get("amount") is not None:
            d["amount"] = float(d["amount"])
        if d.get("txn_date") is not None:
            d["txn_date"] = str(d["txn_date"])
        if d.get("imported_at") is not None:
            d["imported_at"] = str(d["imported_at"])
        out.append(d)
    return out


@router.get("/transactions/count")
def count_transactions(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    tag: int | None = None,
    q: str | None = None,
    account: int | None = None,
    kind: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
):
    conn = get_conn()
    where_sql, params = _build_filter(from_, to, tag, q, account, kind, source)
    sql = (
        """
        SELECT COUNT(*) FROM all_transactions t
        LEFT JOIN transaction_tags tt ON tt.transaction_id = t.id
        """
        + where_sql
    )
    (total,) = conn.execute(sql, params).fetchone()
    return {"total": int(total)}


@router.patch("/transactions/{txn_id}")
def update_transaction(txn_id: int, body: TransactionUpdate):
    conn = get_conn()
    fields = body.model_dump(exclude_unset=True)

    # tag_id lives in its own table (transaction_tags), same as before —
    # everything else (notes/audited) goes through transaction_meta.
    if "tag_id" in fields:
        tag_id = fields.pop("tag_id")
        conn.execute(
            "DELETE FROM transaction_tags WHERE transaction_id = ?", [txn_id]
        )
        if tag_id is not None:
            conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                [txn_id, tag_id],
            )

    if fields:
        upsert_transaction_meta(conn, txn_id, **fields)

    return {"ok": True}


@router.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int):
    conn = get_conn()
    table_name = table_name_for_txn(conn, txn_id)
    conn.execute(
        "DELETE FROM transaction_tags WHERE transaction_id = ?", [txn_id]
    )
    conn.execute(
        "DELETE FROM transaction_meta WHERE transaction_id = ?", [txn_id]
    )
    if table_name:
        conn.execute(f'DELETE FROM "{table_name}" WHERE id = ?', [txn_id])
    return {"ok": True}
