# ============================================================================
#  deps —— 请求级身份判定
#
#    require_user       只认浏览器会话：设置、凭证、Token 管理（人做的事）
#    require_principal  会话或 Bearer Token 都行：所有数据接口（人和 AI 都能读）
# ============================================================================
from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth import tokens


def require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "请先通过 Pocket ID 登录")
    return {"kind": "user", **user}


def require_principal(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        row = tokens.verify(request.app.state.db, auth[7:].strip())
        if not row:
            raise HTTPException(401, "Token 无效或已吊销")
        return {"kind": "token", "token_id": row["id"], "name": row["name"]}
    return require_user(request)
