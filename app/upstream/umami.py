# ============================================================================
#  umami —— Umami REST API 的薄封装
#
#  只做「发请求、拆 JSON、把错误翻译成 UpstreamError」，不做任何业务判断。
#  鉴权用 Bearer API key（Umami v2 的 x-umami-api-key 在自建版上不通用）。
# ============================================================================
from __future__ import annotations

import time

import httpx

from app.upstream import UpstreamError, raise_for


class Umami:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self.client = client

    async def _get(self, endpoint: str, /, **params) -> dict | list:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self.client.get(self.base + endpoint, params=params, headers=self.headers, timeout=30)
        except httpx.HTTPError as e:
            raise UpstreamError("umami", f"连不上 Umami：{e.__class__.__name__}") from e
        raise_for("umami", r)
        return r.json()

    # ---------------------------------------------------------------- 账号
    async def me(self) -> dict:
        return await self._get("/api/me")

    async def websites(self) -> list[dict]:
        data = await self._get("/api/websites", pageSize=200)
        return data.get("data", []) if isinstance(data, dict) else data

    # ---------------------------------------------------------------- 统计
    async def stats(self, wid: str, start: int, end: int, **filters) -> dict:
        return await self._get(f"/api/websites/{wid}/stats", startAt=start, endAt=end, **filters)

    async def metrics(self, wid: str, kind: str, start: int, end: int, limit: int = 50, **filters) -> list:
        return await self._get(
            f"/api/websites/{wid}/metrics", type=kind, startAt=start, endAt=end, limit=limit, **filters
        )

    async def pageviews(self, wid: str, start: int, end: int, unit: str = "day", **filters) -> dict:
        return await self._get(
            f"/api/websites/{wid}/pageviews", startAt=start, endAt=end, unit=unit, timezone="UTC", **filters
        )

    async def event_values(self, wid: str, event: str, prop: str, start: int, end: int, **filters) -> list:
        return await self._get(
            f"/api/websites/{wid}/event-data/values",
            event=event,
            propertyName=prop,
            startAt=start,
            endAt=end,
            **filters,
        )


def window_ms(days: int, end_days_ago: int = 0) -> tuple[int, int]:
    """[今天-days-end_days_ago, 今天-end_days_ago) 的毫秒时间戳。"""
    now = int(time.time() * 1000)
    end = now - end_days_ago * 86_400_000
    return end - days * 86_400_000, end
