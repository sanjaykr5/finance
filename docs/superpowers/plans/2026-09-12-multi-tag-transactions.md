# Multi-tag transactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a transaction carry any number of tags instead of exactly one, end to end (DB, API, rule auto-tagging, dashboard, UI).

**Architecture:** Widen `transaction_tags` from a single-column to a composite `(transaction_id, tag_id)` primary key; PATCH `/transactions/{id}` replaces a transaction's whole tag set in one call (`tag_ids: list[int]`); `list_transactions`/dashboard queries switch from row-multiplying joins to correlated-subquery tag aggregation so one transaction is always one row; rule auto-tagging applies every matching rule instead of only the first; the frontend gets a checkbox-popover multi-select replacing the single `<Select>`.

**Tech Stack:** FastAPI + DuckDB 1.1.3 (backend), React + TypeScript + Radix UI + Tailwind (frontend), pytest (backend tests; frontend has no test runner — `npm run build` is the verification gate there).

**Spec:** `docs/superpowers/specs/2026-09-12-multi-tag-transactions-design.md`

## Global Constraints

- DuckDB is pinned at 1.1.3 (`backend/requirements.txt`) — every SQL construct in this plan (composite PK, `duckdb_constraints()`, `list(... ORDER BY ...)`, `INSERT ... ON CONFLICT DO NOTHING RETURNING ...`) has been verified to work under that exact version.
- No new backend routes — tag assignment stays on the existing `PATCH /transactions/{id}`.
- PATCH keeps its `exclude_unset=True` semantics: a field absent from the request body is left untouched. `tag_ids` omitted → tags untouched; `tag_ids: []` → tags cleared.
- DuckDB can't `ALTER` a primary key in place — schema changes to `transaction_tags` follow the existing rename-recreate-copy pattern already used in `db.py` (`_migrate_accounts_drop_nickname_and_last4`), not a fresh migration strategy.
- Frontend has no test runner configured (no vitest/jest, no `.test.tsx` files anywhere). Frontend task verification is `npm run build` (`tsc -b && vite build`) — a real type-check + bundle, not a stand-in for tests that don't exist.
- New frontend UI primitives (`popover.tsx`, `checkbox.tsx`) follow the existing `select.tsx` pattern exactly: Radix primitive + `forwardRef` + `cn()` from `@/lib/utils`, no new styling system.

---

### Task 1: DB migration — composite primary key for `transaction_tags`

**Files:**
- Modify: `backend/app/db.py` (the `transaction_tags` `CREATE TABLE` block inside `_bootstrap()`, plus a new migration function)
- Test: `backend/tests/test_db_transaction_tags.py` (new)

**Interfaces:**
- Produces: `_migrate_transaction_tags_composite_pk(conn)` — idempotent, called from `_bootstrap()` right after the `transaction_tags` table is created. After bootstrap (fresh DB or upgraded one), `transaction_tags` has `PRIMARY KEY (transaction_id, tag_id)` and accepts multiple rows per `transaction_id`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_db_transaction_tags.py`:

```python
def test_fresh_database_gets_composite_primary_key_directly():
    """A brand-new database should get the composite PK straight away, no
    migration path needed."""
    from app import db as db_module

    conn = db_module.get_conn()
    pk = conn.execute(
        """
        SELECT constraint_column_names FROM duckdb_constraints()
        WHERE table_name = 'transaction_tags' AND constraint_type = 'PRIMARY KEY'
        """
    ).fetchone()
    assert pk[0] == ["transaction_id", "tag_id"]


def test_migrate_widens_transaction_tags_to_composite_primary_key():
    """A pre-existing database that still has the single-column primary key
    on transaction_tags (transaction_id only, i.e. one tag per transaction)
    gets it widened to a composite (transaction_id, tag_id) key on
    bootstrap, without losing the existing assignment.
    """
    import duckdb

    from app import db as db_module

    legacy_conn = duckdb.connect(str(db_module.DB_PATH))
    legacy_conn.execute(
        """
        CREATE TABLE transaction_tags (
            transaction_id BIGINT PRIMARY KEY,
            tag_id BIGINT NOT NULL
        )
        """
    )
    legacy_conn.execute("INSERT INTO transaction_tags VALUES (100, 1)")
    legacy_conn.close()

    conn = db_module.get_conn()

    pk = conn.execute(
        """
        SELECT constraint_column_names FROM duckdb_constraints()
        WHERE table_name = 'transaction_tags' AND constraint_type = 'PRIMARY KEY'
        """
    ).fetchone()
    assert pk[0] == ["transaction_id", "tag_id"]

    assert conn.execute(
        "SELECT transaction_id, tag_id FROM transaction_tags"
    ).fetchall() == [(100, 1)]

    # A transaction can now carry a second tag without violating the PK.
    conn.execute("INSERT INTO transaction_tags VALUES (100, 2)")
    assert conn.execute(
        "SELECT tag_id FROM transaction_tags WHERE transaction_id = 100 ORDER BY tag_id"
    ).fetchall() == [(1,), (2,)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_db_transaction_tags.py -v`
Expected: both FAIL — `pk[0] == ["transaction_id"]` (single column), not `["transaction_id", "tag_id"]`.

- [ ] **Step 3: Implement**

In `backend/app/db.py`, replace the `transaction_tags` `CREATE TABLE` block inside `_bootstrap()`:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_tags (
            transaction_id BIGINT PRIMARY KEY,
            tag_id BIGINT NOT NULL
        );
        """
    )
```

becomes:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transaction_tags (
            transaction_id BIGINT NOT NULL,
            tag_id BIGINT NOT NULL,
            PRIMARY KEY (transaction_id, tag_id)
        );
        """
    )
    _migrate_transaction_tags_composite_pk(conn)
```

Add the migration function near the other `_migrate_*` functions (e.g. right after `_migrate_accounts_password_column`, before `_migrate_tag_rules_account_id_column`):

```python
def _migrate_transaction_tags_composite_pk(conn: duckdb.DuckDBPyConnection) -> None:
    """One-time upgrade: transaction_tags used to have `transaction_id` as
    its sole primary key (one tag per transaction). Widen it to a composite
    (transaction_id, tag_id) key so a transaction can carry multiple tags.
    DuckDB can't ALTER a primary key in place, so rebuild the table under
    the new shape and copy the data across — same pattern as
    _migrate_accounts_drop_nickname_and_last4.
    """
    pk = conn.execute(
        """
        SELECT constraint_column_names FROM duckdb_constraints()
        WHERE table_name = 'transaction_tags' AND constraint_type = 'PRIMARY KEY'
        """
    ).fetchone()
    if pk is None or len(pk[0]) > 1:
        return  # already composite

    conn.execute("ALTER TABLE transaction_tags RENAME TO transaction_tags_legacy")
    conn.execute(
        """
        CREATE TABLE transaction_tags (
            transaction_id BIGINT NOT NULL,
            tag_id BIGINT NOT NULL,
            PRIMARY KEY (transaction_id, tag_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO transaction_tags SELECT transaction_id, tag_id FROM transaction_tags_legacy"
    )
    conn.execute("DROP TABLE transaction_tags_legacy")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_db_transaction_tags.py -v`
Expected: both PASS.

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (77 pre-existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_db_transaction_tags.py
git commit -m "feat: widen transaction_tags to a composite primary key

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 2: Backend — multi-tag PATCH + list/count endpoints

**Files:**
- Modify: `backend/app/schemas.py` (`TransactionUpdate`)
- Modify: `backend/app/routes/transactions.py` (`_build_filter`, `list_transactions`, `count_transactions`, `update_transaction`)
- Modify: `backend/tests/test_routes_transactions.py`

**Interfaces:**
- Consumes: Task 1's composite-PK `transaction_tags` table.
- Produces: every transaction JSON object now has `tags: [{id, name, color}]` (the `tag_id`/`tag_name`/`tag_color` fields are gone). `PATCH /transactions/{id}` accepts `tag_ids: list[int]`, replacing the transaction's whole tag set.

- [ ] **Step 1: Update the existing PATCH test and add new failing tests**

In `backend/tests/test_routes_transactions.py`, replace the tail of `test_patch_one_field_leaves_others_untouched`:

```python
    # A tag_id-only PATCH (the pre-existing TagPicker contract) must not
    # reset notes/audited back to their defaults.
    tag = client.post("/api/tags", json={"name": "Shared"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_id": tag["id"]})
    txn = client.get("/api/transactions").json()[0]
    assert txn["tag_id"] == tag["id"]
    assert txn["notes"] == "first"
    assert txn["audited"] is True
```

with:

```python
    # A tag_ids-only PATCH (the TagPicker contract) must not reset
    # notes/audited back to their defaults.
    tag = client.post("/api/tags", json={"name": "Shared"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [tag["id"]]})
    txn = client.get("/api/transactions").json()[0]
    assert [t["id"] for t in txn["tags"]] == [tag["id"]]
    assert txn["notes"] == "first"
    assert txn["audited"] is True
```

Then append these new tests to the end of the file:

```python
def test_untagged_transaction_has_empty_tags_list(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn = client.get("/api/transactions").json()[0]
    assert txn["tags"] == []


def test_patch_tag_ids_sets_multiple_tags(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    rent = client.post("/api/tags", json={"name": "rent"}).json()
    shared = client.post("/api/tags", json={"name": "shared"}).json()

    r = client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [rent["id"], shared["id"]]})
    assert r.status_code == 200

    txn = client.get("/api/transactions").json()[0]
    assert {t["id"] for t in txn["tags"]} == {rent["id"], shared["id"]}
    assert {t["name"] for t in txn["tags"]} == {"rent", "shared"}


def test_patch_tag_ids_replaces_the_whole_set(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    rent = client.post("/api/tags", json={"name": "rent"}).json()
    shared = client.post("/api/tags", json={"name": "shared"}).json()

    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [rent["id"], shared["id"]]})
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [shared["id"]]})

    txn = client.get("/api/transactions").json()[0]
    assert [t["id"] for t in txn["tags"]] == [shared["id"]]


def test_patch_tag_ids_empty_list_clears_all_tags(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    rent = client.post("/api/tags", json={"name": "rent"}).json()

    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [rent["id"]]})
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": []})

    txn = client.get("/api/transactions").json()[0]
    assert txn["tags"] == []


def test_tag_filter_matches_transaction_that_has_the_tag_among_others(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    rent = client.post("/api/tags", json={"name": "rent"}).json()
    shared = client.post("/api/tags", json={"name": "shared"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [rent["id"], shared["id"]]})

    r = client.get("/api/transactions", params={"tag": shared["id"]})
    assert [t["id"] for t in r.json()] == [txn_id]

    count = client.get("/api/transactions/count", params={"tag": shared["id"]}).json()
    assert count["total"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_transactions.py -v`
Expected: FAIL — `tag_ids` isn't accepted yet, transactions still return `tag_id`/`tag_name`/`tag_color`, not `tags`.

- [ ] **Step 3: Implement**

In `backend/app/schemas.py`:

```python
class TransactionUpdate(BaseModel):
    tag_id: int | None = None
    notes: str | None = None
    audited: bool | None = None
```

becomes:

```python
class TransactionUpdate(BaseModel):
    tag_ids: list[int] | None = None
    notes: str | None = None
    audited: bool | None = None
```

In `backend/app/routes/transactions.py`, `_build_filter`'s tag clause:

```python
    if tag:
        where.append("tt.tag_id = ?")
        params.append(tag)
```

becomes:

```python
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM transaction_tags tt "
            "WHERE tt.transaction_id = t.id AND tt.tag_id = ?)"
        )
        params.append(tag)
```

`list_transactions` (the SQL and the row-processing loop):

```python
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
```

becomes:

```python
    conn = get_conn()
    where_sql, params = _build_filter(from_, to, tag, q, account, kind, source)
    sql = (
        """
        SELECT t.id, t.txn_date, t.description, t.amount, t.currency, t.source,
               t.txn_type, t.account_last4, t.instrument, t.txn_ref,
               t.txn_time, t.transaction_id, t.source_file, t.imported_at,
               t.account_id,
               (SELECT list(tg.id ORDER BY tg.name) FROM transaction_tags tt
                JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tag_ids,
               (SELECT list(tg.name ORDER BY tg.name) FROM transaction_tags tt
                JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tag_names,
               (SELECT list(tg.color ORDER BY tg.name) FROM transaction_tags tt
                JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tag_colors,
               tm.notes, COALESCE(tm.audited, FALSE) AS audited
        FROM all_transactions t
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
        tag_ids = d.pop("tag_ids") or []
        tag_names = d.pop("tag_names") or []
        tag_colors = d.pop("tag_colors") or []
        d["tags"] = [
            {"id": tid, "name": name, "color": color}
            for tid, name, color in zip(tag_ids, tag_names, tag_colors)
        ]
        if d.get("amount") is not None:
            d["amount"] = float(d["amount"])
        if d.get("txn_date") is not None:
            d["txn_date"] = str(d["txn_date"])
        if d.get("imported_at") is not None:
            d["imported_at"] = str(d["imported_at"])
        out.append(d)
    return out
```

`count_transactions` — the `LEFT JOIN transaction_tags` it only needed for the old join-based tag filter is no longer needed (the filter is an `EXISTS` clause now):

```python
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
```

becomes:

```python
    conn = get_conn()
    where_sql, params = _build_filter(from_, to, tag, q, account, kind, source)
    sql = "SELECT COUNT(*) FROM all_transactions t" + where_sql
    (total,) = conn.execute(sql, params).fetchone()
    return {"total": int(total)}
```

`update_transaction`:

```python
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
```

becomes:

```python
    conn = get_conn()
    fields = body.model_dump(exclude_unset=True)

    # tag_ids lives in its own table (transaction_tags), same as before —
    # everything else (notes/audited) goes through transaction_meta. A PATCH
    # replaces the whole tag set: [] clears every tag, omitting the field
    # entirely leaves the existing tags untouched.
    if "tag_ids" in fields:
        tag_ids = fields.pop("tag_ids") or []
        conn.execute(
            "DELETE FROM transaction_tags WHERE transaction_id = ?", [txn_id]
        )
        if tag_ids:
            conn.executemany(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
                [(txn_id, tid) for tid in tag_ids],
            )

    if fields:
        upsert_transaction_meta(conn, txn_id, **fields)

    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_transactions.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all pass. `test_routes_tags.py` and `test_routes_accounts.py` in particular must still pass unchanged — they don't touch `tag_id`/`tag_ids` on transactions directly.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routes/transactions.py backend/tests/test_routes_transactions.py
git commit -m "feat: transactions carry a set of tags instead of one

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 3: Backend — rule auto-tagging applies every matching rule

**Files:**
- Modify: `backend/app/routes/tags.py` (`_matching_tag_id` → `_matching_tag_ids`, `apply_rules_to_transaction_ids`)
- Modify: `backend/tests/test_routes_tags.py`

**Interfaces:**
- Consumes: Task 1's composite-PK `transaction_tags`, Task 2's PATCH semantics (not called directly, but rule application writes to the same table PATCH does).
- Produces: `_matching_tag_ids(description, txn_account_id, rules) -> list[int]` (replaces `_matching_tag_id`, which returned a single `int | None`). `apply_rules_to_transaction_ids` return value is now a count of tag *assignments* added (a transaction matching two rules counts as 2), not transactions touched — this was already true in spirit (the docstring called it "updated" counts of work done); callers (`POST /rules/apply`'s `{"updated": n}`) are unaffected by the type, only by magnitude in multi-match cases.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes_tags.py`:

```python
def test_transaction_matching_two_rules_gets_both_tags(client, monkeypatch):
    """A transaction matching rules for two different tags picks up both,
    not just the highest-priority one."""
    subscriptions = client.post("/api/tags", json={"name": "subscriptions"}).json()
    amazon = client.post("/api/tags", json={"name": "amazon"}).json()

    _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "AMAZON PRIME SUBSCRIPTION"
    )

    client.post(
        "/api/rules",
        json={
            "tag_id": subscriptions["id"],
            "match_type": "contains",
            "pattern": "SUBSCRIPTION",
            "priority": 0,
        },
    )
    client.post(
        "/api/rules",
        json={"tag_id": amazon["id"], "match_type": "contains", "pattern": "AMAZON", "priority": 0},
    )

    r = client.post("/api/rules/apply", json={})
    assert r.status_code == 200
    assert r.json()["updated"] == 2

    txn = client.get("/api/transactions").json()[0]
    assert {t["id"] for t in txn["tags"]} == {subscriptions["id"], amazon["id"]}


def test_reapplying_rules_is_idempotent_and_additive(client, monkeypatch):
    """Running "Apply to existing" again after a transaction already has one
    matching tag still picks up a second tag it's newly eligible for,
    without duplicating the first.
    """
    rent = client.post("/api/tags", json={"name": "rent"}).json()
    recurring = client.post("/api/tags", json={"name": "recurring"}).json()

    _register_and_insert_one(
        client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Paid to Bank Account XXXXXX3811"
    )

    client.post(
        "/api/rules",
        json={
            "tag_id": rent["id"],
            "match_type": "regex",
            "pattern": r"Bank Account XXXXXX3811",
            "priority": 0,
        },
    )
    first = client.post("/api/rules/apply", json={})
    assert first.json()["updated"] == 1

    # A second rule (for a different tag) is added after the first apply.
    client.post(
        "/api/rules",
        json={"tag_id": recurring["id"], "match_type": "contains", "pattern": "Bank Account", "priority": 0},
    )
    second = client.post("/api/rules/apply", json={})
    assert second.json()["updated"] == 1  # only the newly-eligible tag, not a re-add of rent

    txn = client.get("/api/transactions").json()[0]
    assert {t["id"] for t in txn["tags"]} == {rent["id"], recurring["id"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_tags.py -v`
Expected: FAIL — current code stops at the first matching rule and skips any transaction that already has a tag, so `updated == 1` in the first test and the second `apply` call in the idempotency test does nothing (transaction already has a tag, gets excluded entirely).

- [ ] **Step 3: Implement**

In `backend/app/routes/tags.py`, replace `_matching_tag_id`:

```python
def _matching_tag_id(description: str, txn_account_id, rules) -> int | None:
    desc_lower = description.lower()
    for _rid, rule_tag_id, match_type, pattern, _priority, rule_account_id in rules:
        if rule_account_id is not None and rule_account_id != txn_account_id:
            continue
        if match_type == "contains":
            if pattern.lower() in desc_lower:
                return rule_tag_id
        elif match_type == "regex":
            try:
                if re.search(pattern, description, re.IGNORECASE):
                    return rule_tag_id
            except re.error:
                continue
    return None
```

with:

```python
def _matching_tag_ids(description: str, txn_account_id, rules) -> list[int]:
    """Every rule's tag that matches, in rule order (priority DESC, id) —
    a transaction can pick up more than one tag from more than one rule.
    """
    desc_lower = description.lower()
    matched: list[int] = []
    for _rid, rule_tag_id, match_type, pattern, _priority, rule_account_id in rules:
        if rule_account_id is not None and rule_account_id != txn_account_id:
            continue
        if match_type == "contains":
            hit = pattern.lower() in desc_lower
        elif match_type == "regex":
            try:
                hit = bool(re.search(pattern, description, re.IGNORECASE))
            except re.error:
                hit = False
        else:
            hit = False
        if hit and rule_tag_id not in matched:
            matched.append(rule_tag_id)
    return matched
```

Replace `apply_rules_to_transaction_ids`:

```python
def apply_rules_to_transaction_ids(ids: list[int], tag_id: int | None = None) -> int:
    """Apply rules only to currently-untagged transactions among `ids`.

    When `tag_id` is given, only that tag's rules are considered — used by
    the per-tag "Apply to existing" action so it doesn't also apply every
    other tag's rules. A rule with an `account_id` only matches
    transactions from that account; one with account_id NULL matches any.
    """
    if not ids:
        return 0
    conn = get_conn()
    if tag_id is None:
        rules = conn.execute(
            "SELECT id, tag_id, match_type, pattern, priority, account_id FROM tag_rules "
            "ORDER BY priority DESC, id"
        ).fetchall()
    else:
        rules = conn.execute(
            "SELECT id, tag_id, match_type, pattern, priority, account_id FROM tag_rules "
            "WHERE tag_id = ? ORDER BY priority DESC, id",
            [tag_id],
        ).fetchall()
    if not rules:
        return 0
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"""
        SELECT t.id, t.description, t.account_id
        FROM all_transactions t
        LEFT JOIN transaction_tags tt ON tt.transaction_id = t.id
        WHERE t.id IN ({placeholders}) AND tt.transaction_id IS NULL
        """,
        ids,
    ).fetchall()
    n = 0
    for txn_id, desc, txn_account_id in rows:
        matched_tag_id = _matching_tag_id(desc or "", txn_account_id, rules)
        if matched_tag_id is None:
            continue
        conn.execute(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            [txn_id, matched_tag_id],
        )
        n += 1
    return n
```

with:

```python
def apply_rules_to_transaction_ids(ids: list[int], tag_id: int | None = None) -> int:
    """Apply matching rules to every transaction among `ids`, adding
    whichever of their tags it doesn't already have — a transaction can
    pick up more than one tag in a single pass, and re-running this is
    idempotent (an already-assigned tag is left alone, not duplicated).

    When `tag_id` is given, only that tag's rules are considered — used by
    the per-tag "Apply to existing" action so it doesn't also apply every
    other tag's rules. A rule with an `account_id` only matches
    transactions from that account; one with account_id NULL matches any.

    Returns the number of tag *assignments* actually added (a transaction
    newly matching two rules counts as 2; a rule whose tag the transaction
    already has counts as 0), not the number of transactions touched.
    """
    if not ids:
        return 0
    conn = get_conn()
    if tag_id is None:
        rules = conn.execute(
            "SELECT id, tag_id, match_type, pattern, priority, account_id FROM tag_rules "
            "ORDER BY priority DESC, id"
        ).fetchall()
    else:
        rules = conn.execute(
            "SELECT id, tag_id, match_type, pattern, priority, account_id FROM tag_rules "
            "WHERE tag_id = ? ORDER BY priority DESC, id",
            [tag_id],
        ).fetchall()
    if not rules:
        return 0
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"""
        SELECT t.id, t.description, t.account_id
        FROM all_transactions t
        WHERE t.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    n = 0
    for txn_id, desc, txn_account_id in rows:
        for matched_tag_id in _matching_tag_ids(desc or "", txn_account_id, rules):
            inserted = conn.execute(
                "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?) "
                "ON CONFLICT DO NOTHING RETURNING transaction_id",
                [txn_id, matched_tag_id],
            ).fetchall()
            if inserted:
                n += 1
    return n
```

Note the `RETURNING transaction_id` + checking `inserted` is what keeps `n` an accurate count of *new* assignments rather than counting a no-op `ON CONFLICT DO NOTHING` as a hit — without it, re-running "Apply to existing" would over-report.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_tags.py -v`
Expected: all PASS, including the pre-existing tests (`test_rules_apply_can_be_scoped_to_a_single_tag`, `test_rules_apply_without_tag_id_still_applies_every_tag`, `test_rule_scoped_to_an_account_only_matches_that_account`) — none of those exercise multi-match or re-apply, so their counts are unchanged by this rewrite.

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/tags.py backend/tests/test_routes_tags.py
git commit -m "feat: auto-tag rules apply every match instead of only the first

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 4: Backend — dashboard totals/recent multi-tag fix

**Files:**
- Modify: `backend/app/routes/dashboard.py` (`summary`)
- Test: `backend/tests/test_routes_dashboard.py` (new)

**Interfaces:**
- Consumes: Task 1's composite-PK `transaction_tags`.
- Produces: `GET /dashboard/summary` — `total_spend`/`total_credit`/`net` no longer double-count a multi-tagged transaction's amount; `by_tag` is unchanged (a transaction's amount legitimately appears under each of its tags); `recent[].tag: string | null` becomes `recent[].tags: string[]`, one entry per transaction regardless of tag count.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_routes_dashboard.py`:

```python
from datetime import date
from decimal import Decimal

from app.parsers.base import ParsedTransaction
from app.parsers.detect import PARSERS_BY_KEY


class _FakeParser:
    """Stands in for a real parser so a test can insert one transaction
    without needing a real statement file for the format.
    """

    def __init__(self, source, description):
        self.source = source
        self._description = description

    def can_parse(self, filename, content):
        return True

    def parse(self, content, password=None):
        return [
            ParsedTransaction(
                txn_date=date(2025, 1, 15),
                description=self._description,
                amount=Decimal("-100.00"),
                txn_type="debit",
            )
        ]


def _register_and_insert_one(client, monkeypatch, kind, provider, parser_key, description):
    monkeypatch.setitem(PARSERS_BY_KEY, parser_key, _FakeParser(provider, description))
    account = client.post("/api/accounts", json={"kind": kind, "provider": provider}).json()
    assert account["parser"] == parser_key
    token = client.post(
        "/api/upload/parse",
        data={"account_id": str(account["id"])},
        files={"file": ("s.csv", f"irrelevant-{provider}".encode(), "text/csv")},
    ).json()["upload_token"]
    r = client.post(
        "/api/upload/confirm",
        json={"upload_token": token, "selected_indices": [0]},
    )
    assert r.status_code == 200
    return account


def test_untagged_transaction_excluded_from_dashboard(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "HDFC txn")

    body = client.get("/api/dashboard/summary?month=2025-01").json()
    assert body["total_spend"] == 0
    assert body["recent"] == []


def test_totals_do_not_double_count_a_multi_tagged_transaction(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Dinner")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    food = client.post("/api/tags", json={"name": "food"}).json()
    date_night = client.post("/api/tags", json={"name": "date-night"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [food["id"], date_night["id"]]})

    body = client.get("/api/dashboard/summary?month=2025-01").json()
    assert body["total_spend"] == 100.0


def test_by_tag_breakdown_lists_the_transaction_under_each_of_its_tags(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Dinner")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    food = client.post("/api/tags", json={"name": "food"}).json()
    date_night = client.post("/api/tags", json={"name": "date-night"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [food["id"], date_night["id"]]})

    body = client.get("/api/dashboard/summary?month=2025-01").json()
    by_tag = {row["tag"]: row["amount"] for row in body["by_tag"]}
    assert by_tag == {"food": 100.0, "date-night": 100.0}


def test_recent_lists_a_multi_tagged_transaction_once_with_both_tags(client, monkeypatch):
    _register_and_insert_one(client, monkeypatch, "credit_card", "HDFC", "hdfc_credit", "Dinner")
    txn_id = client.get("/api/transactions").json()[0]["id"]
    food = client.post("/api/tags", json={"name": "food"}).json()
    date_night = client.post("/api/tags", json={"name": "date-night"}).json()
    client.patch(f"/api/transactions/{txn_id}", json={"tag_ids": [food["id"], date_night["id"]]})

    body = client.get("/api/dashboard/summary?month=2025-01").json()
    assert len(body["recent"]) == 1
    assert set(body["recent"][0]["tags"]) == {"food", "date-night"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_dashboard.py -v`
Expected: `test_totals_do_not_double_count_a_multi_tagged_transaction` FAILs (`total_spend == 200.0`, doubled by the join); `test_recent_lists_a_multi_tagged_transaction_once_with_both_tags` FAILs (`len(recent) == 2`, one row per tag, and there's no `tags` key yet — `KeyError`).

- [ ] **Step 3: Implement**

Replace the whole `summary` function body in `backend/app/routes/dashboard.py`:

```python
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
```

with:

```python
@router.get("/dashboard/summary")
def summary(month: str | None = Query(None, description="YYYY-MM")):
    """Dashboard is the categorized view, not the raw ingestion pool: a
    transaction only shows up here once it has a tag (applied by a rule, or
    manually) — everything else stays in the Transactions tab until then.
    """
    conn = get_conn()
    tagged_exists = "EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id)"
    if month:
        where = "WHERE strftime(t.txn_date, '%Y-%m') = ?"
        where_tagged = where + " AND " + tagged_exists
        params = [month]
    else:
        where = ""
        where_tagged = "WHERE " + tagged_exists
        params: list = []

    # totals and recent must see one row per transaction regardless of how
    # many tags it has (an EXISTS filter, not a JOIN, or a multi-tagged
    # transaction's amount gets summed once per tag). by_tag is the
    # opposite on purpose: a transaction tagged both "food" and
    # "date-night" should contribute to both buckets.
    totals = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN t.txn_type = 'debit' THEN -t.amount ELSE 0 END), 0) AS spend,
          COALESCE(SUM(CASE WHEN t.txn_type = 'credit' THEN t.amount ELSE 0 END), 0) AS credit,
          COALESCE(SUM(t.amount), 0) AS net
        FROM all_transactions t
        {where_tagged}
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
        SELECT t.id, t.txn_date, t.description, t.amount, t.source,
               (SELECT list(tg.name ORDER BY tg.name) FROM transaction_tags tt
                JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tags
        FROM all_transactions t
        {where_tagged}
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
                "tags": r[5] or [],
            }
            for r in recent
        ],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_routes_dashboard.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/dashboard.py backend/tests/test_routes_dashboard.py
git commit -m "fix: dashboard totals no longer double-count multi-tagged transactions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 5: Frontend — add Popover and Checkbox UI primitives

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (via `npm install`)
- Create: `frontend/src/components/ui/popover.tsx`
- Create: `frontend/src/components/ui/checkbox.tsx`

**Interfaces:**
- Produces: `Popover`, `PopoverTrigger`, `PopoverAnchor`, `PopoverContent` (wrapping `@radix-ui/react-popover`) and `Checkbox` (wrapping `@radix-ui/react-checkbox`), matching the existing `select.tsx` pattern — `forwardRef` + `cn()` from `@/lib/utils`. `Checkbox` accepts the same `checked`/`onCheckedChange`/`disabled` props as Radix's `Checkbox.Root`.

No automated test exists for this repo's frontend (see Global Constraints) — verification is `npm run build` succeeding, since `tsconfig.json`'s `"include": ["src"]` type-checks every file under `src` whether or not anything imports it yet.

- [ ] **Step 1: Install the new dependencies**

Run: `cd frontend && npm install @radix-ui/react-popover @radix-ui/react-checkbox`

Expected: `package.json`/`package-lock.json` gain entries for both packages (alongside the existing `@radix-ui/react-select`, `@radix-ui/react-label`, etc.).

- [ ] **Step 2: Create `frontend/src/components/ui/popover.tsx`**

```tsx
import * as React from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { cn } from '@/lib/utils'

export const Popover = PopoverPrimitive.Root
export const PopoverTrigger = PopoverPrimitive.Trigger
export const PopoverAnchor = PopoverPrimitive.Anchor

export const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = 'start', sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      className={cn(
        'z-50 w-72 rounded-md border bg-popover p-4 text-popover-foreground shadow-md outline-none',
        'data-[state=open]:animate-in data-[state=closed]:animate-out',
        'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
        'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
        'data-[side=bottom]:slide-in-from-top-2',
        'data-[side=top]:slide-in-from-bottom-2',
        className
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
))
PopoverContent.displayName = PopoverPrimitive.Content.displayName
```

- [ ] **Step 3: Create `frontend/src/components/ui/checkbox.tsx`**

```tsx
import * as React from 'react'
import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

export const Checkbox = React.forwardRef<
  React.ElementRef<typeof CheckboxPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      'peer h-4 w-4 shrink-0 rounded-sm border border-primary shadow',
      'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
      'disabled:cursor-not-allowed disabled:opacity-50',
      'data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
      className
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
      <Check className="h-3.5 w-3.5" />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
))
Checkbox.displayName = CheckboxPrimitive.Root.displayName
```

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npm run build`
Expected: succeeds (same output shape as the pre-change baseline — `tsc -b && vite build` with no errors).

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/ui/popover.tsx frontend/src/components/ui/checkbox.tsx
git commit -m "feat: add Popover and Checkbox UI primitives

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 6: Frontend — `api.ts` types + `TagPicker` multi-select rewrite

**Files:**
- Modify: `frontend/src/api.ts` (`Transaction`, `DashboardSummary`)
- Modify: `frontend/src/components/TagPicker.tsx`

**Interfaces:**
- Consumes: Task 2's `tags: [{id,name,color}]` transaction shape, Task 4's `recent[].tags: string[]`, Task 5's `Popover`/`Checkbox` primitives.
- Produces: `TagPicker` now takes `current: Tag[]` (was `number | null`) and PATCHes `tag_ids` (was `tag_id`) on every toggle.

- [ ] **Step 1: Update `frontend/src/api.ts`**

In the `Transaction` type:

```typescript
  tag_id: number | null
  tag_name: string | null
  tag_color: string | null
```

becomes:

```typescript
  tags: Tag[]
```

(`Tag` is declared later in the same file — fine, TypeScript type references aren't order-dependent.)

In `DashboardSummary`:

```typescript
  recent: {
    id: number
    txn_date: string
    description: string
    amount: number
    source: string
    tag: string | null
  }[]
```

becomes:

```typescript
  recent: {
    id: number
    txn_date: string
    description: string
    amount: number
    source: string
    tags: string[]
  }[]
```

- [ ] **Step 2: Rewrite `frontend/src/components/TagPicker.tsx`**

```tsx
import { useState } from 'react'
import { api, Tag } from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

export default function TagPicker({
  txnId,
  current,
  tags,
  onChange,
}: {
  txnId: number
  current: Tag[]
  tags: Tag[]
  onChange?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const currentIds = new Set(current.map((t) => t.id))

  async function toggle(tagId: number) {
    setBusy(true)
    try {
      const next = currentIds.has(tagId)
        ? [...currentIds].filter((id) => id !== tagId)
        : [...currentIds, tagId]
      await api.patch(`/transactions/${txnId}`, { tag_ids: next })
      onChange?.()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 min-w-[140px] justify-start"
          disabled={busy}
        >
          {current.length === 0 ? (
            <span className="text-xs text-muted-foreground">Untagged</span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {current.map((t) => (
                <Badge key={t.id} variant="secondary" className="px-1.5 py-0 text-xs">
                  {t.name}
                </Badge>
              ))}
            </div>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-48 p-1" align="start">
        {tags.map((t) => (
          <label
            key={t.id}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
          >
            <Checkbox
              checked={currentIds.has(t.id)}
              onCheckedChange={() => toggle(t.id)}
            />
            {t.name}
          </label>
        ))}
        {tags.length === 0 && (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">No tags yet.</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
```

Note this file alone won't compile standalone yet — its only caller, `TransactionsView.tsx`, still passes `current={t.tag_id}` (a `number | null`) until Task 7. That's expected; Task 7 completes the wiring and is where `npm run build` needs to pass end to end.

- [ ] **Step 3: Verify `api.ts` compiles in isolation**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: reports errors only in `TransactionsView.tsx` (the `current={t.tag_id}` / `t.tag` mismatches this task doesn't touch) and `Dashboard.tsx` (the `t.tag` reference) — not in `api.ts` or `TagPicker.tsx` themselves. This confirms this task's own files are correct without yet requiring the full-build gate, which arrives in Task 7.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts frontend/src/components/TagPicker.tsx
git commit -m "feat: TagPicker becomes a multi-select

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```

---

### Task 7: Frontend — wire `TransactionsView`, `Dashboard`, `Tags` copy

**Files:**
- Modify: `frontend/src/components/TransactionsView.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx`
- Modify: `frontend/src/pages/Tags.tsx`

**Interfaces:**
- Consumes: Task 6's `Transaction.tags: Tag[]`, `DashboardSummary.recent[].tags: string[]`, and the rewritten `TagPicker`.
- Produces: a fully wired multi-tag UI; `npm run build` passes with zero errors, matching the pre-change baseline.

- [ ] **Step 1: Update `TransactionsView.tsx`**

```tsx
                <TableCell>
                  <TagPicker
                    txnId={t.id}
                    current={t.tag_id}
                    tags={tags}
                    onChange={() => load(page)}
                  />
                </TableCell>
```

becomes:

```tsx
                <TableCell>
                  <TagPicker
                    txnId={t.id}
                    current={t.tags}
                    tags={tags}
                    onChange={() => load(page)}
                  />
                </TableCell>
```

- [ ] **Step 2: Update `Dashboard.tsx`**

```tsx
                            <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                              <span>{formatDate(t.txn_date)}</span>
                              {t.tag && (
                                <>
                                  <span>·</span>
                                  <Badge variant="secondary" className="px-1.5 py-0">
                                    {t.tag}
                                  </Badge>
                                </>
                              )}
                            </div>
```

becomes:

```tsx
                            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                              <span>{formatDate(t.txn_date)}</span>
                              {t.tags.length > 0 && <span>·</span>}
                              {t.tags.map((name) => (
                                <Badge key={name} variant="secondary" className="px-1.5 py-0">
                                  {name}
                                </Badge>
                              ))}
                            </div>
```

- [ ] **Step 3: Update `Tags.tsx` copy**

```tsx
          <CardDescription>
            Each transaction can carry one tag. Click a tag to manage its
            auto-tag rules. Counts reflect current assignments.
          </CardDescription>
```

becomes:

```tsx
          <CardDescription>
            A transaction can carry any number of tags. Click a tag to
            manage its auto-tag rules. Counts reflect current assignments.
          </CardDescription>
```

- [ ] **Step 4: Verify the build**

Run: `cd frontend && npm run build`
Expected: succeeds with zero TypeScript errors — this is the point where every `tag_id`/`tag`/`t.tag` reference introduced by Tasks 6-7's rewrite has a matching counterpart, so the whole frontend compiles clean again.

- [ ] **Step 5: Manual smoke test (optional but recommended)**

Use the `run` skill to launch the app, open Transactions, and confirm: a transaction can be given two tags via the new picker, both show as badges, unchecking one leaves the other, and the Dashboard's "Recent activity" list shows both badges for a multi-tagged transaction.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TransactionsView.tsx frontend/src/pages/Dashboard.tsx frontend/src/pages/Tags.tsx
git commit -m "feat: wire multi-tag UI into Transactions, Dashboard, and Tags pages

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0162Uq1JZikMcTpw9YFLeCH5"
```
