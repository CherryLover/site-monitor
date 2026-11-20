# ============================================================================
#  oidc —— Pocket ID 单点登录（唯一的登录方式）
#
#  授权码 + PKCE(S256)。回调地址固定为 SM_BASE_URL/auth/callback，
#  需要在 Pocket ID 的 OIDC 客户端里登记。会话存在签名 cookie 里。
#  谁能登录由 Pocket ID 决定（客户端的「允许的用户组」），这里不再维护名单。
# ============================================================================
from __future__ import annotations

from urllib.parse import urlparse

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import Config
from app.db import DB, now_iso

router = APIRouter()


def make_oauth(cfg: Config) -> OAuth:
    oauth = OAuth()
    oauth.register(
        "pocketid",
        client_id=cfg.oidc_client_id,
        client_secret=cfg.oidc_client_secret,
        server_metadata_url=f"{cfg.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
    )
    return oauth


@router.get("/auth/login")
async def login(request: Request):
    cfg: Config = request.app.state.cfg
    # 会话 cookie 绑定主机名：从别的地址进来时，先跳到 SM_BASE_URL，否则回调时 state 对不上
    if request.url.netloc != urlparse(cfg.base_url).netloc:
        return RedirectResponse(f"{cfg.base_url}/auth/login")
    return await request.app.state.oauth.pocketid.authorize_redirect(request, f"{cfg.base_url}/auth/callback")


@router.get("/auth/callback")
async def callback(request: Request):
    try:
        token = await request.app.state.oauth.pocketid.authorize_access_token(request)
    except OAuthError as e:
        return HTMLResponse(
            f"<p>登录失败：{e.error}。<a href='/auth/login'>重新登录</a></p>", status_code=400
        )
    info = token.get("userinfo") or {}
    user = {
        "sub": info["sub"],
        "email": info.get("email"),
        "name": info.get("name") or info.get("preferred_username") or info.get("email"),
    }
    db: DB = request.app.state.db
    db.run(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?) ON CONFLICT(sub) DO UPDATE SET email = excluded.email, "
        "name = excluded.name, last_login_at = excluded.last_login_at",
        user["sub"],
        user["email"],
        user["name"],
        now_iso(),
        now_iso(),
    )
    request.session.clear()
    request.session["user"] = user
    return RedirectResponse("/")


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/signed-out", status_code=303)
