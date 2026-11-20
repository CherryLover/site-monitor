# ============================================================================
#  site_routes —— 监控站点的增删改 + 手动触发同步
#
#  读接口人和 Token 都能用；增删改只认浏览器会话。
#  新增站点后立即在后台跑一轮完整同步，页面轮询 jobs 看进度。
# ============================================================================
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_principal, require_user
from app.core import setup, sync
from app.db import DB, now_iso

router = APIRouter(prefix="/api/v1")
KINDS = ("gsc", "inspections", "pagespeed")


class SiteIn(BaseModel):
    domain: str
    umami_website_id: str | None = None
    gsc_property: str | None = None
    hostname: str | None = None
    brand_terms: list[str] = []
    funnel_events: list[str] = []


class SitePatch(BaseModel):
    umami_website_id: str | None = None
    gsc_property: str | None = None
    hostname: str | None = None
    brand_terms: list[str] | None = None
    funnel_events: list[str] | None = None


def get_site(db: DB, domain: str) -> dict:
    site = db.one("SELECT * FROM sites WHERE domain = ?", setup.bare(domain))
    if not site:
        raise HTTPException(404, f"没有监控站点 {domain}")
    return site


def public(db: DB, site: dict, scheduler) -> dict:
    jobs = {
        k: db.one(
            "SELECT started_at, finished_at, ok, detail FROM jobs WHERE site_id = ? AND kind = ? "
            "ORDER BY id DESC LIMIT 1",
            site["id"],
            k,
        )
        for k in KINDS
    }
    for k, j in jobs.items():
        if j is not None:
            j["ok"] = None if j["ok"] is None else bool(j["ok"])
        jobs[k] = {
            **(j or {}),
            "running": scheduler.is_running(site["id"], k),
            "last_ok": sync.last_ok(db, site["id"], k),
        }
    number = db.one(
        "SELECT COUNT(*) + 1 AS n FROM sites WHERE position < ? OR (position = ? AND id < ?)",
        site["position"],
        site["position"],
        site["id"],
    )["n"]
    return {
        "number": number,
        "domain": site["domain"],
        "umami_website_id": site["umami_website_id"],
        "gsc_property": site["gsc_property"],
        "hostname": site["hostname"],
        "brand_terms": json.loads(site["brand_terms"]),
        "funnel_events": json.loads(site["funnel_events"]),
        "jobs": jobs,
    }


def kick(request: Request, site: dict, kinds=KINDS) -> None:
    sched = request.app.state.scheduler

    async def chain():
        for k in kinds:  # 顺序执行：pagespeed 依赖 gsc 选出的热门页面
            await sched.run(site, k)

    request.app.state.tasks.add(t := asyncio.create_task(chain()))
    t.add_done_callback(request.app.state.tasks.discard)


# ---------------------------------------------------------------- 读
@router.get("/sites", dependencies=[Depends(require_principal)])
async def list_sites(request: Request):
    db = request.app.state.db
    return [
        public(db, s, request.app.state.scheduler)
        for s in db.all("SELECT * FROM sites ORDER BY position, id")
    ]


@router.get("/sites/{domain}", dependencies=[Depends(require_principal)])
async def site_detail(domain: str, request: Request):
    db = request.app.state.db
    return public(db, get_site(db, domain), request.app.state.scheduler)


# ---------------------------------------------------------------- 写
@router.post("/sites", status_code=201, dependencies=[Depends(require_user)])
async def create_site(body: SiteIn, request: Request):
    db: DB = request.app.state.db
    domain = setup.bare(body.domain.strip())
    if not domain or "/" in domain:
        raise HTTPException(422, "域名格式不对，例如 example.com")
    if db.one("SELECT id FROM sites WHERE domain = ?", domain):
        raise HTTPException(409, f"{domain} 已在监控中")
    terms = [t.strip() for t in body.brand_terms if t.strip()] or setup.default_brand_terms(domain)
    db.run(
        "INSERT INTO sites (domain, umami_website_id, gsc_property, hostname, brand_terms, funnel_events, "
        "position, created_at) VALUES (?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM sites), ?)",
        domain,
        body.umami_website_id,
        body.gsc_property,
        (body.hostname or domain).strip(),
        json.dumps(terms, ensure_ascii=False),
        json.dumps(body.funnel_events, ensure_ascii=False),
        now_iso(),
    )
    site = get_site(db, domain)
    kick(request, site)
    return public(db, site, request.app.state.scheduler)


class OrderIn(BaseModel):
    domains: list[str]


@router.put("/sites/order", dependencies=[Depends(require_user)])
async def reorder_sites(body: OrderIn, request: Request):
    """拖拽排序：传入全部站点的新顺序，序号和页面上的顺序都以它为准。"""
    db: DB = request.app.state.db
    wanted = [setup.bare(d) for d in body.domains]
    existing = {r["domain"] for r in db.all("SELECT domain FROM sites")}
    if sorted(wanted) != sorted(existing):
        raise HTTPException(422, "domains 必须恰好包含全部站点，各一次")
    with db.conn() as c:
        c.executemany(
            "UPDATE sites SET position = ? WHERE domain = ?", [(i + 1, d) for i, d in enumerate(wanted)]
        )
    return await list_sites(request)


@router.patch("/sites/{domain}", dependencies=[Depends(require_user)])
async def patch_site(domain: str, body: SitePatch, request: Request):
    db: DB = request.app.state.db
    site = get_site(db, domain)
    changes = body.model_dump(exclude_none=True)
    for k in ("brand_terms", "funnel_events"):
        if k in changes:
            changes[k] = json.dumps([t.strip() for t in changes[k] if t.strip()], ensure_ascii=False)
    for k, v in changes.items():
        db.run(f"UPDATE sites SET {k} = ? WHERE id = ?", v, site["id"])  # k 来自固定的模型字段
    return public(db, get_site(db, domain), request.app.state.scheduler)


@router.delete("/sites/{domain}", status_code=204, dependencies=[Depends(require_user)])
async def delete_site(domain: str, request: Request):
    db: DB = request.app.state.db
    sid = get_site(db, domain)["id"]
    with db.conn() as c:
        for table in (
            "gsc_daily",
            "gsc_rows",
            "gsc_country",
            "inspections",
            "psi_runs",
            "changelog",
            "opp_state",
            "jobs",
        ):
            c.execute(f"DELETE FROM {table} WHERE site_id = ?", (sid,))
        c.execute("DELETE FROM sites WHERE id = ?", (sid,))


@router.post("/sites/{domain}/sync", status_code=202, dependencies=[Depends(require_principal)])
async def trigger_sync(domain: str, request: Request, kind: str = "all"):
    if kind != "all" and kind not in KINDS:
        raise HTTPException(422, f"kind 只能是 all / {' / '.join(KINDS)}")
    site = get_site(request.app.state.db, domain)
    kick(request, site, KINDS if kind == "all" else (kind,))
    return {"started": list(KINDS) if kind == "all" else [kind]}


@router.get("/sites/{domain}/jobs", dependencies=[Depends(require_principal)])
async def jobs(domain: str, request: Request, limit: int = 20):
    db = request.app.state.db
    rows = db.all(
        "SELECT kind, started_at, finished_at, ok, detail FROM jobs WHERE site_id = ? "
        "ORDER BY id DESC LIMIT ?",
        get_site(db, domain)["id"],
        min(limit, 200),
    )
    return [{**r, "ok": None if r["ok"] is None else bool(r["ok"])} for r in rows]
