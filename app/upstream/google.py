# ============================================================================
#  google —— Search Console / URL Inspection / PageSpeed / CrUX
#
#  Service Account 用 RS256 JWT 换 access token（缓存到过期前 5 分钟）。
#  只申请 webmasters.readonly：就算 Search Console 里给了「完整」权限，这里也只读。
# ============================================================================
from __future__ import annotations

import base64
import json
import time
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.upstream import UpstreamError, raise_for

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
REQUIRED_SA_FIELDS = ("type", "client_email", "private_key", "token_uri")


def parse_service_account(raw: str) -> dict:
    """解析并做结构校验；错误信息直接给引导流程展示。"""
    try:
        sa = json.loads(raw)
    except ValueError as e:
        raise UpstreamError("google", "不是合法的 JSON 文件") from e
    if not isinstance(sa, dict):
        raise UpstreamError("google", "不是合法的 Service Account JSON")
    missing = [k for k in REQUIRED_SA_FIELDS if not sa.get(k)]
    if missing:
        raise UpstreamError("google", f"缺少字段：{', '.join(missing)}")
    if sa["type"] != "service_account":
        raise UpstreamError("google", f"type 应为 service_account，实际是 {sa['type']}")
    return sa


def _b64(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


class ServiceAccount:
    def __init__(self, sa: dict, client: httpx.AsyncClient):
        self.sa, self.client = sa, client
        self._token, self._exp = "", 0.0

    @property
    def email(self) -> str:
        return self.sa["client_email"]

    def _assertion(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.email,
            "scope": SCOPE,
            "aud": self.sa["token_uri"],
            "iat": now,
            "exp": now + 3600,
        }
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        seg = head + b"." + _b64(json.dumps(claims).encode())
        try:
            key = serialization.load_pem_private_key(self.sa["private_key"].encode(), None)
        except ValueError as e:
            raise UpstreamError("google", "private_key 无法解析，文件可能被改动过") from e
        return (seg + b"." + _b64(key.sign(seg, padding.PKCS1v15(), hashes.SHA256()))).decode()

    async def token(self) -> str:
        if self._token and time.time() < self._exp - 300:
            return self._token
        data = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": self._assertion()}
        try:
            r = await self.client.post(self.sa["token_uri"], data=data, timeout=30)
        except httpx.HTTPError as e:
            raise UpstreamError("google", f"连不上 Google 授权服务：{e.__class__.__name__}") from e
        raise_for("google", r)
        body = r.json()
        self._token, self._exp = body["access_token"], time.time() + body.get("expires_in", 3600)
        return self._token


class SearchConsole:
    def __init__(self, sa: ServiceAccount, gsc_base: str, inspect_base: str):
        self.sa, self.gsc, self.inspect_base = sa, gsc_base.rstrip("/"), inspect_base.rstrip("/")

    async def _call(self, method: str, url: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {await self.sa.token()}"}
        try:
            r = await self.sa.client.request(method, url, json=body, headers=headers, timeout=60)
        except httpx.HTTPError as e:
            raise UpstreamError("gsc", f"连不上 Search Console：{e.__class__.__name__}") from e
        raise_for("gsc", r)
        return r.json()

    async def sites(self) -> list[dict]:
        return (await self._call("GET", f"{self.gsc}/sites")).get("siteEntry", [])

    async def query(self, prop: str, body: dict) -> list[dict]:
        url = f"{self.gsc}/sites/{quote(prop, safe='')}/searchAnalytics/query"
        return (await self._call("POST", url, body)).get("rows", [])

    async def sitemaps(self, prop: str) -> list[dict]:
        return (await self._call("GET", f"{self.gsc}/sites/{quote(prop, safe='')}/sitemaps")).get(
            "sitemap", []
        )

    async def inspect(self, prop: str, url: str) -> dict:
        body = {"inspectionUrl": url, "siteUrl": prop}
        res = await self._call("POST", f"{self.inspect_base}/urlInspection/index:inspect", body)
        return res["inspectionResult"]["indexStatusResult"]


class PageSpeed:
    def __init__(self, api_key: str, client: httpx.AsyncClient, psi_base: str, crux_base: str):
        self.key, self.client = api_key, client
        self.psi_base, self.crux_base = psi_base.rstrip("/"), crux_base.rstrip("/")

    async def run(self, url: str, strategy: str) -> dict:
        params = [("url", url), ("strategy", strategy), ("key", self.key)]
        params += [("category", c) for c in ("performance", "seo", "accessibility", "best-practices")]
        try:
            r = await self.client.get(f"{self.psi_base}/runPagespeed", params=params, timeout=120)
        except httpx.HTTPError as e:
            raise UpstreamError("pagespeed", f"PageSpeed 请求失败：{e.__class__.__name__}") from e
        raise_for("pagespeed", r)
        lh = r.json()["lighthouseResult"]
        score = lambda k: round(lh["categories"][k]["score"] * 100) if k in lh["categories"] else None  # noqa: E731
        num = lambda k: lh["audits"].get(k, {}).get("numericValue")  # noqa: E731
        return {
            "performance": score("performance"),
            "seo": score("seo"),
            "accessibility": score("accessibility"),
            "best_practices": score("best-practices"),
            "lcp_ms": num("largest-contentful-paint"),
            "cls": num("cumulative-layout-shift"),
            "tbt_ms": num("total-blocking-time"),
        }

    async def crux(self, origin: str) -> dict | None:
        """404 = 该站流量不足，CrUX 没有数据，这是正常结果而不是错误。"""
        try:
            r = await self.client.post(
                f"{self.crux_base}/records:queryRecord",
                params={"key": self.key},
                json={"origin": origin},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise UpstreamError("crux", f"CrUX 请求失败：{e.__class__.__name__}") from e
        if r.status_code == 404:
            return None
        raise_for("crux", r)
        return r.json().get("record")
