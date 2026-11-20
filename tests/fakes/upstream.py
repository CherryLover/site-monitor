# ============================================================================
#  fakes.upstream —— 一个进程模拟全部上游：Pocket ID / Umami / Google 全家
#
#    /oidc/*     OIDC 提供方：授权页直接带 code 跳回（模拟「用户已登录并同意」）
#    /umami/*    Umami API：只认 GOOD_UMAMI_KEY
#    /google/*   SA 换 token、Search Console、URL Inspection、PageSpeed、CrUX
#    /site/*     被监控站点本身（sitemap.xml）
#
#  数据是确定性的，覆盖每一条机会规则：冲前三、抢词、缺页、未收录、失败事件、脏域名。
# ============================================================================
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

GOOD_UMAMI_KEY = "umami_good_key"
GOOD_GOOGLE_KEY = "AIza-good-key"
SA_EMAIL = "monitor@fake-project.iam.gserviceaccount.com"
CLIENT_ID, CLIENT_SECRET = "test-client", "test-secret"
DOMAIN = "acme.test"
PROP = f"sc-domain:{DOMAIN}"
USER = {"sub": "user-1", "email": "owner@acme.test", "name": "Acme Owner"}

_OIDC_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign_jwt(claims: dict) -> str:
    seg = (
        b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "k1"}).encode())
        + "."
        + b64(json.dumps(claims).encode())
    )
    return seg + "." + b64(_OIDC_KEY.sign(seg.encode(), padding.PKCS1v15(), hashes.SHA256()))


def service_account_json(base: str, email: str = SA_EMAIL) -> str:
    pem = SA_KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "fake-project",
            "private_key_id": "x",
            "private_key": pem,
            "client_email": email,
            "client_id": "1",
            "token_uri": f"{base}/google/token",
        }
    )


# ---------------------------------------------------------------- 搜索数据
QUERIES = [  # (query, page, 每日展示, 排名, 每日点击, 国家)
    ("acme", "/", 6, 1.2, 4, "usa"),
    ("acme converter", "/", 2, 1.5, 1, "usa"),
    ("office to markdown", "/", 6, 6.5, 1, "usa"),
    ("word to markdown", "/guides/", 2, 70.0, 0, "usa"),
    ("word to markdown", "/docx/", 1, 66.0, 0, "gbr"),
    ("pdf to md", "/pdf/", 1, 45.0, 0, "ind"),
    ("markdown guide", "/guides/compare/", 2, 14.0, 0, "usa"),
    # 德语长尾：单个词过不了逐词门槛，合起来是「德语市场」——站点没有 /de/，应当生成市场缺口
    ("word zu markdown", "/docx/", 1, 70.0, 0, "deu"),
    ("excel zu markdown", "/docx/", 1, 66.0, 0, "aut"),
    # 印尼语需求同样排名靠后，但站点已有 /id/ 版本，不应生成市场缺口
    ("pdf ke markdown", "/pdf/", 1, 40.0, 0, "idn"),
]
INSPECT = {
    "/": "Submitted and indexed",
    "/docx/": "Submitted and indexed",
    "/pdf/": "Submitted and indexed",
    "/guides/": "Submitted and indexed",
    "/id/": "Submitted and indexed",
    "/guides/compare/": "Discovered - currently not indexed",
    "/new/": "URL is unknown to Google",
    "/flaky/": "503",  # 永远 503：验证单个 URL 失败不会拖垮整轮巡检
}


def gsc_rows(dims: list[str], start: str, end: str, base: str) -> list[dict]:
    d0, d1 = date.fromisoformat(start), min(date.fromisoformat(end), date.today() - timedelta(days=2))
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    row = lambda keys, c, i, pos: {"keys": keys, "clicks": c, "impressions": i, "position": pos}  # noqa: E731
    if dims == ["date"]:
        anon = 3
        clicks, impr = sum(q[4] for q in QUERIES), sum(q[2] for q in QUERIES) + anon
        return [row([str(d)], clicks, impr, 8.0) for d in days]
    if dims == ["date", "query", "page"]:
        return [row([str(d), q, f"{base}{p}"], c, i, pos) for d in days for q, p, i, pos, c, _ in QUERIES]
    if dims == ["date", "query", "country"]:
        return [row([str(d), q, cc], c, i, pos) for d in days for q, _, i, pos, c, cc in QUERIES]
    return []


def build(base_ref: dict) -> FastAPI:
    """base_ref["url"] 在服务启动后写入（端口随机），用于拼回调和资源地址。"""
    app = FastAPI()
    codes: dict[str, dict] = {}
    base = lambda: base_ref["url"]  # noqa: E731

    # ------------------------------------------------------------ OIDC
    @app.get("/oidc/.well-known/openid-configuration")
    async def discovery():
        b = base()
        return {
            "issuer": f"{b}/oidc",
            "authorization_endpoint": f"{b}/oidc/authorize",
            "token_endpoint": f"{b}/oidc/token",
            "jwks_uri": f"{b}/oidc/jwks",
            "userinfo_endpoint": f"{b}/oidc/userinfo",
            "id_token_signing_alg_values_supported": ["RS256"],
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "code_challenge_methods_supported": ["S256"],
        }

    @app.get("/oidc/jwks")
    async def jwks():
        pub = _OIDC_KEY.public_key().public_numbers()
        to = lambda n: b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))  # noqa: E731
        return {
            "keys": [
                {"kty": "RSA", "kid": "k1", "alg": "RS256", "use": "sig", "n": to(pub.n), "e": to(pub.e)}
            ]
        }

    @app.get("/oidc/authorize")
    async def authorize(request: Request):
        q = request.query_params
        if q.get("client_id") != CLIENT_ID or q.get("code_challenge_method") != "S256":
            raise HTTPException(400, "bad client or missing PKCE")
        code = secrets.token_urlsafe(16)
        codes[code] = {
            "nonce": q.get("nonce"),
            "challenge": q.get("code_challenge"),
            "redirect": q["redirect_uri"],
        }
        return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q['state']})}")

    @app.post("/oidc/token")
    async def token(request: Request):
        form = await request.form()
        auth = request.headers.get("authorization", "")
        cid, csec = form.get("client_id"), form.get("client_secret")
        if auth.startswith("Basic "):
            cid, _, csec = base64.b64decode(auth[6:]).decode().partition(":")
        if (cid, csec) != (CLIENT_ID, CLIENT_SECRET):
            return JSONResponse({"error": "invalid_client"}, 401)
        c = codes.pop(form.get("code"), None)
        verifier = form.get("code_verifier", "")
        if not c or b64(hashlib.sha256(verifier.encode()).digest()) != c["challenge"]:
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = int(time.time())
        idt = sign_jwt(
            {
                "iss": f"{base()}/oidc",
                "aud": CLIENT_ID,
                "iat": now,
                "exp": now + 600,
                "nonce": c["nonce"],
                **USER,
            }
        )
        return {
            "access_token": "at-" + secrets.token_hex(8),
            "token_type": "Bearer",
            "expires_in": 600,
            "id_token": idt,
        }

    @app.get("/oidc/userinfo")
    async def userinfo():
        return USER

    # ------------------------------------------------------------ Umami
    def umami_auth(request: Request):
        if request.headers.get("authorization") != f"Bearer {GOOD_UMAMI_KEY}":
            raise HTTPException(401, "Unauthorized")

    @app.get("/umami/api/me")
    async def me(request: Request):
        umami_auth(request)
        return {"user": {"username": "admin", "role": "admin"}}

    @app.get("/umami/api/websites")
    async def websites(request: Request):
        umami_auth(request)
        return {
            "data": [
                {"id": "w-acme", "name": "Acme", "domain": DOMAIN},
                {"id": "w-blog", "name": "Blog", "domain": "blog.acme.test"},
            ]
        }

    @app.get("/umami/api/websites/{wid}/stats")
    async def stats(wid: str, request: Request):
        umami_auth(request)
        q = request.query_params
        scale = 1 if q.get("path") else 4
        return {
            "pageviews": 50 * scale,
            "visitors": 20 * scale,
            "visits": 25 * scale,
            "bounces": 10 * scale,
            "totaltime": 1,
        }

    @app.get("/umami/api/websites/{wid}/metrics")
    async def metrics(wid: str, request: Request):
        umami_auth(request)
        q = request.query_params
        if q["type"] == "hostname":
            return [{"x": DOMAIN, "y": 400}, {"x": "localhost", "y": 120}]
        if q["type"] == "event":
            k = 1 if q.get("path") else 3
            return [
                {"x": "upload", "y": 30 * k},
                {"x": "convert_success", "y": 27 * k},
                {"x": "download", "y": 20 * k},
                {"x": "convert_fail", "y": 3 * k},
            ]
        return []

    @app.get("/umami/api/websites/{wid}/event-data/values")
    async def values(wid: str, request: Request):
        umami_auth(request)
        return [{"value": "unknown", "total": 7}, {"value": "1007", "total": 2}]

    # ------------------------------------------------------------ Google
    @app.post("/google/token")
    async def google_token(assertion: str = Form(...)):
        claims = json.loads(base64.urlsafe_b64decode(assertion.split(".")[1] + "=="))
        if claims.get("iss") != SA_EMAIL:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}, 400
            )
        return {"access_token": "ya29.fake", "expires_in": 3600, "token_type": "Bearer"}

    def google_auth(request: Request):
        if request.headers.get("authorization") != "Bearer ya29.fake":
            raise HTTPException(401)

    @app.get("/google/webmasters/v3/sites")
    async def gsc_sites(request: Request):
        google_auth(request)
        return {"siteEntry": [{"siteUrl": PROP, "permissionLevel": "siteFullUser"}]}

    @app.post("/google/webmasters/v3/sites/{prop}/searchAnalytics/query")
    async def gsc_query(prop: str, request: Request):
        google_auth(request)
        body = await request.json()
        rows = gsc_rows(body.get("dimensions", []), body["startDate"], body["endDate"], f"https://{DOMAIN}")
        start = body.get("startRow", 0)
        return {"rows": rows[start : start + body.get("rowLimit", 1000)]}

    @app.get("/google/webmasters/v3/sites/{prop}/sitemaps")
    async def gsc_sitemaps(prop: str, request: Request):
        google_auth(request)
        return {
            "sitemap": [
                {"path": f"{base()}/site/sitemap.xml", "contents": [{"submitted": "6", "indexed": "0"}]}
            ]
        }

    @app.post("/google/v1/urlInspection/index:inspect")
    async def inspect(request: Request):
        google_auth(request)
        url = (await request.json())["inspectionUrl"]
        p = "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else "/"
        if INSPECT.get(p) == "503":
            return JSONResponse(
                {"error": {"code": 503, "message": "The service is currently unavailable."}}, 503
            )
        return {
            "inspectionResult": {
                "indexStatusResult": {
                    "coverageState": INSPECT.get(p, "URL is unknown to Google"),
                    "lastCrawlTime": "2026-11-01T00:00:00Z",
                    "googleCanonical": url,
                }
            }
        }

    @app.get("/google/pagespeed/runPagespeed")
    async def psi(request: Request):
        if request.query_params.get("key") != GOOD_GOOGLE_KEY:
            return JSONResponse(
                {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}, 400
            )
        cat = lambda s: {"score": s}  # noqa: E731
        mobile = request.query_params.get("strategy") == "mobile"
        return {
            "lighthouseResult": {
                "categories": {
                    "performance": cat(0.91 if mobile else 0.99),
                    "seo": cat(1.0),
                    "accessibility": cat(0.89),
                    "best-practices": cat(1.0),
                },
                "audits": {
                    "largest-contentful-paint": {"numericValue": 2700 if mobile else 900},
                    "cumulative-layout-shift": {"numericValue": 0},
                    "total-blocking-time": {"numericValue": 90},
                },
            }
        }

    @app.post("/google/crux/records:queryRecord")
    async def crux(request: Request):
        if request.query_params.get("key") != GOOD_GOOGLE_KEY:
            return JSONResponse({"error": {"code": 400, "message": "API key not valid."}}, 400)
        return {"record": {"key": {"origin": (await request.json())["origin"]}}}

    # ------------------------------------------------------------ 被监控的站点
    @app.get("/site/sitemap.xml")
    async def sitemap():
        locs = "".join(f"<url><loc>https://{DOMAIN}{p}</loc></url>" for p in INSPECT)
        return Response(f'<?xml version="1.0"?><urlset>{locs}</urlset>', media_type="application/xml")

    return app
