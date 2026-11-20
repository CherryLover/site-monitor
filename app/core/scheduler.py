# ============================================================================
#  scheduler —— 进程内的极简调度器
#
#  每 10 分钟醒一次，看每个站点的每类作业「距离上次成功」是否超过周期。
#  只有一个进程、一个 SQLite 文件，不需要 cron、队列或分布式锁。
#  同一 (站点, 作业) 同时只跑一份：手动触发和定时触发共用这把锁。
# ============================================================================
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.core import sync
from app.core.clients import Clients, NotConfigured
from app.db import DB

log = logging.getLogger("site-monitor.scheduler")

PERIODS = {"gsc": timedelta(hours=20), "inspections": timedelta(days=7), "pagespeed": timedelta(days=7)}
TICK_SECONDS = 600


class Scheduler:
    def __init__(self, db: DB, clients: Clients):
        self.db, self.clients = db, clients
        self.running: set[tuple[int, str]] = set()
        self._task: asyncio.Task | None = None

    def is_running(self, site_id: int, kind: str) -> bool:
        return (site_id, kind) in self.running

    async def run(self, site: dict, kind: str) -> dict:
        key = (site["id"], kind)
        if key in self.running:
            return {"kind": kind, "ok": False, "detail": "已有同类作业在运行"}
        self.running.add(key)
        try:
            return await sync.run_job(self.db, self.clients, kind, site)
        finally:
            self.running.discard(key)

    def due(self, site_id: int, kind: str) -> bool:
        last = sync.last_ok(self.db, site_id, kind)
        if not last:
            return True
        return datetime.now(UTC) - datetime.fromisoformat(last.replace("Z", "+00:00")) > PERIODS[kind]

    async def tick(self) -> None:
        for site in self.db.all("SELECT * FROM sites ORDER BY position, id"):
            for kind in PERIODS:  # gsc 在前：pagespeed 依赖它选出的热门页面
                if self.due(site["id"], kind):
                    try:
                        await self.run(site, kind)
                    except NotConfigured:
                        return  # 引导流程没走完：整轮跳过

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
