# ============================================================================
#  sync —— 把 Google 数据落到本地（Umami 不落库，查询时实时取）
#
#    gsc          每天：重拉最近 5 天（GSC 数据会在 2–3 天内修正）；首次拉 16 个月
#    inspections  每周：sitemap 里每个 URL 的收录状态
#    pagespeed    每周：首页 + 展示最多的 3 个页面 × 手机/桌面
#
#  每次运行记录到 jobs 表，失败不抛出，页面和 API 通过 jobs 看到原因。
# ============================================================================
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

from app.core.clients import Clients, NotConfigured
from app.db import DB, now_iso
from app.upstream import UpstreamError

FIRST_SYNC_DAYS = 480
REFRESH_DAYS = 5
MAX_INSPECT_URLS = 300
RETRY_DELAYS = (3, 10)  # 第 1、2 次重试前的等待秒数
BROWSER_UA = "Mozilla/5.0 (compatible; site-monitor/1.0; +https://github.com/CherryLover/site-monitor)"


# ---------------------------------------------------------------- 作业外壳
async def run_job(db: DB, clients: Clients, kind: str, site: dict) -> dict:
    job_id = db.run(
        "INSERT INTO jobs (site_id, kind, started_at) VALUES (?, ?, ?)", site["id"], kind, now_iso()
    )
    try:
        detail = await JOBS[kind](db, clients, site)
        ok = True
    except NotConfigured as e:
        db.run("UPDATE jobs SET finished_at = ?, ok = 0, detail = ? WHERE id = ?", now_iso(), str(e), job_id)
        raise
    except UpstreamError as e:
        detail, ok = e.message, False
    except Exception as e:  # noqa: BLE001 —— 作业失败要记录下来，而不是让调度器崩掉
        detail, ok = f"{e.__class__.__name__}: {e}", False
    db.run(
        "UPDATE jobs SET finished_at = ?, ok = ?, detail = ? WHERE id = ?",
        now_iso(),
        int(ok),
        str(detail),
        job_id,
    )
    return {"kind": kind, "ok": ok, "detail": detail}


def last_ok(db: DB, site_id: int, kind: str) -> str | None:
    row = db.one(
        "SELECT MAX(finished_at) AS t FROM jobs WHERE site_id = ? AND kind = ? AND ok = 1", site_id, kind
    )
    return row["t"] if row else None


# ---------------------------------------------------------------- Search Analytics
async def sync_gsc(db: DB, clients: Clients, site: dict) -> str:
    if not site["gsc_property"]:
        return "未关联 Search Console 属性，跳过"
    gsc = clients.gsc()
    days = REFRESH_DAYS if last_ok(db, site["id"], "gsc") else FIRST_SYNC_DAYS
    end = date.today()
    start = end - timedelta(days=days)
    base = {"startDate": str(start), "endDate": str(end), "rowLimit": 25000, "dataState": "all"}

    daily = await gsc.query(site["gsc_property"], {**base, "dimensions": ["date"]})
    rows = await _paged(gsc, site["gsc_property"], {**base, "dimensions": ["date", "query", "page"]})

    sid, lo = site["id"], str(start)
    with db.conn() as c:
        c.execute("DELETE FROM gsc_daily WHERE site_id = ? AND date >= ?", (sid, lo))
        c.execute("DELETE FROM gsc_rows WHERE site_id = ? AND date >= ?", (sid, lo))
        c.executemany(
            "INSERT INTO gsc_daily VALUES (?, ?, ?, ?, ?)",
            [(sid, r["keys"][0], r["clicks"], r["impressions"], r["position"]) for r in daily],
        )
        c.executemany(
            "INSERT INTO gsc_rows VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(sid, *r["keys"], r["clicks"], r["impressions"], r["position"]) for r in rows],
        )
    return f"{start} ~ {end}：{len(daily)} 天，{len(rows)} 行明细"


async def _paged(gsc, prop: str, body: dict) -> list[dict]:
    out, start = [], 0
    while True:
        page = await gsc.query(prop, {**body, "startRow": start})
        out += page
        if len(page) < body["rowLimit"]:
            return out
        start += len(page)


# ---------------------------------------------------------------- 收录巡检
async def sitemap_urls(clients: Clients, site: dict) -> list[str]:
    maps = await clients.gsc().sitemaps(site["gsc_property"])
    paths = [m["path"] for m in maps] or [f"https://{site['domain']}/sitemap.xml"]
    urls: list[str] = []
    for p in paths:
        r = await clients.http.get(p, headers={"User-Agent": BROWSER_UA}, timeout=30, follow_redirects=True)
        if r.is_success:
            urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
    return list(dict.fromkeys(urls))[:MAX_INSPECT_URLS]


async def retrying(fn, *args):
    """网络错误 / 429 / 5xx 重试；凭证类 4xx 立即抛出（重试也没用）。"""
    for delay in (*RETRY_DELAYS, None):
        try:
            return await fn(*args)
        except UpstreamError as e:
            transient = e.status is None or e.status == 429 or e.status >= 500
            if not transient or delay is None:
                raise
            await asyncio.sleep(delay)


async def sync_inspections(db: DB, clients: Clients, site: dict) -> str:
    if not site["gsc_property"]:
        return "未关联 Search Console 属性，跳过"
    gsc, urls = clients.gsc(), await sitemap_urls(clients, site)
    failed: list[str] = []
    for url in urls:
        try:
            r = await retrying(gsc.inspect, site["gsc_property"], url)
        except UpstreamError as e:
            if e.status in (401, 403):
                raise
            failed.append(f"{url}（{e.message}）")
            continue  # 单个 URL 失败不影响其余 URL，旧记录保留
        db.run(
            "INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(site_id, url) DO UPDATE SET "
            "coverage = excluded.coverage, last_crawl = excluded.last_crawl, "
            "google_canonical = excluded.google_canonical, checked_at = excluded.checked_at",
            site["id"],
            url,
            r.get("coverageState", "未知"),
            r.get("lastCrawlTime"),
            r.get("googleCanonical"),
            now_iso(),
        )
    if urls and len(failed) == len(urls):
        raise UpstreamError("gsc", f"全部 {len(urls)} 个 URL 巡检失败，例如 {failed[0]}")
    tail = f"，{len(failed)} 个失败：{'；'.join(failed[:3])}" if failed else ""
    return f"巡检 {len(urls) - len(failed)} 个 URL{tail}"


# ---------------------------------------------------------------- PageSpeed
async def sync_pagespeed(db: DB, clients: Clients, site: dict) -> str:
    ps = clients.pagespeed()
    home = f"https://{site['domain']}/"
    top = db.all(
        "SELECT page, SUM(impressions) AS i FROM gsc_rows WHERE site_id = ? AND date >= ? "
        "GROUP BY page ORDER BY i DESC LIMIT 4",
        site["id"],
        str(date.today() - timedelta(days=28)),
    )
    urls = list(dict.fromkeys([home] + [r["page"] for r in top]))[:4]
    for url in urls:
        for strategy in ("mobile", "desktop"):
            m = await retrying(ps.run, url, strategy)
            db.run(
                "INSERT OR REPLACE INTO psi_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                site["id"],
                url,
                strategy,
                now_iso(),
                m["performance"],
                m["seo"],
                m["accessibility"],
                m["best_practices"],
                m["lcp_ms"],
                m["cls"],
                m["tbt_ms"],
            )
    return f"测试 {len(urls)} 个页面 × 2 种设备"


JOBS = {"gsc": sync_gsc, "inspections": sync_inspections, "pagespeed": sync_pagespeed}
