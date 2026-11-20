# ============================================================================
#  upstream —— 外部服务客户端（Umami / Google）
#
#  所有上游错误统一成 UpstreamError：带来源、给人看的中文说明、HTTP 状态码。
#  引导流程直接把 message 展示给用户，所以它必须说清「哪里错了、怎么改」。
# ============================================================================
from __future__ import annotations

import httpx

HINTS = {
    401: "凭证无效或已过期",
    403: "凭证有效，但没有访问权限",
    404: "地址不存在，检查 URL 是否正确",
    429: "调用超出配额，稍后再试",
}


class UpstreamError(Exception):
    def __init__(self, source: str, message: str, status: int | None = None):
        super().__init__(message)
        self.source, self.message, self.status = source, message, status


def raise_for(source: str, r: httpx.Response) -> None:
    if r.is_success:
        return
    detail = ""
    try:
        body = r.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        detail = err.get("message", "") if isinstance(err, dict) else str(err)
    except ValueError:
        detail = r.text[:160]
    hint = HINTS.get(r.status_code, "上游返回错误")
    raise UpstreamError(
        source, f"{hint}（HTTP {r.status_code}）{'：' + detail if detail else ''}", r.status_code
    )
