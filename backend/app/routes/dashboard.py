from fastapi import APIRouter, Query

from ..db import get_conn

router = APIRouter()


@router.get("/dashboard/summary")
def summary(month: str | None = Query(None, description="YYYY-MM")):
    """Dashboard is the categorized view, not the raw ingestion pool: a
    transaction only shows up here once it has a tag (applied by a rule, or
    manually) — everything else stays in the Transactions tab until then.
    """
    conn = get_conn()
    if month:
        where = "WHERE strftime(t.txn_date, '%Y-%m') = ?"
        params = [month]
    else:
        where = ""
        params: list = []

    totals = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.txn_type = 'debit' THEN -t.amount ELSE 0 END), 0) AS spend,
          COALESCE(SUM(CASE WHEN t.txn_type = 'credit' THEN t.amount ELSE 0 END), 0) AS credit,
          COALESCE(SUM(t.amount), 0) AS net
        FROM all_transactions t
        JOIN transaction_tags tt ON tt.transaction_id = t.id
        {where}
        """,
        params,
    ).fetchone()

    by_tag = conn.execute(
        f"""
        SELECT tg.name AS tag,
               COALESCE(SUM(CASE WHEN t.txn_type = 'debit' THEN -t.amount ELSE 0 END), 0) AS amount
        FROM all_transactions t
        JOIN transaction_tags tt ON tt.transaction_id = t.id
        JOIN tags tg ON tg.id = tt.tag_id
        {where}
        GROUP BY tg.name
        HAVING SUM(CASE WHEN t.txn_type = 'debit' THEN -t.amount ELSE 0 END) > 0
        ORDER BY amount DESC
        """,
        params,
    ).fetchall()

    recent = conn.execute(
        f"""
        SELECT t.id, t.txn_date, t.description, t.amount, t.source, tg.name AS tag
        FROM all_transactions t
        JOIN transaction_tags tt ON tt.transaction_id = t.id
        JOIN tags tg ON tg.id = tt.tag_id
        {where}
        ORDER BY t.txn_date DESC, t.id DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    return {
        "total_spend": float(totals[0]),
        "total_credit": float(totals[1]),
        "net": float(totals[2]),
        "by_tag": [{"tag": r[0], "amount": float(r[1])} for r in by_tag],
        "recent": [
            {
                "id": r[0],
                "txn_date": str(r[1]),
                "description": r[2],
                "amount": float(r[3]),
                "source": r[4],
                "tag": r[5],
            }
            for r in recent
        ],
    }
