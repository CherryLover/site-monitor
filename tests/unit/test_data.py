# 同步 → 落库 → 报表口径 → 机会规则，整条数据链路
from __future__ import annotations

from tests.fakes import upstream as F

S = f"/api/v1/sites/{F.DOMAIN}"


def test_sync_jobs_all_succeed(ready):
    site = ready.get(S).json()
    assert all(j["ok"] for j in site["jobs"].values()), site["jobs"]
    jobs = ready.get(f"{S}/jobs").json()
    assert {j["kind"] for j in jobs} == {"gsc", "inspections", "pagespeed"}
    insp = next(j for j in jobs if j["kind"] == "inspections")
    assert insp["detail"].startswith("巡检 6 个 URL，1 个失败") and "/flaky/" in insp["detail"]


def test_summary_splits_brand_and_filters_hostnames(ready):
    s = ready.get(f"{S}/summary?range=7d").json()
    cur = s["search"]["current"]
    # 每天：品牌 6+2 展示，非品牌 6+2+1+1+2，匿名 3
    assert cur["brand"]["impressions"] == 8 * 7 and cur["nonbrand"]["impressions"] == 12 * 7
    assert cur["anonymous"]["impressions"] == 3 * 7
    assert cur["brand"]["clicks"] == 5 * 7 and cur["nonbrand"]["clicks"] == 7
    assert s["traffic"]["current"]["visitors"] == 80
    assert s["traffic"]["other_hostnames"] == [{"hostname": "localhost", "pageviews": 120}]


def test_trend_has_requested_weeks(ready):
    weeks = ready.get(f"{S}/trend?weeks=4").json()
    assert len(weeks) == 4 and weeks[-1]["nonbrand"]["position"] is not None


def test_queries_include_all_landing_pages(ready):
    q = {r["query"]: r for r in ready.get(f"{S}/queries?range=28d").json()}
    assert q["acme"]["brand"] and not q["office to markdown"]["brand"]
    assert len(q["word to markdown"]["pages"]) == 2
    assert ready.get(f"{S}/queries?brand=false").json()[0]["brand"] is False


def test_pages_funnel_joins_gsc_and_umami(ready):
    f = ready.get(f"{S}/pages?range=28d").json()
    assert f["events"] == ["upload", "convert_success", "download", "convert_fail"]
    home = next(r for r in f["rows"] if r["path"] == "/")
    assert (
        home["impressions"] > 0
        and home["visits"] == 25
        and home["events"]["upload"] == 30
        and home["failures"] == 3
    )


def test_index_status_and_health(ready):
    ix = ready.get(f"{S}/index-status").json()
    assert ix["total"] == 6 and ix["indexed"] == 4
    assert {p["coverage"] for p in ix["problems"]} == {
        "Discovered - currently not indexed",
        "URL is unknown to Google",
    }
    h = ready.get(f"{S}/health").json()
    mobile = next(
        r for r in h["pagespeed"] if r["strategy"] == "mobile" and r["url"].endswith(f"{F.DOMAIN}/")
    )
    assert mobile["performance"] == 91 and h["fail_events_28d"] == {"convert_fail": 9}


def test_every_opportunity_rule_fires(ready):
    opps = ready.get(f"{S}/opportunities").json()
    kinds = {o["type"] for o in opps}
    assert kinds == {"rank", "cannibal", "gap", "index", "bug", "data"}, kinds
    keys = {o["key"] for o in opps}
    assert "rank:q:office to markdown" in keys and "cannibal:word to markdown" in keys
    assert "rank:p:https://acme.test/guides/compare/" in keys
    assert all(o["status"] == "open" for o in opps)


def test_opportunity_status_roundtrip(ready):
    key = "rank:q:office to markdown"
    assert ready.patch(f"{S}/opportunities", json={"key": key, "status": "done"}).status_code == 200
    o = next(o for o in ready.get(f"{S}/opportunities").json() if o["key"] == key)
    assert o["status"] == "done"
    assert ready.patch(f"{S}/opportunities", json={"key": key, "status": "weird"}).status_code == 422


def test_events_breakdown(ready):
    e = ready.get(f"{S}/events?name=convert_fail&props=error_code").json()
    assert e == {"error_code": [{"value": "unknown", "count": 7}, {"value": "1007", "count": 2}]}


def test_changelog(ready):
    assert (
        ready.post(
            f"{S}/changelog", json={"summary": "首页 title 调整", "date": "2026-11-01", "page": "/"}
        ).status_code
        == 201
    )
    assert ready.post(f"{S}/changelog", json={"summary": "x", "date": "11/01"}).status_code == 422
    assert ready.get(f"{S}/changelog").json()[0]["summary"] == "首页 title 调整"


def test_site_crud_and_validation(ready):
    assert ready.post("/api/v1/sites", json={"domain": F.DOMAIN}).status_code == 409
    assert ready.post("/api/v1/sites", json={"domain": "bad/domain"}).status_code == 422
    r = ready.patch(S, json={"brand_terms": ["acme", " Acme Inc "], "funnel_events": ["upload"]}).json()
    assert r["brand_terms"] == ["acme", "Acme Inc"] and r["funnel_events"] == ["upload"]
    assert ready.get(f"{S}/pages").json()["events"] == ["upload"]
    assert ready.post(f"{S}/sync?kind=bogus").status_code == 422
    assert ready.delete(S).status_code == 204
    assert ready.get(S).status_code == 404
    assert ready.app.state.db.one("SELECT COUNT(*) n FROM gsc_rows")["n"] == 0


def test_incremental_sync_only_refreshes_recent_days(ready):
    db = ready.app.state.db
    before = db.one("SELECT COUNT(*) n, MIN(date) d FROM gsc_daily")
    ready.post(f"{S}/sync?kind=gsc")

    async def drain():
        import asyncio

        while ready.app.state.tasks:
            await asyncio.gather(*list(ready.app.state.tasks))

    ready.portal.call(drain)
    after = db.one("SELECT COUNT(*) n, MIN(date) d FROM gsc_daily")
    assert after == before  # 重拉最近 5 天，总天数和起点都不变
