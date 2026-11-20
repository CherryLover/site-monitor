# 已处理 → 观察：修复前后同口径对比、每日判定、再次处理、复发
from __future__ import annotations

from datetime import date, timedelta

from app.core.watch.metrics import IMPROVING, NO_CHANGE, VERIFIED, WAITING, judge
from tests.fakes import upstream as F

S = f"/api/v1/sites/{F.DOMAIN}"
RANK = "rank:q:office to markdown"


def ago(n: int) -> str:
    return str(date.today() - timedelta(days=n))


def test_judge_table():
    j = dict(min_days=14, patience=28)
    assert judge(goal=True, improved=True, enough=True, days=20, **j) == VERIFIED
    assert judge(goal=True, improved=True, enough=True, days=5, **j) == IMPROVING  # 达标但观察期不够
    assert judge(goal=False, improved=False, enough=False, days=40, **j) == WAITING  # 样本不足
    assert judge(goal=False, improved=False, enough=True, days=10, **j) == WAITING
    assert judge(goal=False, improved=True, enough=True, days=40, **j) == IMPROVING
    assert judge(goal=False, improved=False, enough=True, days=40, **j) == NO_CHANGE


def test_mark_creates_watch_and_keeps_opportunity(ready):
    r = ready.post(f"{S}/watches", json={"key": RANK, "note": "首页 title 前置关键词", "fixed_at": ago(30)})
    assert r.status_code == 201, r.text
    w = r.json()
    # 假数据每天一样：修复前后排名不变，观察 31 天 → 未见改善
    assert w["verdict"] == NO_CHANGE and w["days"] >= 28
    assert "排名" in w["summary"] and w["before"]["position"] == w["after"]["position"]
    assert w["evidence_at_mark"] and w["history"][0]["note"] == "首页 title 前置关键词"
    assert len(w["points"]) == 1 and w["rule_firing"] is True
    # 机会不消失，只是标成 watching
    o = next(o for o in ready.get(f"{S}/opportunities").json() if o["key"] == RANK)
    assert o["status"] == "watching"
    # 同步写一条变更日志
    log = ready.get(f"{S}/changelog").json()[0]
    assert log["source"] == "watch" and "首页 title 前置关键词" in log["summary"]


def test_refix_appends_history_and_restarts(ready):
    ready.post(f"{S}/watches", json={"key": RANK, "note": "第一次", "fixed_at": ago(30)})
    w = ready.post(f"{S}/watches", json={"key": RANK, "note": "加内链", "fixed_at": ago(1)}).json()
    assert [h["note"] for h in w["history"]] == ["第一次", "加内链"]
    assert w["fixed_at"] == ago(1) and w["verdict"] == WAITING  # GSC 有延迟，新观察期还没有数据
    assert len(ready.get(f"{S}/watches").json()) == 1


def test_regression_after_verified(ready):
    w = ready.post(f"{S}/watches", json={"key": RANK, "note": "x", "fixed_at": ago(30)}).json()
    db = ready.app.state.db
    db.run("UPDATE watches SET verified_at = ? WHERE id = ?", ago(3), w["id"])
    again = next(x for x in ready.get(f"{S}/watches").json() if x["key"] == RANK)
    assert again["verdict"] == "regressed" and again["verdict_label"] == "复发"


def test_index_watch_tracks_scoped_urls(ready):
    opp = next(o for o in ready.get(f"{S}/opportunities").json() if o["type"] == "index")
    assert opp["key"] == "index:sitemap" and opp["scope"]
    w = ready.post(f"{S}/watches", json={"key": "index:sitemap", "note": "补内链", "fixed_at": ago(2)}).json()
    assert w["verdict"] == WAITING and w["summary"].startswith(f"已收录 0 / {len(opp['scope'])}")
    # 下一轮巡检：范围内的 URL 都收录了（/flaky/ 从没巡检成功过，补一行）
    db = ready.app.state.db
    for url in opp["scope"]:
        db.run(
            "INSERT OR REPLACE INTO inspections (site_id, url, coverage, checked_at) VALUES (1, ?, ?, ?)",
            url,
            "Submitted and indexed",
            ago(0),
        )
    w = next(x for x in ready.get(f"{S}/watches").json() if x["key"] == "index:sitemap")
    assert w["verdict"] == VERIFIED and w["verified_at"] == str(date.today())


def test_bug_watch_reports_rate_and_breakdown(ready):
    w = ready.post(
        f"{S}/watches", json={"key": "bug:convert_fail", "note": "失败归类", "fixed_at": ago(20)}
    ).json()
    assert "失败率" in w["summary"] and w["after"]["breakdown"][0]["value"] == "unknown"
    assert w["verdict"] in (NO_CHANGE, WAITING)  # 假 Umami 不分日期：前后一样


def test_hosts_and_market_watches(ready):
    h = ready.post(
        f"{S}/watches", json={"key": "data:hosts", "note": "data-domains", "fixed_at": ago(10)}
    ).json()
    assert "其他域名流量" in h["summary"]
    m = ready.post(f"{S}/watches", json={"key": "market:de", "note": "上线 /de/", "fixed_at": ago(20)}).json()
    assert (
        m["summary"].startswith("/de/ 页面展示 0 → 0") and m["verdict"] == WAITING
    )  # 新语言页要等收录，观察期 42 天


def test_watch_validation(ready):
    assert ready.post(f"{S}/watches", json={"key": "rank:q:nope", "note": "x"}).status_code == 404
    assert ready.post(f"{S}/watches", json={"key": RANK, "note": " "}).status_code == 422
    assert ready.post(f"{S}/watches", json={"key": RANK, "note": "x", "fixed_at": "11/20"}).status_code == 422
