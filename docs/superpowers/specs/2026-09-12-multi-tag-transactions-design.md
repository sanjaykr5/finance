# Multi-tag transactions

Status: approved
Date: 2026-09-12

## Problem

A transaction can currently carry exactly one tag: `transaction_tags` has
`transaction_id BIGINT PRIMARY KEY`, and `tag_id`/`tag_name`/`tag_color` are
singular fields threaded through the backend (`schemas.py`,
`routes/transactions.py`, `routes/dashboard.py`, `routes/tags.py`) and the
frontend (`api.ts`, `TagPicker.tsx`, `TransactionsView.tsx`, `Tags.tsx`,
`Dashboard.tsx`). Real transactions often belong under more than one
category (e.g. an Amazon purchase that's both "Subscriptions" and
"Recurring"), so a transaction needs to carry a *set* of tags instead.

## Decisions made during brainstorming

- **Tag update API**: full replace, not incremental add/remove. `PATCH
  /transactions/{id}` takes `tag_ids: list[int]` and replaces the whole set
  in one call — same pattern already used for `notes`/`audited`/`share_pct`.
  No new routes.
- **Rule auto-tagging**: a transaction can pick up several tags at once.
  Every rule that matches an untagged-for-that-tag transaction adds its tag;
  priority orders evaluation, it no longer means "only the winner applies."
  The "already covered" guard moves from *transaction has zero tags* to
  *transaction doesn't already have this rule's tag*, so re-running "Apply
  to existing" stays idempotent and additive rather than skipping anyone
  who already picked up one tag.
- **Tag filter in the transactions table**: stays single-select ("show
  transactions that have tag X" via `?tag=`). That's an "any of" filter on
  the list endpoint, unrelated to how many tags a given transaction can
  carry — no change needed there.
- **`by_tag` dashboard breakdown**: intentionally allowed to double-count. A
  transaction tagged both "food" and "date-night" contributes to both
  buckets, so `sum(by_tag.amount)` can exceed `total_spend`. That's correct
  for a per-tag breakdown, not a bug to fix.

## 1. Data model migration

`transaction_tags` keeps its two columns; only the primary key changes,
from single-column to composite. DuckDB can't `ALTER` a primary key in
place, so this follows the existing rename-recreate-copy precedent in
`db.py` (`_migrate_accounts_drop_nickname_and_last4`):

```python
def _migrate_transaction_tags_composite_pk(conn: duckdb.DuckDBPyConnection) -> None:
    """One-time upgrade: transaction_tags used to have `transaction_id` as
    its sole primary key (one tag per transaction). Widen it to a composite
    (transaction_id, tag_id) key so a transaction can carry multiple tags.
    DuckDB can't ALTER a primary key in place, so rebuild the table under
    the new shape and copy the data across — same pattern as
    _migrate_accounts_drop_nickname_and_last4.
    """
    pk_cols = conn.execute(
        """
        SELECT constraint_column_names FROM duckdb_constraints()
        WHERE table_name = 'transaction_tags' AND constraint_type = 'PRIMARY KEY'
        """
    ).fetchone()
    if pk_cols is None or len(pk_cols[0]) > 1:
        return  # already composite (or table doesn't exist yet)

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

Call it from `_bootstrap()` right after the `CREATE TABLE IF NOT EXISTS
transaction_tags` block, and update that block's DDL to the composite-PK
shape directly (so a fresh database gets it right first try; the migration
function only matters for existing databases). No data is lost — every
current single-tag assignment is exactly one row and carries over as-is.

## 2. Backend API

### `schemas.py`

```python
class TransactionUpdate(BaseModel):
    tag_ids: list[int] | None = None
    notes: str | None = None
    audited: bool | None = None
    share_pct: float | None = None
```

`tag_ids=None` still means "field not sent" under `exclude_unset=True`
(unchanged PATCH semantics); `tag_ids=[]` means "clear all tags."

### `routes/transactions.py`

`update_transaction`: replace the single-row delete/insert with a
full-set replace:

```python
if "tag_ids" in fields:
    tag_ids = fields.pop("tag_ids") or []
    conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", [txn_id])
    if tag_ids:
        conn.executemany(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
            [(txn_id, tid) for tid in tag_ids],
        )
```

`_build_filter`'s `tag` clause moves from a join column (`tt.tag_id = ?`)
to an existence check, since the join is going away:

```python
if tag:
    where.append(
        "EXISTS (SELECT 1 FROM transaction_tags tt "
        "WHERE tt.transaction_id = t.id AND tt.tag_id = ?)"
    )
    params.append(tag)
```

`list_transactions`: drop the row-multiplying `LEFT JOIN transaction_tags
... LEFT JOIN tags`. Instead select three parallel arrays via correlated
scalar subqueries, ordered by tag name so they line up:

```sql
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
       tm.notes, COALESCE(tm.audited, FALSE) AS audited,
       COALESCE(tm.share_pct, 100) AS share_pct
FROM all_transactions t
LEFT JOIN transaction_meta tm ON tm.transaction_id = t.id
```

In the route, after fetching each row, zip the three arrays into
`tags: [{"id": ..., "name": ..., "color": ...}]` and drop the three raw
array fields before appending to `out` (DuckDB returns `list(...)` columns
as Python lists already, `None`/empty when a transaction has no tags — treat
`None` the same as `[]`).

`count_transactions`: drop the `LEFT JOIN transaction_tags` entirely — it
was only there to support the join-based tag filter, which is now an
`EXISTS` clause in `_build_filter` and needs no join.

### `routes/dashboard.py`

`totals`: replace `JOIN transaction_tags tt ON tt.transaction_id = t.id`
with `WHERE EXISTS (SELECT 1 FROM transaction_tags tt WHERE
tt.transaction_id = t.id)` (folded into the existing `{where}` — becomes an
`AND EXISTS (...)` when `month` is also set, or `WHERE EXISTS (...)` when
it isn't) so a multi-tagged transaction's amount counts once, not per tag.

`by_tag`: unchanged — the join-and-group-by-tag is exactly what "double
counts" a multi-tagged transaction across its tags, which is the desired
behavior per the brainstorming decision above.

`recent`: needs the same one-row-per-transaction treatment as
`list_transactions`. The current `JOIN transaction_tags ... JOIN tags`
does two things at once — filters to only tagged transactions, and
produces one row per tag — so it splits into an `EXISTS` filter (like
`totals`) plus a correlated subquery for the aggregated names:

```sql
SELECT t.id, t.txn_date, t.description, t.amount, t.source,
       (SELECT list(tg.name ORDER BY tg.name) FROM transaction_tags tt
        JOIN tags tg ON tg.id = tt.tag_id WHERE tt.transaction_id = t.id) AS tags
FROM all_transactions t
WHERE EXISTS (SELECT 1 FROM transaction_tags tt WHERE tt.transaction_id = t.id)
{and_where}
ORDER BY t.txn_date DESC, t.id DESC
LIMIT 10
```

(`{and_where}` folds in the optional `month` filter as `AND ...` — same
adjustment as `totals`.) Rename the response field from `tag` (singular,
`str | None`) to `tags` (`list[str]`).

### `routes/tags.py`

`_matching_tag_id` → `_matching_tag_ids`, collecting every rule's tag
instead of returning on the first hit:

```python
def _matching_tag_ids(description: str, txn_account_id, rules) -> list[int]:
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

`apply_rules_to_transaction_ids`: the candidate-selection query no longer
excludes every already-tagged transaction — it needs to consider all `ids`
and let per-tag `INSERT ... ON CONFLICT DO NOTHING` (composite PK makes
this a no-op on a duplicate pair) handle idempotency:

```python
rows = conn.execute(
    f"SELECT t.id, t.description, t.account_id FROM all_transactions t "
    f"WHERE t.id IN ({placeholders})",
    ids,
).fetchall()
n = 0
for txn_id, desc, txn_account_id in rows:
    for matched_tag_id in _matching_tag_ids(desc or "", txn_account_id, rules):
        conn.execute(
            "INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?) "
            "ON CONFLICT DO NOTHING",
            [txn_id, matched_tag_id],
        )
        n += 1
```

Note `n` now counts tag *assignments* added, not transactions touched — a
transaction matching two rules counts as 2. That's consistent with the
existing "Tagged N ..." message in `TagRulesDialog.tsx` being a count of
work done, not a headcount of transactions; no wording change needed
there beyond what section 3 covers.

## 3. Frontend

### `api.ts`

```typescript
export type Transaction = {
  // ...unchanged fields...
  tags: Tag[]  // replaces tag_id / tag_name / tag_color
  notes: string | null
  audited: boolean
}

export type DashboardSummary = {
  total_spend: number
  total_credit: number
  net: number
  by_tag: { tag: string; amount: number }[]
  recent: {
    id: number
    txn_date: string
    description: string
    amount: number
    source: string
    tags: string[]  // replaces tag: string | null
  }[]
}
```

(`Tag` itself is unchanged — `{id, name, color, txn_count}` — `tags: Tag[]`
just reuses it, dropping `txn_count` isn't necessary since it's simply
ignored per-transaction.)

### `TagPicker.tsx` → multi-select

Replace the single `<Select>` with a popover-driven checklist. Trigger
shows the transaction's current tags as small colored badges (or
"Untagged" in muted text when empty); opening it lists every tag with a
checkbox, toggling one recomputes the full id array and PATCHes it:

```typescript
export default function TagPicker({
  txnId,
  current,       // Tag[] — was `number | null`
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
        <Button variant="outline" size="sm" className="h-8 min-w-[140px] justify-start" disabled={busy}>
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
      <PopoverContent className="w-48 p-1">
        {tags.map((t) => (
          <label key={t.id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent">
            <Checkbox checked={currentIds.has(t.id)} onCheckedChange={() => toggle(t.id)} />
            {t.name}
          </label>
        ))}
      </PopoverContent>
    </Popover>
  )
}
```

(Exact `Popover`/`Checkbox` imports follow whatever's already in
`frontend/src/components/ui/` — same family as the `Select` it replaces.)

### `TransactionsView.tsx`

The tag-filter dropdown (lines ~272-282) is unchanged — still a single
`<Select>` over `?tag=`. Only the table cell changes, from `current={t.tag_id}`
to `current={t.tags}`.

### `Dashboard.tsx`

The "recent activity" tag badge (lines ~202-208) switches from one
`{t.tag && <Badge>{t.tag}</Badge>}` to mapping `t.tags`:

```tsx
{t.tags.map((name) => (
  <Badge key={name} variant="secondary" className="px-1.5 py-0">
    {name}
  </Badge>
))}
```

### `Tags.tsx`

Copy update only: "Each transaction can carry one tag" → "A transaction
can carry any number of tags."

## 4. Testing

Backend (`backend/tests/`):

- `test_routes_transactions.py`: PATCH with `tag_ids` adding multiple tags,
  removing one while keeping another, clearing to `[]`; `list_transactions`
  returning the right `tags` array per transaction; `?tag=` filter matching
  transactions that have that tag among others.
- `test_routes_tags.py` / a new dashboard test: `apply_rules_to_transaction_ids`
  assigning multiple tags from multiple matching rules in one pass, and
  re-running it staying idempotent (no duplicate rows, no error) for a
  transaction that already has some but not all matching tags.
- Dashboard `totals` no longer double-counting a multi-tagged transaction's
  amount, while `by_tag` still shows it under each of its tags.

Frontend: no existing component test suite to extend (none of the `tag`-
related files have `.test.tsx` siblings today) — manual verification via
the `run` skill is the right bar here, unless you'd rather add one.
