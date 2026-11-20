# ============================================================================
#  report —— 把落库的 GSC 数据和实时的 Umami 数据算成页面/API 要的形状
#
#  品牌词判定、时间窗口、Umami 主机名过滤都在这里统一处理：
#  页面和 AI skill 看到的永远是同一份口径。
# ============================================================================
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta

from app.core.clients import Clients
from app.db import DB
from app.upstream.umami import window_ms

RANGES = {"7d": 7, "28d": 28, "8w": 56, "90d": 90}
_cache: dict[tuple, tuple[float, object]] = {}
CACHE_SECONDS = 60


async def cached(key: tuple, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    val = await fn()
    _cache[key] = (time.time(), val)
    return val


def brand_fn(site: dict):
    terms = [t.replace(" ", "").lower() for t in json.loads(site["brand_terms"]) if t.strip()]
    return lambda q: any(t in q.replace(" ", "").lower() for t in terms)


def gsc_end(db: DB, site_id: int) -> date:
    row = db.one("SELECT MAX(date) AS d FROM gsc_daily WHERE site_id = ?", site_id)
    return date.fromisoformat(row["d"]) if row and row["d"] else date.today()


def gsc_window(db: DB, site_id: int, days: int, offset: int = 0) -> tuple[str, str]:
    end = gsc_end(db, site_id) - timedelta(days=offset)
    return str(end - timedelta(days=days - 1)), str(end)


# ---------------------------------------------------------------- GSC 聚合
def search_split(db: DB, site: dict, lo: str, hi: str) -> dict:
    """总量来自 gsc_daily；品牌/非品牌来自明细；差值是 Google 匿名化的长尾。"""
    tot = db.one(
        "SELECT COALESCE(SUM(clicks),0) c, COALESCE(SUM(impressions),0) i FROM gsc_daily "
        "WHERE site_id = ? AND date BETWEEN ? AND ?",
        site["id"],
        lo,
        hi,
    )
    is_brand = brand_fn(site)
    b = {"clicks": 0, "impressions": 0}
    n = {"clicks": 0, "impressions": 0, "pos_w": 0.0}
    for r in db.all(
        "SELECT query, clicks, impressions, position FROM gsc_rows "
        "WHERE site_id = ? AND date BETWEEN ? AND ?",
        site["id"],
        lo,
        hi,
    ):
        t = b if is_brand(r["query"]) else n
        t["clicks"] += r["clicks"]
        t["impressions"] += r["impressions"]
        if t is n:
            n["pos_w"] += r["position"] * r["impressions"]
    return {
        "total": {"clicks": tot["c"], "impressions": tot["i"]},
        "brand": b,
        "nonbrand": {
            "clicks": n["clicks"],
            "impressions": n["impressions"],
            "position": round(n["pos_w"] / n["impressions"], 1) if n["impressions"] else None,
        },
        "anonymous": {
            "clicks": max(tot["c"] - b["clicks"] - n["clicks"], 0),
            "impressions": max(tot["i"] - b["impressions"] - n["impressions"], 0),
        },
    }


def weekly(db: DB, site: dict, weeks: int = 8) -> list[dict]:
    end = gsc_end(db, site["id"])
    monday = end - timedelta(days=end.weekday())
    out = []
    for k in range(weeks - 1, -1, -1):
        lo = monday - timedelta(weeks=k)
        hi = min(lo + timedelta(days=6), end)
        s = search_split(db, site, str(lo), str(hi))
        out.append({"week": str(lo), **s})
    return out


def queries(db: DB, site: dict, lo: str, hi: str, min_impr: int = 1) -> list[dict]:
    is_brand = brand_fn(site)
    rows = db.all(
        "SELECT query, page, SUM(clicks) c, SUM(impressions) i, "
        "SUM(position * impressions) / SUM(impressions) p FROM gsc_rows "
        "WHERE site_id = ? AND date BETWEEN ? AND ? GROUP BY query, page",
        site["id"],
        lo,
        hi,
    )
    agg: dict[str, dict] = {}
    for r in rows:
        q = agg.setdefault(
            r["query"],
            {
                "query": r["query"],
                "brand": is_brand(r["query"]),
                "clicks": 0,
                "impressions": 0,
                "pos_w": 0.0,
                "pages": [],
            },
        )
        q["clicks"] += r["c"]
        q["impressions"] += r["i"]
        q["pos_w"] += r["p"] * r["i"]
        q["pages"].append(
            {"page": r["page"], "impressions": r["i"], "clicks": r["c"], "position": round(r["p"], 1)}
        )
    out = []
    for q in agg.values():
        if q["impressions"] < min_impr:
            continue
        q["position"] = round(q.pop("pos_w") / q["impressions"], 1)
        q["pages"].sort(key=lambda p: -p["impressions"])
        out.append(q)
    return sorted(out, key=lambda q: -q["impressions"])


def pages(db: DB, site: dict, lo: str, hi: str) -> list[dict]:
    rows = db.all(
        "SELECT page, SUM(clicks) c, SUM(impressions) i, SUM(position * impressions) / SUM(impressions) p "
        "FROM gsc_rows WHERE site_id = ? AND date BETWEEN ? AND ? GROUP BY page ORDER BY i DESC",
        site["id"],
        lo,
        hi,
    )
    return [
        {"page": r["page"], "clicks": r["c"], "impressions": r["i"], "position": round(r["p"], 1)}
        for r in rows
    ]


# ---------------------------------------------------------------- Umami 实时
async def umami_overview(clients: Clients, site: dict, days: int) -> dict:
    """当前窗口 vs 上一个等长窗口；只统计站点主机名，测试/预览流量天然被排除。"""
    if not site["umami_website_id"]:
        return {}
    u, wid, host = clients.umami(), site["umami_website_id"], site["hostname"]

    async def fetch():
        cur, prev = window_ms(days), window_ms(days, days)
        s_cur, s_prev, ev_cur, ev_prev, hosts = await asyncio.gather(
            u.stats(wid, *cur, hostname=host),
            u.stats(wid, *prev, hostname=host),
            u.metrics(wid, "event", *cur, hostname=host),
            u.metrics(wid, "event", *prev, hostname=host),
            u.metrics(wid, "hostname", *cur),
        )
        strip = lambda s: {k: s.get(k) for k in ("pageviews", "visitors", "visits", "bounces")}  # noqa: E731
        return {
            "current": {**strip(s_cur), "events": {e["x"]: e["y"] for e in ev_cur}},
            "previous": {**strip(s_prev), "events": {e["x"]: e["y"] for e in ev_prev}},
            "other_hostnames": [{"hostname": h["x"], "pageviews": h["y"]} for h in hosts if h["x"] != host],
        }

    return await cached(("overview", site["id"], days), fetch)


async def umami_pages(clients: Clients, site: dict, days: int, paths: list[str]) -> dict[str, dict]:
    if not site["umami_website_id"]:
        return {}
    u, wid, host = clients.umami(), site["umami_website_id"], site["hostname"]
    win = window_ms(days)

    async def one(path: str) -> tuple[str, dict]:
        stats, ev = await asyncio.gather(
            u.stats(wid, *win, hostname=host, path=path),
            u.metrics(wid, "event", *win, hostname=host, path=path),
        )
        return path, {"visits": stats.get("visits", 0), "events": {e["x"]: e["y"] for e in ev}}

    async def fetch():
        return dict(await asyncio.gather(*(one(p) for p in paths)))

    return await cached(("pages", site["id"], days, tuple(paths)), fetch)


async def umami_events(clients: Clients, site: dict, days: int, name: str, props: list[str]) -> dict:
    u, wid, host = clients.umami(), site["umami_website_id"], site["hostname"]
    win = window_ms(days)
    vals = await asyncio.gather(*(u.event_values(wid, name, p, *win, hostname=host) for p in props))
    return {
        p: [{"value": v["value"], "count": v["total"]} for v in vs] for p, vs in zip(props, vals, strict=True)
    }
