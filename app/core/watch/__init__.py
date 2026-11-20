# ============================================================================
#  watch —— 「已处理」不是终点，是观察期的起点
#
#  标记一条机会为已处理 = 建一条观察：记下做了什么、哪天上线、当时的证据。
#  之后每天用同一把尺子量一次（修复前 28 天 vs 修复后），把判定写进 watch_points，
#  形成一条可回看的时间线。观察永不删除：
#    没见效 → 可以「再次处理」，追加一条处理记录、观察期从新日期重新算；
#    验证过又回落 → 判为「复发」，问题重新冒出来。
#  每次处理都同步写一条变更日志（source = watch），和人工记录放在同一条时间线上。
# ============================================================================
from __future__ import annotations

import json
from datetime import date

from app.core.watch import metrics
from app.core.watch.metrics import LABELS, REGRESSED, VERIFIED, WAITING, Ctx
from app.db import DB, now_iso

POINTS_SHOWN = 60


class WatchError(Exception):
    pass


def rows(db: DB, site_id: int) -> list[dict]:
    return db.all("SELECT * FROM watches WHERE site_id = ? ORDER BY fixed_at DESC, id DESC", site_id)


def watched_keys(db: DB, site_id: int) -> set[str]:
    return {r["key"] for r in db.all("SELECT key FROM watches WHERE site_id = ?", site_id)}


# ---------------------------------------------------------------- 建立 / 再次处理
def mark(db: DB, site_id: int, opp: dict | None, key: str, note: str, fixed_at: str) -> int:
    """opp 是标记那一刻的机会快照；已有观察时追加处理记录并重启观察期。"""
    existing = db.one("SELECT * FROM watches WHERE site_id = ? AND key = ?", site_id, key)
    entry = {"date": fixed_at, "note": note, "at": now_iso()}
    if existing:
        history = json.loads(existing["history"]) + [entry]
        db.run(
            "UPDATE watches SET fixed_at = ?, history = ?, verified_at = NULL, verdict = ? WHERE id = ?",
            fixed_at,
            json.dumps(history, ensure_ascii=False),
            WAITING,
            existing["id"],
        )
        wid, target = existing["id"], existing["target"]
    else:
        if opp is None:
            raise WatchError("当前机会清单里没有这个 key，无法建立观察")
        wid = db.run(
            "INSERT INTO watches (site_id, key, type, target, evidence, scope, history, fixed_at, verdict, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            site_id,
            key,
            opp["type"],
            opp["target"],
            opp["evidence"],
            json.dumps(opp.get("scope")) if opp.get("scope") is not None else None,
            json.dumps([entry], ensure_ascii=False),
            fixed_at,
            WAITING,
            now_iso(),
        )
        target = opp["target"]
    db.run(
        "INSERT INTO changelog (site_id, date, page, summary, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        site_id,
        fixed_at,
        None,
        f"{target}：{note}",
        "watch",
        now_iso(),
    )
    return wid


# ---------------------------------------------------------------- 评估
async def evaluate(db: DB, clients, site: dict, w: dict) -> dict:
    """量一次、判一次、落一个当天的点；复发由历史推出：验证过、现在又不达标。"""
    res = await metrics.measure(Ctx(db, clients, site), w)
    verdict = res.verdict
    if w["verified_at"] and verdict != VERIFIED and res.days:
        verdict = REGRESSED
    today = str(date.today())
    verified_at = w["verified_at"] or (today if verdict == VERIFIED else None)
    db.run(
        "UPDATE watches SET verdict = ?, summary = ?, verified_at = ?, checked_at = ? WHERE id = ?",
        verdict,
        res.summary,
        verified_at,
        now_iso(),
        w["id"],
    )
    db.run(
        "INSERT INTO watch_points (watch_id, date, verdict, summary, before, after) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(watch_id, date) DO UPDATE SET verdict = excluded.verdict, summary = excluded.summary, "
        "before = excluded.before, after = excluded.after",
        w["id"],
        today,
        verdict,
        res.summary,
        json.dumps(res.before, ensure_ascii=False),
        json.dumps(res.after, ensure_ascii=False),
    )
    return {
        **w,
        "verdict": verdict,
        "summary": res.summary,
        "verified_at": verified_at,
        "days": res.days,
        "before": res.before,
        "after": res.after,
    }


async def refresh_due(db: DB, clients, site: dict) -> int:
    """调度器用：每条观察每天只量一次。"""
    today = str(date.today())
    due = [w for w in rows(db, site["id"]) if not (w["checked_at"] or "").startswith(today)]
    for w in due:
        await evaluate(db, clients, site, w)
    return len(due)


# ---------------------------------------------------------------- 对外形状
def public(db: DB, w: dict, firing: set[str]) -> dict:
    points = db.all(
        "SELECT date, verdict, summary FROM watch_points WHERE watch_id = ? ORDER BY date DESC LIMIT ?",
        w["id"],
        POINTS_SHOWN,
    )
    return {
        "id": w["id"],
        "key": w["key"],
        "type": w["type"],
        "target": w["target"],
        "evidence_at_mark": w["evidence"],
        "history": json.loads(w["history"]),
        "fixed_at": w["fixed_at"],
        "days": w.get("days"),
        "verdict": w["verdict"],
        "verdict_label": LABELS[w["verdict"]],
        "summary": w.get("summary"),
        "verified_at": w["verified_at"],
        "rule_firing": w["key"] in firing,
        "before": w.get("before"),
        "after": w.get("after"),
        "points": list(reversed(points)),
    }


async def listing(db: DB, clients, site: dict, firing: set[str]) -> list[dict]:
    out = [public(db, await evaluate(db, clients, site, w), firing) for w in rows(db, site["id"])]
    order = {REGRESSED: 0, "no_change": 1, WAITING: 2, "improving": 3, VERIFIED: 4}
    return sorted(out, key=lambda w: (order[w["verdict"]], w["fixed_at"]))
