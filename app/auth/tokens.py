# ============================================================================
#  tokens —— API Bearer Token
#
#  明文只在创建时返回一次；库里只存 SHA-256。前缀（smk_xxxx）用于在列表里认出是哪一个。
# ============================================================================
from __future__ import annotations

import hashlib
import secrets

from app.db import DB, now_iso

PREFIX = "smk_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(db: DB, name: str, created_by: str) -> dict:
    token = PREFIX + secrets.token_urlsafe(32)
    tid = db.run(
        "INSERT INTO api_tokens (name, prefix, token_hash, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        name,
        token[:10],
        _hash(token),
        created_by,
        now_iso(),
    )
    return {"id": tid, "name": name, "token": token, "prefix": token[:10]}


def list_all(db: DB) -> list[dict]:
    return db.all(
        "SELECT id, name, prefix, created_by, created_at, last_used_at, revoked_at FROM api_tokens "
        "ORDER BY revoked_at IS NOT NULL, id DESC"
    )


def revoke(db: DB, tid: int) -> bool:
    with db.conn() as c:
        cur = c.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now_iso(), tid)
        )
        return cur.rowcount > 0


def verify(db: DB, token: str) -> dict | None:
    if not token.startswith(PREFIX):
        return None
    row = db.one(
        "SELECT id, name, created_by FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
        _hash(token),
    )
    if row:
        db.run("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", now_iso(), row["id"])
    return row
