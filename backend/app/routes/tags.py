import re

from fastapi import APIRouter, HTTPException

from ..db import get_conn
from ..schemas import RuleCreate, TagCreate

router = APIRouter()


@router.get("/tags")
def list_tags():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.color, COUNT(tt.transaction_id) AS txn_count
        FROM tags t
        LEFT JOIN transaction_tags tt ON tt.tag_id = t.id
        GROUP BY t.id, t.name, t.color
        ORDER BY t.name
        """
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "color": r[2], "txn_count": r[3]} for r in rows
    ]


@router.post("/tags")
def create_tag(t: TagCreate):
    conn = get_conn()
    try:
        row = conn.execute(
            "INSERT INTO tags (id, name, color) VALUES (nextval('seq_tag'), ?, ?) "
            "RETURNING id, name, color",
            [t.name.strip(), t.color],
        ).fetchone()
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"id": row[0], "name": row[1], "color": row[2]}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM transaction_tags WHERE tag_id = ?", [tag_id])
    conn.execute("DELETE FROM tag_rules WHERE tag_id = ?", [tag_id])
    conn.execute("DELETE FROM tags WHERE id = ?", [tag_id])
    return {"ok": True}


@router.get("/rules")
def list_rules():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT r.id, r.tag_id, t.name, r.match_type, r.pattern, r.priority
        FROM tag_rules r
        JOIN tags t ON t.id = r.tag_id
        ORDER BY r.priority DESC, r.id
        """
    ).fetchall()
    return [
        {
            "id": r[0],
            "tag_id": r[1],
            "tag_name": r[2],
            "match_type": r[3],
            "pattern": r[4],
            "priority": r[5],
        }
        for r in rows
    ]


@router.post("/rules")
def create_rule(r: RuleCreate):
    if r.match_type not in ("contains", "regex"):
        raise HTTPException(400, "match_type must be 'contains' or 'regex'")
    if r.match_type == "regex":
        try:
            re.compile(r.pattern)
        except re.error as e:
            raise HTTPException(400, f"Invalid regex: {e}")
    conn = get_conn()
    row = conn.execute(
        "INSERT INTO tag_rules (id, tag_id, match_type, pattern, priority) "
        "VALUES (nextval('seq_rule'), ?, ?, ?, ?) RETURNING id",
        [r.tag_id, r.match_type, r.pattern, r.priority],
    ).fetchone()
    return {"id": row[0]}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM tag_rules WHERE id = ?", [rule_id])
    return {"ok": True}


def _matching_tag_id(description: str, rules) -> int | None:
    desc_lower = description.lower()
    for _rid, tag_id, match_type, pattern, _priority in rules:
        if match_type == "contains":
            if pattern.lower() in desc_lower:
                return tag_id
        elif match_type == "regex":
            try:
                if re.search(pattern, description, re.IGNORECASE):
                    return tag_id
            except re.error:
                continue
    return None


def apply_rules_to_transaction_ids(ids: list[int]) -> int:
    """Apply rules only to currently-untagged transactions among `ids`."""
    if not ids:
        return 0
    conn = get_conn()
    rules = conn.execute(
        "SELECT id, tag_id, match_type, pattern, priority FROM tag_rules "
        "ORDER BY priority DESC, id"
    ).fetchall()
    if not rules:
        return 0
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"""
        SELECT t.id, t.description
        FROM all_transactions t
        LEFT JOIN transaction_tags tt ON tt.transaction_id = t.id
        WHERE t.id IN ({placeholders}) AND tt.transaction_id IS NULL
        """,
        ids,
    ).fetchall()
    n = 0
    for txn_id, desc in rows:
        tag_id = _matching_tag_id(desc or "", rules)
        if tag_id is None:
            continue
        conn.execute(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            [txn_id, tag_id],
        )
        n += 1
    return n


@router.post("/rules/apply")
def apply_rules_to_existing():
    conn = get_conn()
    all_ids = [r[0] for r in conn.execute("SELECT id FROM all_transactions").fetchall()]
    n = apply_rules_to_transaction_ids(all_ids)
    return {"updated": n}
