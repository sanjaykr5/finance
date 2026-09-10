# Expense Tracker

Local webapp to ingest credit-card / PhonePe / bank-statement files, store the
parsed transactions in a DuckDB file, browse and tag them, and see month-level
summaries.

- **Backend:** Python 3.11+ · FastAPI · DuckDB · pdfplumber · pandas
- **Frontend:** React + Vite + TypeScript + Tailwind + Recharts
- **DB:** single file `backend/expense.duckdb` (created on first boot)

## First-time setup

```bash
# Backend
cd backend
python3.13 -m venv .venv          # any 3.11+ works
.venv/bin/pip install -r requirements.txt

# Frontend
cd ../frontend
npm install
```

## Running (two terminals)

```bash
# Terminal 1 — API on :8000
cd backend && ./run.sh
```

```bash
# Terminal 2 — UI on :5173
cd frontend && npm run dev
```

Open <http://localhost:5173>.

The Vite dev server proxies `/api/*` to the FastAPI server, so the only URL
you need to use is `localhost:5173`.

## What the four tabs do

| Tab | Purpose |
| --- | --- |
| **Dashboard** | Month picker · spend / credit / net cards · spend-by-tag pie · recent 10 |
| **Upload** | Drag-drop a PDF or CSV; enter password if the PDF is locked. Shows parser used, rows parsed/inserted/duplicates, and an auto-tag count |
| **Transactions** | Filter by date / tag / search; inline tag picker on each row |
| **Tags** | Create tags, define `contains` / `regex` rules per tag, "Apply to existing" backfills tags on currently-untagged transactions |

## Supported inputs

- **HDFC credit-card PDF** — picked when filename or first page contains "hdfc"
- **HSBC credit-card PDF** — picked when filename or first page contains "hsbc"
- **PhonePe transaction PDF** — picked when filename or first page contains "phonepe" / "phone pe"
- **CSV bank statements** — generic header detection (date / description / debit / credit / amount / account)

Re-uploading the exact same file is a no-op (sha256 of the file is recorded).
Duplicate transactions inside different files are also de-duped via
`sha256(date|amount|description|source|account_last4)`.

> The bank PDF parsers ship with reasonable regex defaults. After uploading a
> real statement, if rows get missed share the PDF and the per-bank parser in
> `backend/app/parsers/{hdfc,hsbc,phonepe}.py` can be tuned — each is ~50 lines.

## Sign convention

`amount` is negative for spend / debit, positive for credit / refund / income.

## Project layout

```
finance/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── db.py
│   │   ├── schemas.py
│   │   ├── parsers/{base,detect,hdfc,hsbc,phonepe,csv_statement}.py
│   │   └── routes/{upload,transactions,tags,dashboard}.py
│   ├── requirements.txt
│   └── run.sh
└── frontend/
    ├── package.json · vite.config.ts · tailwind.config.js · tsconfig.json
    └── src/
        ├── main.tsx · App.tsx · api.ts · index.css
        ├── components/{Sidebar,TagPicker}.tsx
        └── pages/{Dashboard,Upload,Transactions,Tags}.tsx
```

## API quick reference

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/upload` | multipart `file`, optional `password` |
| `GET` | `/api/transactions` | query: `from`, `to`, `tag`, `q`, `limit`, `offset` |
| `PATCH` | `/api/transactions/{id}` | body `{tag_id: number \| null}` |
| `GET` | `/api/tags` | with `txn_count` per tag |
| `POST` | `/api/tags` | `{name, color?}` |
| `DELETE` | `/api/tags/{id}` | also drops its rules / assignments |
| `GET` | `/api/rules` | |
| `POST` | `/api/rules` | `{tag_id, match_type:'contains'\|'regex', pattern, priority?}` |
| `DELETE` | `/api/rules/{id}` | |
| `POST` | `/api/rules/apply` | re-runs all rules over **untagged** transactions |
| `GET` | `/api/dashboard/summary?month=YYYY-MM` | totals, by-tag, recent |
