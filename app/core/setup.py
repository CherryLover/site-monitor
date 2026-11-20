# ============================================================================
#  setup —— 引导流程：凭证校验 + 进度判定 + 站点候选
#
#  每个校验函数都真实调用一次上游，返回 (ok, detail)。detail 是给页面展示的
#  结构化结果：成功时展示拿到了什么，失败时展示原因和修复办法。
#  校验通过才落库 —— 数据库里永远只有「验证过能用」的凭证。
# ============================================================================
from __future__ import annotations

import asyncio

import httpx

from app.config import Config
from app.core import secrets as S
from app.core.clients import Clients
from app.db import DB
from app.upstream import UpstreamError
from app.upstream.google import PageSpeed, SearchConsole, ServiceAccount, parse_service_account
from app.upstream.umami import Umami

STEPS = [
    {"id": "umami", "title": "Umami", "keys": [S.UMAMI_URL, S.UMAMI_KEY]},
    {"id": "google_sa", "title": "Google Service Account", "keys": [S.GOOGLE_SA]},
    {"id": "google_key", "title": "Google API Key", "keys": [S.GOOGLE_KEY]},
    {"id": "sites", "title": "监控站点", "keys": []},
]


def fail(e: Exception) -> tuple[bool, dict]:
    msg = e.message if isinstance(e, UpstreamError) else f"{e.__class__.__name__}: {e}"
    return False, {"error": msg}


# ---------------------------------------------------------------- 各步校验
async def verify_umami(base_url: str, api_key: str, http: httpx.AsyncClient) -> tuple[bool, dict]:
    if not base_url.startswith(("http://", "https://")):
        return False, {"error": "地址需要以 http:// 或 https:// 开头"}
    u = Umami(base_url, api_key, http)
    try:
        me = await u.me()
        sites = await u.websites()
    except Exception as e:  # noqa: BLE001 —— 任何失败都要展示给用户
        return fail(e)
    user = me.get("user", {})
    return True, {
        "user": user.get("username"),
        "role": user.get("role"),
        "websites": [{"id": w["id"], "name": w["name"], "domain": w["domain"]} for w in sites],
    }


async def verify_service_account(raw: str, cfg: Config, http: httpx.AsyncClient) -> tuple[bool, dict]:
    try:
        sa = ServiceAccount(parse_service_account(raw), http)
        await sa.token()
        props = await SearchConsole(sa, cfg.gsc_base, cfg.inspect_base).sites()
    except Exception as e:  # noqa: BLE001
        return fail(e)
    detail = {
        "email": sa.email,
        "properties": [{"site": p["siteUrl"], "permission": p["permissionLevel"]} for p in props],
    }
    if not props:
        detail["error"] = (
            f"授权成功，但这个账号还看不到任何站点。请在 Search Console → 设置 → 用户和权限 "
            f"里添加 {sa.email}（受限权限即可）。"
        )
        return False, detail
    return True, detail


async def verify_api_key(key: str, cfg: Config, http: httpx.AsyncClient) -> tuple[bool, dict]:
    ps = PageSpeed(key, http, cfg.psi_base, cfg.crux_base)

    async def probe(name: str, coro) -> dict:
        try:
            await coro
            return {"api": name, "ok": True}
        except Exception as e:  # noqa: BLE001
            return {"api": name, "ok": False, "error": fail(e)[1]["error"]}

    results = await asyncio.gather(
        probe("Chrome UX Report", ps.crux("https://www.google.com")),
        probe("PageSpeed Insights", ps.run("https://www.google.com/", "desktop")),
    )
    ok = all(r["ok"] for r in results)
    detail: dict = {"apis": list(results)}
    if not ok:
        detail["error"] = "部分 API 不可用：在 Google Cloud 控制台启用对应 API，并确认 Key 的限制里包含它们。"
    return ok, detail


# ---------------------------------------------------------------- 进度
def status(db: DB, store: S.SecretStore) -> dict:
    steps = []
    for st in STEPS:
        if st["id"] == "sites":
            n = db.one("SELECT COUNT(*) AS n FROM sites")["n"]
            steps.append(
                {
                    "id": "sites",
                    "title": st["title"],
                    "configured": n > 0,
                    "ok": n > 0,
                    "detail": {"count": n},
                    "checked_at": None,
                }
            )
            continue
        configured = all(store.get(k) for k in st["keys"])
        chk = store.check(st["id"]) if configured else None
        steps.append(
            {
                "id": st["id"],
                "title": st["title"],
                "configured": configured,
                "ok": bool(chk and chk["ok"]),
                "detail": chk["detail"] if chk else None,
                "checked_at": chk["checked_at"] if chk else None,
            }
        )
    return {"complete": all(s["configured"] for s in steps), "steps": steps}


async def recheck(step: str, clients: Clients) -> tuple[bool, dict]:
    """用已保存的凭证重新跑一次校验（设置页的「重新检测」按钮）。"""
    g, cfg, http = clients.secrets.get, clients.cfg, clients.http
    if step == "umami":
        ok, d = await verify_umami(g(S.UMAMI_URL) or "", g(S.UMAMI_KEY) or "", http)
    elif step == "google_sa":
        ok, d = await verify_service_account(g(S.GOOGLE_SA) or "", cfg, http)
    elif step == "google_key":
        ok, d = await verify_api_key(g(S.GOOGLE_KEY) or "", cfg, http)
    else:
        raise ValueError(step)
    clients.secrets.record_check(step, ok, d)
    return ok, d


# ---------------------------------------------------------------- 站点候选
def bare(domain: str) -> str:
    return domain.lower().removeprefix("www.")


def prop_domain(prop: str) -> str:
    if prop.startswith("sc-domain:"):
        return bare(prop.removeprefix("sc-domain:"))
    return bare(prop.split("://", 1)[-1].split("/", 1)[0])


def default_brand_terms(domain: str) -> list[str]:
    label = bare(domain).split(".")[0]
    return sorted({label, label.replace("-", " "), label.replace("-", "")})


async def candidates(clients: Clients, db: DB) -> list[dict]:
    """Umami 网站 × GSC 属性按域名配对；已添加的标记出来。"""
    websites = await clients.umami().websites()
    props = [p["siteUrl"] for p in await clients.gsc().sites()]
    existing = {r["domain"] for r in db.all("SELECT domain FROM sites")}
    out = []
    for w in websites:
        dom = bare(w["domain"])
        matches = [p for p in props if prop_domain(p) == dom]
        matches.sort(key=lambda p: not p.startswith("sc-domain:"))  # 优先域名资源
        out.append(
            {
                "domain": dom,
                "name": w["name"],
                "umami_website_id": w["id"],
                "gsc_property": matches[0] if matches else None,
                "brand_terms": default_brand_terms(dom),
                "added": dom in existing,
            }
        )
    return out
