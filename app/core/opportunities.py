# ============================================================================
#  opportunities —— 把数据翻译成「下一步该做什么」
#
#  每条规则 = 一个信号 + 一个固定动作。规则只读数据，不写库；
#  处理状态（open / done / ignored）按稳定的 key 单独存在 opp_state。
#  样本门槛统一用 MIN_IMPR：展示太少的信号是噪声，不生成机会。
# ============================================================================
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core import report
from app.db import DB, now_iso

WINDOW_DAYS = 28
MIN_IMPR = 20
FAIL_EVENT = re.compile(r"fail|error", re.I)
NOT_INDEXED_OK = {
    "Submitted and indexed",
    "Indexed, not submitted in sitemap",
    "Page with redirect",
    "Alternate page with proper canonical tag",
}


def path(url: str) -> str:
    return urlparse(url).path or "/"


def item(key, kind, target, evidence, action, impact) -> dict:
    return {
        "key": key,
        "type": kind,
        "target": target,
        "evidence": evidence,
        "action": action,
        "impact": impact,
    }


# ---------------------------------------------------------------- 搜索类规则
def search_rules(qs: list[dict], pages: list[dict]) -> list[dict]:
    out = []
    nonbrand = [q for q in qs if not q["brand"]]
    for q in nonbrand:
        top = path(q["pages"][0]["page"])
        if 3 < q["position"] <= 10 and q["impressions"] >= MIN_IMPR:
            out.append(
                item(
                    f"rank:q:{q['query']}",
                    "rank",
                    f"冲前三：{q['query']} → {top}",
                    f"{q['impressions']} 展示 · 排名 {q['position']} · {q['clicks']} 点击",
                    f"在 {top} 的 title 和 H1 前部放入「{q['query']}」，并从其他页面加内链指向它。",
                    "高" if q["impressions"] >= 100 else "中",
                )
            )
        strong = [p for p in q["pages"] if p["impressions"] >= 3]
        if len(strong) >= 2 and q["impressions"] >= 10:
            ev = " · ".join(f"{path(p['page'])} {p['impressions']} 展示 #{p['position']}" for p in strong[:3])
            best = min(strong, key=lambda p: p["position"])
            out.append(
                item(
                    f"cannibal:{q['query']}",
                    "cannibal",
                    q["query"],
                    ev,
                    f"多个页面在抢同一个词。确定 {path(best['page'])} 为主页面，其余页面弱化该词并链接过去。",
                    "中",
                )
            )
    weak = [q for q in nonbrand if q["position"] > 30 and q["impressions"] >= 10]
    for q in weak[:5]:
        out.append(
            item(
                f"gap:{q['query']}",
                "gap",
                f"有需求、没竞争力：{q['query']}",
                f"{q['impressions']} 展示 · 排名 {q['position']}",
                "现有页面排在 30 名以后。考虑做一个专门针对这个词的页面，或大幅充实现有页面内容。",
                "中",
            )
        )
    for p in pages:
        if 10 < p["position"] <= 20 and p["impressions"] >= MIN_IMPR and path(p["page"]) != "/":
            out.append(
                item(
                    f"rank:p:{p['page']}",
                    "rank",
                    f"冲首页：{path(p['page'])}",
                    f"{p['impressions']} 展示 · 排名 {p['position']} · {p['clicks']} 点击",
                    "页面在第 2 页。补充内容深度（对比表、FAQ），并从高流量页面加内链。",
                    "中",
                )
            )
    return out


# ---------------------------------------------------------------- 收录 / 故障 / 数据质量
def index_rule(db: DB, site_id: int) -> list[dict]:
    bad = [
        r
        for r in db.all("SELECT url, coverage FROM inspections WHERE site_id = ? ORDER BY url", site_id)
        if r["coverage"] not in NOT_INDEXED_OK
    ]
    if not bad:
        return []
    ev = " · ".join(f"{path(r['url'])}（{r['coverage']}）" for r in bad[:4]) + (" …" if len(bad) > 4 else "")
    return [
        item(
            f"index:{len(bad)}",
            "index",
            f"{len(bad)} 个 sitemap URL 未收录",
            ev,
            "检查这些页面的内链入口和内容质量，然后在 Search Console 里请求编入索引。",
            "中" if len(bad) > 3 else "低",
        )
    ]


def umami_rules(overview: dict) -> list[dict]:
    out = []
    events = overview.get("current", {}).get("events", {})
    for name, n in events.items():
        if FAIL_EVENT.search(name) and n >= 5:
            out.append(
                item(
                    f"bug:{name}",
                    "bug",
                    f"失败事件 {name}",
                    f"近 {WINDOW_DAYS} 天 {n} 次",
                    f"用事件明细接口查看 {name} 的错误属性分布，定位主要失败原因。",
                    "高" if n >= 20 else "中",
                )
            )
    noisy = [h for h in overview.get("other_hostnames", []) if h["pageviews"] >= 10]
    if noisy:
        ev = " · ".join(f"{h['hostname']} {h['pageviews']} PV" for h in noisy[:4])
        out.append(
            item(
                "data:hosts",
                "data",
                "统计里混入了其他域名的流量",
                ev,
                "让统计脚本只在正式域名加载；多余域名做 301 跳转。监控已按主机名过滤，不影响这里的数字。",
                "低",
            )
        )
    return out


# ---------------------------------------------------------------- 汇总
async def build(db: DB, clients, site: dict) -> list[dict]:
    lo, hi = report.gsc_window(db, site["id"], WINDOW_DAYS)
    items = search_rules(report.queries(db, site, lo, hi), report.pages(db, site, lo, hi))
    items += index_rule(db, site["id"])
    if site["umami_website_id"]:
        items += umami_rules(await report.umami_overview(clients, site, WINDOW_DAYS))
    states = {
        r["key"]: r["status"]
        for r in db.all("SELECT key, status FROM opp_state WHERE site_id = ?", site["id"])
    }
    rank = {"高": 0, "中": 1, "低": 2}
    for it in items:
        it["status"] = states.get(it["key"], "open")
    return sorted(items, key=lambda it: (it["status"] != "open", rank[it["impact"]]))


def set_status(db: DB, site_id: int, key: str, status: str) -> None:
    db.run(
        "INSERT INTO opp_state VALUES (?, ?, ?, ?) ON CONFLICT(site_id, key) DO UPDATE SET "
        "status = excluded.status, updated_at = excluded.updated_at",
        site_id,
        key,
        status,
        now_iso(),
    )
