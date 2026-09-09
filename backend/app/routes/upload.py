import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock

import duckdb
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..db import dedup_hash, get_conn, get_decrypted_password
from ..parsers.base import ParsedTransaction, PasswordRequiredError, UnsupportedFileError
from ..parsers.detect import PARSERS_BY_KEY
from .tags import apply_rules_to_transaction_ids

router = APIRouter()

_STAGING_TTL_SECONDS = 30 * 60


@dataclass
class StagedUpload:
    account_id: int
    table_name: str
    label: str
    filename: str
    sha256: str
    rows: list[ParsedTransaction]
    dedup_hashes: list[str]  # parallel to rows
    created_at: float = field(default_factory=time.monotonic)


_staging: dict[str, StagedUpload] = {}
_staging_lock = Lock()


def _sweep_staging() -> None:
    """Drop abandoned previews (parsed but never confirmed or cancelled)
    older than the TTL, so memory doesn't grow unbounded. Opportunistic —
    run at the top of every parse call rather than on a background thread,
    which is enough for a single-user local app.
    """
    cutoff = time.monotonic() - _STAGING_TTL_SECONDS
    with _staging_lock:
        expired = [t for t, s in _staging.items() if s.created_at < cutoff]
        for t in expired:
            del _staging[t]


def _row_to_preview(index: int, t: ParsedTransaction, duplicate: bool) -> dict:
    return {
        "index": index,
        "txn_date": str(t.txn_date),
        "description": t.description,
        "amount": str(t.amount),
        "txn_type": t.txn_type,
        "account_last4": t.account_last4,
        "instrument": t.instrument,
        "txn_ref": t.txn_ref,
        "txn_time": t.txn_time,
        "transaction_id": t.transaction_id,
        "duplicate": duplicate,
    }


class ConfirmUpload(BaseModel):
    upload_token: str
    selected_indices: list[int]


@router.post("/upload/parse")
async def parse_upload(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    password: str | None = Form(None),
):
    _sweep_staging()
    conn = get_conn()

    account = conn.execute(
        "SELECT table_name, parser, label FROM accounts WHERE id = ?", [account_id]
    ).fetchone()
    if not account:
        raise HTTPException(400, f"Unknown account_id {account_id}")
    table_name, parser_key, label = account
    parser = PARSERS_BY_KEY.get(parser_key) if parser_key else None
    if parser is None:
        raise HTTPException(
            400,
            f"No parser is configured yet for '{label}'. Share a sample "
            "statement to get one added, then try again.",
        )

    content = await file.read()
    sha = hashlib.sha256(content).hexdigest()

    existing = conn.execute(
        "SELECT id FROM uploaded_files WHERE sha256 = ?", [sha]
    ).fetchone()
    if existing:
        return {
            "status": "duplicate_file",
            "message": "This exact file was already uploaded.",
        }

    effective_password = password or get_decrypted_password(conn, account_id)

    try:
        parsed_txns = parser.parse(content, effective_password)
    except PasswordRequiredError as e:
        raise HTTPException(400, f"PDF password required or incorrect: {e}")
    except UnsupportedFileError as e:
        raise HTTPException(400, str(e))

    # Snapshot of hashes already committed to this account's table — same
    # semantics as the old single-shot upload: a hash is only a duplicate
    # against rows already in the DB, never against a sibling row in this
    # same batch (two genuinely distinct transactions can share a hash).
    existing_hashes = {
        r[0]
        for r in conn.execute(f'SELECT dedup_hash FROM "{table_name}"').fetchall()
    }

    dedup_hashes = []
    duplicates = []
    for t in parsed_txns:
        h = dedup_hash(t.txn_date, t.amount, t.description, t.account_last4, t.txn_ref)
        dedup_hashes.append(h)
        duplicates.append(h in existing_hashes)

    token = uuid.uuid4().hex
    with _staging_lock:
        _staging[token] = StagedUpload(
            account_id=account_id,
            table_name=table_name,
            label=label,
            filename=file.filename,
            sha256=sha,
            rows=parsed_txns,
            dedup_hashes=dedup_hashes,
        )

    return {
        "status": "ok",
        "upload_token": token,
        "parser": label,
        "rows": [
            _row_to_preview(i, t, d)
            for i, (t, d) in enumerate(zip(parsed_txns, duplicates))
        ],
    }


@router.post("/upload/confirm")
def confirm_upload(body: ConfirmUpload):
    with _staging_lock:
        staged = _staging.pop(body.upload_token, None)
    if staged is None:
        raise HTTPException(
            404, "Upload session expired or already confirmed — re-parse the file."
        )

    selected_indices = sorted(set(body.selected_indices))
    if any(i < 0 or i >= len(staged.rows) for i in selected_indices):
        raise HTTPException(400, "selected_indices contains an out-of-range index")

    conn = get_conn()
    inserted_ids: list[int] = []

    conn.execute("BEGIN TRANSACTION")

    already_committed = conn.execute(
        "SELECT id FROM uploaded_files WHERE sha256 = ?", [staged.sha256]
    ).fetchone()
    if already_committed:
        conn.execute("ROLLBACK")
        raise HTTPException(
            409,
            "This exact file was already uploaded — a different confirm for "
            "the same file landed first.",
        )

    try:
        for i in selected_indices:
            t = staged.rows[i]
            row = conn.execute(
                f"""
                INSERT INTO "{staged.table_name}"
                  (id, txn_date, description, amount, txn_type,
                   account_last4, instrument, txn_ref, txn_time, transaction_id,
                   source_file, raw, dedup_hash)
                VALUES (nextval('seq_txn'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                [
                    t.txn_date,
                    t.description,
                    str(t.amount),
                    t.txn_type,
                    t.account_last4,
                    t.instrument,
                    t.txn_ref,
                    t.txn_time,
                    t.transaction_id,
                    staged.filename,
                    json.dumps(t.raw, default=str),
                    staged.dedup_hashes[i],
                ],
            ).fetchone()
            inserted_ids.append(row[0])

        conn.execute(
            """
            INSERT INTO uploaded_files
              (id, filename, sha256, account_id, parser_used, rows_parsed, rows_inserted)
            VALUES (nextval('seq_file'), ?, ?, ?, ?, ?, ?)
            """,
            [
                staged.filename, staged.sha256, staged.account_id, staged.label,
                len(staged.rows), len(inserted_ids),
            ],
        )

        conn.execute("COMMIT")
    except duckdb.CatalogException:
        conn.execute("ROLLBACK")
        raise HTTPException(
            400,
            "The account this file was parsed against no longer exists — "
            "re-parse it against a different account.",
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise

    auto_tagged = apply_rules_to_transaction_ids(inserted_ids)

    return {
        "parser": staged.label,
        "parsed": len(staged.rows),
        "inserted": len(inserted_ids),
        "skipped_duplicates": len(staged.rows) - len(inserted_ids),
        "auto_tagged": auto_tagged,
    }


@router.delete("/upload/parse/{token}")
def cancel_upload(token: str):
    with _staging_lock:
        _staging.pop(token, None)
    return {"ok": True}
