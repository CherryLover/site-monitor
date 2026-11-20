# ============================================================================
#  conftest —— 共用夹具：假上游、指向假上游的配置、已登录的客户端
# ============================================================================
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.core import sync
from app.main import create_app
from tests.fakes import free_port, serve
from tests.fakes import upstream as F

sync.RETRY_DELAYS = (0, 0)  # 测试里重试不等待


@pytest.fixture(scope="session")
def fake() -> str:
    ref: dict = {}
    port = free_port()
    ref["url"] = f"http://127.0.0.1:{port}"
    server = serve(F.build(ref), port)
    yield ref["url"]
    server.should_exit = True


def make_cfg(fake: str, db_path: str, base_url: str = "http://testserver") -> Config:
    return Config(
        base_url=base_url,
        oidc_issuer=f"{fake}/oidc",
        oidc_client_id=F.CLIENT_ID,
        oidc_client_secret=F.CLIENT_SECRET,
        secret_key="test-secret-key-0123456789",
        db_path=db_path,
        gsc_base=f"{fake}/google/webmasters/v3",
        inspect_base=f"{fake}/google/v1",
        psi_base=f"{fake}/google/pagespeed",
        crux_base=f"{fake}/google/crux",
        scheduler=False,
    )


@pytest.fixture
def cfg(fake, tmp_path) -> Config:
    return make_cfg(fake, str(tmp_path / "t.db"))


@pytest.fixture
def anon(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


def login(client: TestClient) -> None:
    """走完整 OIDC 授权码流程：app → 假 Pocket ID → 回调。"""
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    r2 = httpx.get(r.headers["location"], follow_redirects=False)
    assert r2.status_code in (302, 307), r2.text
    cb = urlparse(r2.headers["location"])
    r3 = client.get(f"{cb.path}?{cb.query}", follow_redirects=False)
    assert r3.status_code == 307 and r3.headers["location"] == "/", r3.text


@pytest.fixture
def user(anon):
    login(anon)
    return anon


def configure(client: TestClient, fake: str) -> None:
    assert client.put(
        "/api/v1/setup/umami", json={"base_url": f"{fake}/umami", "api_key": F.GOOD_UMAMI_KEY}
    ).json()["ok"]
    assert client.put("/api/v1/setup/google_sa", json={"json_text": F.service_account_json(fake)}).json()[
        "ok"
    ]
    assert client.put("/api/v1/setup/google_key", json={"api_key": F.GOOD_GOOGLE_KEY}).json()["ok"]


def add_site_and_sync(client: TestClient) -> None:
    r = client.post(
        "/api/v1/sites",
        json={
            "domain": F.DOMAIN,
            "umami_website_id": "w-acme",
            "gsc_property": F.PROP,
            "brand_terms": ["acme"],
        },
    )
    assert r.status_code == 201, r.text
    app = client.app

    async def drain():
        while app.state.tasks:
            await asyncio.gather(*list(app.state.tasks))

    client.portal.call(drain)


@pytest.fixture
def ready(user, fake):
    configure(user, fake)
    add_site_and_sync(user)
    return user
