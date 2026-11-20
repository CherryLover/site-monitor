# ============================================================================
#  secrets —— 业务凭证的加密存储 + 校验结果
#
#  凭证用 Fernet 加密后落库，密钥由 SM_SECRET_KEY 派生；数据库文件被拷走也读不出明文。
#  每个凭证的最近一次校验结果存在 checks 表，引导流程和设置页都读它。
# ============================================================================
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.db import DB, now_iso

# ---- 引导流程要求的凭证，顺序即步骤顺序 ----
UMAMI_URL = "umami.base_url"
UMAMI_KEY = "umami.api_key"
GOOGLE_SA = "google.service_account"
GOOGLE_KEY = "google.api_key"


class SecretStore:
    def __init__(self, db: DB, secret_key: str):
        self.db = db
        digest = hashlib.sha256(("site-monitor/secrets/" + secret_key).encode()).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))

    # ---------------------------------------------------------------- 读写
    def set(self, key: str, value: str) -> None:
        blob = self.fernet.encrypt(value.encode())
        self.db.run(
            "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            key,
            blob,
            now_iso(),
        )

    def get(self, key: str) -> str | None:
        row = self.db.one("SELECT value FROM secrets WHERE key = ?", key)
        if not row:
            return None
        try:
            return self.fernet.decrypt(row["value"]).decode()
        except InvalidToken:
            return None  # SM_SECRET_KEY 换过：视为未配置，引导用户重新填写

    def updated_at(self, key: str) -> str | None:
        row = self.db.one("SELECT updated_at FROM secrets WHERE key = ?", key)
        return row["updated_at"] if row else None

    # ---------------------------------------------------------------- 校验结果
    def record_check(self, name: str, ok: bool, detail: dict) -> None:
        self.db.run(
            "INSERT INTO checks (key, ok, detail, checked_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET ok = excluded.ok, detail = excluded.detail, "
            "checked_at = excluded.checked_at",
            name,
            int(ok),
            json.dumps(detail, ensure_ascii=False),
            now_iso(),
        )

    def check(self, name: str) -> dict | None:
        row = self.db.one("SELECT ok, detail, checked_at FROM checks WHERE key = ?", name)
        if not row:
            return None
        return {"ok": bool(row["ok"]), "detail": json.loads(row["detail"]), "checked_at": row["checked_at"]}


def mask(value: str | None) -> str | None:
    """展示用：只露头尾，永远不把完整凭证返回给前端。"""
    if not value:
        return None
    return value[:4] + "…" + value[-4:] if len(value) > 12 else "…"
