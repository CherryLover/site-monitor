# ============================================================================
#  setup_routes —— 引导流程、凭证设置、API Token 管理（只接受浏览器会话）
#
#  凭证接口统一语义：先真实校验，通过才保存；无论成败都返回结构化 detail
#  供页面展示。凭证从不以明文返回，只给掩码。
# ============================================================================
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import tokens
from app.auth.deps import require_user
from app.core import secrets as S
from app.core import setup
from app.core.secrets import mask
from app.upstream.google import parse_service_account

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user)])


class UmamiIn(BaseModel):
    base_url: str
    api_key: str = ""


class ServiceAccountIn(BaseModel):
    json_text: str


class ApiKeyIn(BaseModel):
    api_key: str


class TokenIn(BaseModel):
    name: str


def _save(request: Request, step: str, ok: bool, detail: dict, values: dict[str, str]) -> dict:
    store: S.SecretStore = request.app.state.secrets
    if ok:
        for k, v in values.items():
            store.set(k, v)
        store.record_check(step, ok, detail)
    return {"ok": ok, "detail": detail}


# ---------------------------------------------------------------- 身份与进度
@router.get("/me")
async def me(request: Request, user: dict = Depends(require_user)):
    return {k: user.get(k) for k in ("sub", "email", "name")}


@router.get("/setup/status")
async def status(request: Request):
    return setup.status(request.app.state.db, request.app.state.secrets)


@router.get("/setup/credentials")
async def credentials(request: Request):
    g = request.app.state.secrets.get
    sa = g(S.GOOGLE_SA)
    sa_info = None
    if sa:
        parsed = json.loads(sa)
        sa_info = {"email": parsed.get("client_email"), "project_id": parsed.get("project_id")}
    return {
        "umami": {"base_url": g(S.UMAMI_URL), "api_key": mask(g(S.UMAMI_KEY))},
        "google_sa": sa_info,
        "google_key": {"api_key": mask(g(S.GOOGLE_KEY))},
    }


# ---------------------------------------------------------------- 三类凭证
@router.put("/setup/umami")
async def put_umami(body: UmamiIn, request: Request):
    key = body.api_key.strip() or request.app.state.secrets.get(S.UMAMI_KEY) or ""
    url = body.base_url.strip().rstrip("/")
    ok, detail = await setup.verify_umami(url, key, request.app.state.http)
    return _save(request, "umami", ok, detail, {S.UMAMI_URL: url, S.UMAMI_KEY: key})


@router.put("/setup/google_sa")
async def put_sa(body: ServiceAccountIn, request: Request):
    ok, detail = await setup.verify_service_account(
        body.json_text, request.app.state.cfg, request.app.state.http
    )
    if ok:  # 存规整后的 JSON，去掉粘贴带来的空白差异
        body.json_text = json.dumps(parse_service_account(body.json_text))
    return _save(request, "google_sa", ok, detail, {S.GOOGLE_SA: body.json_text})


@router.put("/setup/google_key")
async def put_key(body: ApiKeyIn, request: Request):
    key = body.api_key.strip()
    ok, detail = await setup.verify_api_key(key, request.app.state.cfg, request.app.state.http)
    return _save(request, "google_key", ok, detail, {S.GOOGLE_KEY: key})


@router.post("/setup/{step}/recheck")
async def recheck(step: str, request: Request):
    if step not in ("umami", "google_sa", "google_key"):
        raise HTTPException(404, "未知步骤")
    ok, detail = await setup.recheck(step, request.app.state.clients)
    return {"ok": ok, "detail": detail}


@router.get("/setup/site-candidates")
async def site_candidates(request: Request):
    return await setup.candidates(request.app.state.clients, request.app.state.db)


# ---------------------------------------------------------------- API Token
@router.get("/tokens")
async def list_tokens(request: Request):
    return tokens.list_all(request.app.state.db)


@router.post("/tokens", status_code=201)
async def create_token(body: TokenIn, request: Request, user: dict = Depends(require_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请给 Token 起个名字，方便日后识别")
    return tokens.create(request.app.state.db, name[:60], user.get("email") or user["sub"])


@router.delete("/tokens/{tid}", status_code=204)
async def revoke_token(tid: int, request: Request):
    if not tokens.revoke(request.app.state.db, tid):
        raise HTTPException(404, "Token 不存在或已吊销")
