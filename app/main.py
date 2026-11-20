# ============================================================================
#  main —— 应用装配：配置 → 存储 → 上游客户端 → 调度器 → 路由
#
#  create_app() 是唯一入口；测试通过传入 Config 把所有上游指向本地假服务。
#  页面是一个无构建步骤的单页应用（app/web），只通过 /api/v1 取数据。
# ============================================================================
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import data_routes, setup_routes, site_routes
from app.auth import oidc
from app.config import Config
from app.core.clients import Clients, NotConfigured
from app.core.scheduler import Scheduler
from app.core.secrets import SecretStore
from app.db import DB
from app.upstream import UpstreamError

WEB = Path(__file__).parent / "web"
PAGES = ("/", "/setup", "/settings")


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.http = httpx.AsyncClient(headers={"User-Agent": "site-monitor/1.0"})
        app.state.clients = Clients(cfg, app.state.secrets, app.state.http)
        app.state.scheduler = Scheduler(app.state.db, app.state.clients)
        app.state.tasks = set()
        if cfg.scheduler:
            app.state.scheduler.start()
        yield
        await app.state.scheduler.stop()
        await app.state.http.aclose()

    app = FastAPI(
        title="Site Monitor",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.cfg = cfg
    app.state.db = DB(cfg.db_path)
    app.state.secrets = SecretStore(app.state.db, cfg.secret_key)
    app.state.oauth = oidc.make_oauth(cfg)

    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key,
        session_cookie="sm_session",
        max_age=14 * 86400,
        same_site="lax",
        https_only=cfg.secure_cookies,
    )
    for r in (oidc.router, setup_routes.router, site_routes.router, data_routes.router):
        app.include_router(r)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ------------------------------------------------------------ 错误翻译
    @app.exception_handler(NotConfigured)
    async def not_configured(_: Request, e: NotConfigured):
        return JSONResponse({"error": "setup_incomplete", "detail": str(e)}, status_code=409)

    @app.exception_handler(UpstreamError)
    async def upstream(_: Request, e: UpstreamError):
        return JSONResponse({"error": "upstream", "source": e.source, "detail": e.message}, status_code=502)

    # ------------------------------------------------------------ 页面
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True}

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        # 未登录只展示登录页，由用户主动点击按钮再跳转到 Pocket ID
        if request.session.get("user"):
            return RedirectResponse("/")
        return FileResponse(WEB / "login.html", headers={"Cache-Control": "no-store"})

    async def page(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    for p in PAGES:
        app.add_api_route(p, page, include_in_schema=False)
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8700)
