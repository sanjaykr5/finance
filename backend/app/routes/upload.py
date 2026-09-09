import hashlib
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..db import dedup_hash, get_conn
from ..parsers.base import PasswordRequiredError, UnsupportedFileError
from ..parsers.detect import PARSERS_BY_KEY
from .tags import apply_rules_to_transaction_ids

router = APIRouter()


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    password: str | None = Form(None),
):
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
            "parser": None,
            "parsed": 0,
            "inserted": 0,
            "skipped_duplicates": 0,
            "message": "This exact file was already uploaded.",
        }

    try:
        parsed_txns = parser.parse(content, password)
    except PasswordRequiredError as e:
        raise HTTPException(400, f"PDF password required or incorrect: {e}")
    except UnsupportedFileError as e:
        raise HTTPException(400, str(e))

    # Snapshot dedup hashes already committed to *this account's* table from
    # earlier uploads. A hash is only treated as a duplicate against this
    # fixed snapshot, never against a sibling row inserted moments earlier
    # in this same batch — this file already passed the sha256 check above,
    # so every row in it is new by definition, even if two of them happen to
    # look identical (e.g. two same-day, same-amount orders at the same
    # merchant, which HSBC in particular gives no reference number to tell
    # apart).
    existing_hashes = {
        r[0]
        for r in conn.execute(f'SELECT dedup_hash FROM "{table_name}"').fetchall()
    }

    inserted = 0
    skipped = 0
    inserted_ids: list[int] = []
    for t in parsed_txns:
        dedup = dedup_hash(t.txn_date, t.amount, t.description, t.account_last4, t.txn_ref)
        if dedup in existing_hashes:
            skipped += 1
            continue
        row = conn.execute(
            f"""
            INSERT INTO "{table_name}"
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
                file.filename,
                json.dumps(t.raw, default=str),
                dedup,
            ],
        ).fetchone()
        inserted += 1
        inserted_ids.append(row[0])

    conn.execute(
        """
        INSERT INTO uploaded_files
          (id, filename, sha256, account_id, parser_used, rows_parsed, rows_inserted)
        VALUES (nextval('seq_file'), ?, ?, ?, ?, ?, ?)
        """,
        [file.filename, sha, account_id, label, len(parsed_txns), inserted],
    )

    auto_tagged = apply_rules_to_transaction_ids(inserted_ids)

    return {
        "parser": label,
        "parsed": len(parsed_txns),
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "auto_tagged": auto_tagged,
    }
