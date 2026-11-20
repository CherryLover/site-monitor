# ============================================================================
#  data_routes —— 所有数据查询接口（浏览器会话或 Bearer Token）
#
#  页面上的每一块都只调这些接口；AI skill 也只调这些接口。
#  接口返回比页面展示更全的字段（例如每个搜索词的所有落地页），
#  页面只是挑着画。
# ============================================================================
from __future__ import annotations

import json
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.site_routes import get_site
from app.auth.deps import require_principal
from app.core import opportunities, report, watch
from app.db import now_iso

router = APIRouter(prefix="/api/v1/sites/{domain}", dependencies=[Depends(require_principal)])
RangeQ = Query("28d", pattern="^(7d|28d|8w|90d)$")


def ctx(request: Request, domain: str):
    st = request.app.state
    return st.db, st.clients, get_site(st.db, domain)


def pct(cur, prev):
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


# ---------------------------------------------------------------- 概览
@router.get("/summary")
async def summary(domain: str, request: Request, range: str = RangeQ):
    db, clients, site = ctx(request, domain)
    days = report.RANGES[range]
    cur = report.search_split(db, site, *report.gsc_window(db, site["id"], days))
    prev = report.search_split(db, site, *report.gsc_window(db, site["id"], days, days))
    um = await report.umami_overview(clients, site, days)
    return {
        "range": range,
        "gsc_through": str(report.gsc_end(db, site["id"])),
        "search": {
            "current": cur,
            "previous": prev,
            "change_pct": {
                "nonbrand_clicks": pct(cur["nonbrand"]["clicks"], prev["nonbrand"]["clicks"]),
                "nonbrand_impressions": pct(cur["nonbrand"]["impressions"], prev["nonbrand"]["impressions"]),
            },
        },
        "traffic": um,
    }


@router.get("/trend")
async def trend(domain: str, request: Request, weeks: int = Query(8, ge=2, le=52)):
    db, _, site = ctx(request, domain)
    return report.weekly(db, site, weeks)


# ---------------------------------------------------------------- 机会清单
class OppPatch(BaseModel):
    key: str
    status: str


@router.get("/opportunities")
async def opps(domain: str, request: Request):
    db, clients, site = ctx(request, domain)
    return await opportunities.build(db, clients, site)


@router.patch("/opportunities")
async def opp_status(domain: str, body: OppPatch, request: Request):
    if body.status not in ("open", "ignored"):
        raise HTTPException(422, "status 只能是 open / ignored；已处理请 POST /watches 建立观察")
    db, _, site = ctx(request, domain)
    opportunities.set_status(db, site["id"], body.key, body.status)
    return {"key": body.key, "status": body.status}


# ---------------------------------------------------------------- 观察（已处理 → 持续验证）
class WatchIn(BaseModel):
    key: str
    note: str
    fixed_at: str | None = None


@router.get("/watches")
async def watches(domain: str, request: Request):
    """每条观察：处理记录、修复前后同口径对比、当前判定、按天的判定时间线。"""
    db, clients, site = ctx(request, domain)
    firing = {o["key"] for o in await opportunities.build(db, clients, site)}
    return await watch.listing(db, clients, site, firing)


@router.post("/watches", status_code=201)
async def add_watch(domain: str, body: WatchIn, request: Request):
    """标记已处理。同一个 key 再次提交 = 再次处理：追加记录，观察期从新日期重算。"""
    db, clients, site = ctx(request, domain)
    day = body.fixed_at or str(date.today())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not body.note.strip():
        raise HTTPException(422, "需要 note（做了什么），fixed_at 格式为 YYYY-MM-DD")
    opps = {o["key"]: o for o in await opportunities.build(db, clients, site)}
    try:
        wid = watch.mark(db, site["id"], opps.get(body.key), body.key, body.note.strip(), day)
    except watch.WatchError as e:
        raise HTTPException(404, str(e)) from None
    w = db.one("SELECT * FROM watches WHERE id = ?", wid)
    return watch.public(db, await watch.evaluate(db, clients, site, w), set(opps))


# ---------------------------------------------------------------- 明细
@router.get("/queries")
async def queries(
    domain: str,
    request: Request,
    range: str = RangeQ,
    min_impressions: int = 1,
    brand: bool | None = None,
    limit: int = Query(200, le=5000),
):
    db, _, site = ctx(request, domain)
    rows = report.queries(db, site, *report.gsc_window(db, site["id"], report.RANGES[range]), min_impressions)
    if brand is not None:
        rows = [r for r in rows if r["brand"] == brand]
    return rows[:limit]


@router.get("/pages")
async def pages(domain: str, request: Request, range: str = RangeQ, limit: int = Query(10, le=50)):
    """落地页漏斗：GSC 展示/排名/点击 + Umami 访问与各漏斗事件，按 URL 路径拼接。"""
    db, clients, site = ctx(request, domain)
    days = report.RANGES[range]
    gsc = report.pages(db, site, *report.gsc_window(db, site["id"], days))[:limit]
    paths = [opportunities.path(p["page"]) for p in gsc]
    events = json.loads(site["funnel_events"])
    if not events and site["umami_website_id"]:
        ov = await report.umami_overview(clients, site, days)
        events = [k for k, _ in sorted(ov["current"]["events"].items(), key=lambda kv: -kv[1])][:4]
    um = await report.umami_pages(clients, site, days, paths)
    rows = []
    for p, path in zip(gsc, paths, strict=True):
        u = um.get(path, {"visits": None, "events": {}})
        rows.append(
            {
                "path": path,
                "impressions": p["impressions"],
                "position": p["position"],
                "clicks": p["clicks"],
                "visits": u["visits"],
                "events": {e: u["events"].get(e, 0) for e in events},
                "failures": sum(v for k, v in u["events"].items() if opportunities.FAIL_EVENT.search(k)),
            }
        )
    return {"events": events, "rows": rows}


@router.get("/index-status")
async def index_status(domain: str, request: Request):
    db, _, site = ctx(request, domain)
    rows = db.all(
        "SELECT url, coverage, last_crawl, google_canonical, checked_at FROM inspections "
        "WHERE site_id = ? ORDER BY url",
        site["id"],
    )
    ok = [r for r in rows if r["coverage"] in opportunities.NOT_INDEXED_OK]
    return {
        "total": len(rows),
        "indexed": len(ok),
        "checked_at": max((r["checked_at"] for r in rows), default=None),
        "problems": [r for r in rows if r["coverage"] not in opportunities.NOT_INDEXED_OK],
        "urls": rows,
    }


@router.get("/health")
async def health(domain: str, request: Request):
    db, clients, site = ctx(request, domain)
    psi = db.all("SELECT * FROM psi_runs WHERE site_id = ? ORDER BY url, strategy", site["id"])
    fails = {}
    if site["umami_website_id"]:
        ov = await report.umami_overview(clients, site, 28)
        fails = {k: v for k, v in ov["current"]["events"].items() if opportunities.FAIL_EVENT.search(k)}
    return {
        "pagespeed": [{k: v for k, v in r.items() if k != "site_id"} for r in psi],
        "fail_events_28d": fails,
    }


@router.get("/events")
async def events(domain: str, request: Request, name: str, props: str = "", range: str = RangeQ):
    _, clients, site = ctx(request, domain)
    if not site["umami_website_id"]:
        raise HTTPException(409, "这个站点没有关联 Umami")
    wanted = [p for p in props.split(",") if p] or ["error_code", "error_message", "file_type"]
    return await report.umami_events(clients, site, report.RANGES[range], name, wanted)


# ---------------------------------------------------------------- 变更日志
class ChangeIn(BaseModel):
    summary: str
    date: str | None = None
    page: str | None = None
    source: str = "manual"


@router.get("/changelog")
async def changelog(domain: str, request: Request, limit: int = Query(50, le=500)):
    db, _, site = ctx(request, domain)
    return db.all(
        "SELECT id, date, page, summary, source, created_at FROM changelog WHERE site_id = ? "
        "ORDER BY date DESC, id DESC LIMIT ?",
        site["id"],
        limit,
    )


@router.post("/changelog", status_code=201)
async def add_change(domain: str, body: ChangeIn, request: Request):
    db, _, site = ctx(request, domain)
    day = body.date or str(date.today())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not body.summary.strip():
        raise HTTPException(422, "需要 summary，date 格式为 YYYY-MM-DD")
    cid = db.run(
        "INSERT INTO changelog (site_id, date, page, summary, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        site["id"],
        day,
        body.page,
        body.summary.strip(),
        body.source[:20],
        now_iso(),
    )
    return {
        "id": cid,
        "date": day,
        "page": body.page,
        "summary": body.summary.strip(),
        "source": body.source[:20],
    }
