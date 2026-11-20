# ============================================================================
#  main —— 应用装配：配置 → 存储 → 上游客户端 → 调度器 → 路由
#
#  create_app() 是唯一入口；测试通过传入 Config 把所有上游指向本地假服务。
#  页面是一个无构建步骤的单页应用（app/web），只通过 /api/v1 取数据。
# ============================================================================
from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
IMMUTABLE = "public, max-age=31536000, immutable"


# ---------------------------------------------------------------- 静态资源版本化
#  CDN（Cloudflare）会给没有缓存头的 JS/CSS 补上 4 小时浏览器缓存，上线后用户刷新也看不到新界面。
#  做法：版本号 = web/ 下所有文件内容的哈希；HTML 每次都不缓存，并把引用改写成 /static/<版本>/…，
#  带版本的地址永久缓存（内容变了地址就变）。JS 之间的 import "/static/x.js" 由 import map
#  统一映射到带版本的地址，源码不用改。
def asset_version() -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in WEB.rglob("*") if p.is_file()):
        h.update(f.relative_to(WEB).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


class CachedStatic(StaticFiles):
    def __init__(self, cache_control: str, **kw):
        super().__init__(**kw)
        self.cache_control = cache_control

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = self.cache_control
        return resp


def html_page(name: str, ver: str) -> HTMLResponse:
    text = (WEB / name).read_text(encoding="utf-8")
    text = text.replace('="/static/', f'="/static/{ver}/')
    imports = f'<script type="importmap">{{"imports": {{"/static/": "/static/{ver}/"}}}}</script>'
    text = text.replace("</head>", f"  {imports}\n</head>", 1)
    return HTMLResponse(text, headers={"Cache-Control": "no-store"})


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
    ver = asset_version()
    app.state.asset_version = ver
    # 顺序要紧：带版本的挂载必须在 /static 之前，否则会被 /static 先匹配走
    app.mount(f"/static/{ver}", CachedStatic(IMMUTABLE, directory=WEB), name="assets")
    app.mount("/static", CachedStatic("no-cache", directory=WEB), name="static")

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
        return html_page("login.html", ver)

    async def page(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return html_page("index.html", ver)

    for p in PAGES:
        app.add_api_route(p, page, include_in_schema=False)
    app.add_api_route("/settings/{section}", page, include_in_schema=False)
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8700)
