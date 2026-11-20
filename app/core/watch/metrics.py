# ============================================================================
#  watch.metrics —— 每类机会怎么量、怎么判
#
#  一条观察 = 修复前一段（基线）和修复后一段（观察期）用同一把尺子量两次。
#  尺子按机会类型选：排名看加权平均排名，抢词看头部页面的展示占比，
#  故障看失败率，收录看逐 URL 状态……度量只读数据，不写库。
#
#  判定统一走 judge()：
#    样本够 + 达标 + 观察满 min_days         → verified   已验证
#    观察不满 patience 天或样本不够           → improving（已有改善）/ waiting
#    观察够久仍未达标                         → improving / no_change
#  「复发」不在这里判：它依赖历史（曾经验证过），由上层根据 verified_at 推出。
# ============================================================================
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlparse

from app.core import markets, report
from app.core.clients import NotConfigured
from app.upstream import UpstreamError

WAITING, IMPROVING, VERIFIED, NO_CHANGE, REGRESSED = (
    "waiting",
    "improving",
    "verified",
    "no_change",
    "regressed",
)
LABELS = {
    WAITING: "等待数据",
    IMPROVING: "见效中",
    VERIFIED: "已验证",
    NO_CHANGE: "未见改善",
    REGRESSED: "复发",
}
BASELINE_DAYS = 28


@dataclass
class Ctx:
    db: object
    clients: object
    site: dict


@dataclass
class Result:
    verdict: str
    summary: str
    before: dict
    after: dict
    days: int


def judge(*, goal: bool, improved: bool, enough: bool, days: int, min_days: int, patience: int) -> str:
    if enough and goal and days >= min_days:
        return VERIFIED
    if not enough or days < patience:
        return IMPROVING if enough and (improved or goal) else WAITING
    return IMPROVING if improved else NO_CHANGE


def windows(fixed: date, end: date) -> tuple[str, str, str, str, int]:
    """基线 = 修复前 28 天；观察期 = 修复当天到数据最新日。"""
    b_hi = fixed - timedelta(days=1)
    b_lo = b_hi - timedelta(days=BASELINE_DAYS - 1)
    days = max((end - fixed).days + 1, 0)
    return str(b_lo), str(b_hi), str(fixed), str(end), days


def path(url: str) -> str:
    return urlparse(url).path or "/"


# ---------------------------------------------------------------- GSC 度量
def query_stats(db, sid: int, query: str, lo: str, hi: str) -> dict:
    rows = db.all(
        "SELECT page, SUM(impressions) i, SUM(clicks) c, SUM(position * impressions) pw FROM gsc_rows "
        "WHERE site_id = ? AND query = ? AND date BETWEEN ? AND ? GROUP BY page ORDER BY i DESC",
        sid,
        query,
        lo,
        hi,
    )
    return gsc_summary(rows)


def page_stats(db, sid: int, page: str, lo: str, hi: str) -> dict:
    rows = db.all(
        "SELECT page, SUM(impressions) i, SUM(clicks) c, SUM(position * impressions) pw FROM gsc_rows "
        "WHERE site_id = ? AND page = ? AND date BETWEEN ? AND ? GROUP BY page",
        sid,
        page,
        lo,
        hi,
    )
    return gsc_summary(rows)


def gsc_summary(rows: list[dict]) -> dict:
    impr = sum(r["i"] for r in rows)
    pos = round(sum(r["pw"] for r in rows) / impr, 1) if impr else None
    top = rows[0] if rows else None
    return {
        "impressions": impr,
        "clicks": sum(r["c"] for r in rows),
        "position": pos,
        "top_page": path(top["page"]) if top else None,
        "top_share": round(top["i"] / impr, 2) if impr else None,
        "pages": len(rows),
    }


def position_rule(goal_pos: float, step: float, min_impr: int, min_days=14, patience=28):
    """排名类：越小越好。达标 = 平均排名进入 goal_pos；改善 = 至少前进 step 名。"""

    def rule(b: dict, a: dict, days: int) -> tuple[str, str]:
        enough = a["impressions"] >= min_impr
        bp, ap = b["position"], a["position"]
        improved = bool(bp and ap and bp - ap >= step)
        verdict = judge(
            goal=bool(ap and ap <= goal_pos),
            improved=improved,
            enough=enough,
            days=days,
            min_days=min_days,
            patience=patience,
        )
        text = f"排名 {bp or '—'} → {ap or '—'}（目标 ≤ {goal_pos:g}）· 观察期 {a['impressions']} 展示 · {a['clicks']} 点击"
        return verdict, text

    return rule


def share_rule(b: dict, a: dict, days: int) -> tuple[str, str]:
    """抢词：头部页面拿到 ≥ 80% 展示即视为收敛。"""
    bs, as_ = b["top_share"] or 0, a["top_share"] or 0
    verdict = judge(
        goal=as_ >= 0.8,
        improved=as_ - bs >= 0.15,
        enough=a["impressions"] >= 10,
        days=days,
        min_days=14,
        patience=28,
    )
    text = (
        f"头部页面占比 {round(bs * 100)}% → {round(as_ * 100)}%（{a['top_page'] or '—'}，目标 ≥ 80%）"
        f" · 观察期 {a['pages']} 个页面分走 {a['impressions']} 展示"
    )
    return verdict, text


async def gsc_measure(ctx: Ctx, w: dict) -> Result:
    kind, _, rest = w["key"].partition(":")
    db, sid = ctx.db, ctx.site["id"]
    b_lo, b_hi, a_lo, a_hi, days = windows(date.fromisoformat(w["fixed_at"]), report.gsc_end(db, sid))
    if kind == "rank" and rest.startswith("p:"):
        stats, rule = (lambda lo, hi: page_stats(db, sid, rest[2:], lo, hi)), position_rule(10, 2, 20)
    elif kind == "rank":
        stats, rule = (lambda lo, hi: query_stats(db, sid, rest[2:], lo, hi)), position_rule(3, 1, 10)
    elif kind == "gap":
        stats, rule = (lambda lo, hi: query_stats(db, sid, rest, lo, hi)), position_rule(30, 5, 5)
    else:  # cannibal
        stats, rule = (lambda lo, hi: query_stats(db, sid, rest, lo, hi)), share_rule
    before = stats(b_lo, b_hi)
    if not days:
        text = f"基线：{baseline_text(before)}；修复后的搜索数据还没到（GSC 延迟约 2–3 天）"
        return Result(WAITING, text, before, gsc_summary([]), 0)
    after = stats(a_lo, a_hi)
    verdict, text = rule(before, after, days)
    return Result(verdict, text, before, after, days)


def baseline_text(b: dict) -> str:
    if not b["impressions"]:
        return "修复前 28 天没有展示"
    return f"排名 {b['position']} · {b['impressions']} 展示 · 头部页面 {b['top_page']} 占 {round(b['top_share'] * 100)}%"


async def market_measure(ctx: Ctx, w: dict) -> Result:
    """市场缺口：新语言目录下的页面开始拿到展示，才算真正接住了需求。"""
    lang = w["key"].split(":", 1)[1]
    db, sid = ctx.db, ctx.site["id"]
    b_lo, b_hi, a_lo, a_hi, days = windows(date.fromisoformat(w["fixed_at"]), report.gsc_end(db, sid))

    def measure(lo: str, hi: str) -> dict:
        row = db.one(
            "SELECT COALESCE(SUM(impressions),0) i, COALESCE(SUM(clicks),0) c FROM gsc_rows "
            "WHERE site_id = ? AND date BETWEEN ? AND ? AND page LIKE ?",
            sid,
            lo,
            hi,
            f"%/{lang}/%",
        )
        demand = markets.language_demand(db, sid, lo, hi, report.brand_fn(ctx.site)).get(lang, {})
        return {
            "lang_page_impressions": row["i"],
            "lang_page_clicks": row["c"],
            "demand": demand.get("impressions", 0),
        }

    before, after = measure(b_lo, b_hi), measure(a_lo, a_hi)
    verdict = judge(
        goal=after["lang_page_impressions"] >= 20,
        improved=after["lang_page_impressions"] > before["lang_page_impressions"],
        enough=days >= 7,
        days=days,
        min_days=14,
        patience=42,
    )
    text = (
        f"/{lang}/ 页面展示 {before['lang_page_impressions']} → {after['lang_page_impressions']}"
        f"（目标 ≥ 20）· 点击 {after['lang_page_clicks']} · 该语种需求 {after['demand']} 展示"
    )
    return Result(verdict, text, before, after, days)


# ---------------------------------------------------------------- 收录
async def index_measure(ctx: Ctx, w: dict) -> Result:
    urls = json.loads(w["scope"] or "[]")
    rows = {
        r["url"]: r
        for r in ctx.db.all(
            "SELECT url, coverage, checked_at FROM inspections WHERE site_id = ?", ctx.site["id"]
        )
    }
    ok = [u for u in urls if rows.get(u, {}).get("coverage") in report.NOT_INDEXED_OK]
    pending = [
        f"{path(u)}（{rows[u]['coverage'] if u in rows else '不在 sitemap'}）" for u in urls if u not in ok
    ]
    days = max((date.today() - date.fromisoformat(w["fixed_at"])).days + 1, 0)
    verdict = judge(
        goal=bool(urls) and len(ok) == len(urls),
        improved=bool(ok),
        enough=True,
        days=days,
        min_days=0,
        patience=21,
    )
    text = f"已收录 {len(ok)} / {len(urls)}" + (f" · 仍未收录：{'、'.join(pending[:4])}" if pending else "")
    return Result(
        verdict, text, {"urls": len(urls), "indexed": 0}, {"urls": len(urls), "indexed": len(ok)}, days
    )


# ---------------------------------------------------------------- Umami 度量
def ms_range(lo: str, hi: str) -> tuple[int, int]:
    start = datetime.combine(date.fromisoformat(lo), time.min, UTC)
    end = datetime.combine(date.fromisoformat(hi), time.max, UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


async def umami_counts(ctx: Ctx, lo: str, hi: str) -> dict:
    u, wid, host = ctx.clients.umami(), ctx.site["umami_website_id"], ctx.site["hostname"]
    win = ms_range(lo, hi)
    stats = await u.stats(wid, *win, hostname=host)
    events = await u.metrics(wid, "event", *win, hostname=host)
    hosts = await u.metrics(wid, "hostname", *win)
    return {
        "visits": stats.get("visits", 0) or 0,
        "events": {e["x"]: e["y"] for e in events},
        "other_hosts": {h["x"]: h["y"] for h in hosts if h["x"] != host},
    }


async def bug_measure(ctx: Ctx, w: dict) -> Result:
    """故障：失败率 = 失败事件 / 分母事件。

    分母优先用站点配置的漏斗第一步；没配置时用基线期里次数最多的非失败事件
    （转换类站点就是 upload），前后两段用同一个分母，口径才可比。
    """
    name = w["key"].split(":", 1)[1]
    b_lo, b_hi, a_lo, a_hi, days = windows(date.fromisoformat(w["fixed_at"]), date.today())
    base_counts = await umami_counts(ctx, b_lo, b_hi)
    denom_name = denominator(ctx.site, base_counts["events"])

    def shape(c: dict) -> dict:
        denom = c["events"].get(denom_name, 0) if denom_name else c["visits"]
        fails = c["events"].get(name, 0)
        return {
            "fails": fails,
            "base": denom,
            "base_name": denom_name or "访问",
            "rate": round(fails / denom, 4) if denom else None,
        }

    async def measure(lo: str, hi: str) -> dict:
        return shape(base_counts if (lo, hi) == (b_lo, b_hi) else await umami_counts(ctx, lo, hi))

    before, after = await measure(b_lo, b_hi), await measure(a_lo, a_hi)
    after["breakdown"] = await error_breakdown(ctx, name, a_lo, a_hi) if after["fails"] else []
    br, ar = before["rate"] or 0, after["rate"]
    verdict = judge(
        goal=ar is not None and (ar <= br * 0.5 if br else after["fails"] == 0),
        improved=ar is not None and br > 0 and ar < br * 0.8,
        enough=after["base"] >= 30,
        days=days,
        min_days=7,
        patience=14,
    )
    pct = lambda r: "—" if r is None else f"{r * 100:.1f}%"  # noqa: E731
    text = (
        f"失败率 {pct(br)} → {pct(ar)}（目标减半）· 观察期 {after['fails']} 次失败 / "
        f"{after['base']} 次 {before['base_name']}"
    )
    if after["breakdown"]:
        text += " · " + "、".join(f"{v['value']}×{v['count']}" for v in after["breakdown"][:4])
    return Result(verdict, text, before, after, days)


def denominator(site: dict, events: dict[str, int]) -> str | None:
    funnel = json.loads(site["funnel_events"] or "[]")
    if funnel:
        return funnel[0]
    normal = [(n, k) for k, n in events.items() if not report.FAIL_EVENT.search(k)]
    return max(normal)[1] if normal else None


async def error_breakdown(ctx: Ctx, name: str, lo: str, hi: str) -> list[dict]:
    u, wid, host = ctx.clients.umami(), ctx.site["umami_website_id"], ctx.site["hostname"]
    try:
        vals = await u.event_values(wid, name, "error_code", *ms_range(lo, hi), hostname=host)
    except UpstreamError:
        return []
    return sorted(({"value": v["value"], "count": v["total"]} for v in vals), key=lambda v: -v["count"])


async def hosts_measure(ctx: Ctx, w: dict) -> Result:
    b_lo, b_hi, a_lo, a_hi, days = windows(date.fromisoformat(w["fixed_at"]), date.today())
    before = await umami_counts(ctx, b_lo, b_hi)
    after = await umami_counts(ctx, a_lo, a_hi)
    b_pv, a_pv = sum(before["other_hosts"].values()), sum(after["other_hosts"].values())
    per_day = lambda pv, n: pv / n if n else 0  # noqa: E731
    verdict = judge(
        goal=a_pv == 0,
        improved=per_day(a_pv, days) < per_day(b_pv, BASELINE_DAYS) * 0.5,
        enough=days >= 1,
        days=days,
        min_days=7,
        patience=14,
    )
    ev = "、".join(f"{h} {n} PV" for h, n in after["other_hosts"].items()) or "无"
    text = f"其他域名流量 {b_pv} PV（修复前 28 天）→ {a_pv} PV（观察期 {days} 天）· 观察期来源：{ev}"
    return Result(verdict, text, {"other_pv": b_pv}, {"other_pv": a_pv, "hosts": after["other_hosts"]}, days)


MEASURES = {
    "rank": gsc_measure,
    "gap": gsc_measure,
    "cannibal": gsc_measure,
    "market": market_measure,
    "index": index_measure,
    "bug": bug_measure,
    "data": hosts_measure,
}


async def measure(ctx: Ctx, w: dict) -> Result:
    fn = MEASURES.get(w["type"])
    if fn is None:
        return Result(WAITING, "这类机会还没有度量方法，只做记录。", {}, {}, 0)
    try:
        return await fn(ctx, w)
    except (NotConfigured, UpstreamError) as e:
        return Result(WAITING, f"暂时取不到数据：{e}", {}, {}, 0)
