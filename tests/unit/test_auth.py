# 登录、会话、Bearer Token 的边界
from __future__ import annotations

from tests.conftest import login


def test_pages_redirect_to_pocket_id_when_signed_out(anon):
    for p in ("/", "/setup", "/settings"):
        r = anon.get(p, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/auth/login"


def test_api_requires_auth(anon):
    assert anon.get("/api/v1/sites").status_code == 401
    assert anon.get("/api/v1/setup/status").status_code == 401
    assert anon.get("/healthz").json() == {"ok": True}


def test_login_redirect_uses_pkce(anon, fake):
    loc = anon.get("/auth/login", follow_redirects=False).headers["location"]
    assert loc.startswith(f"{fake}/oidc/authorize?")
    assert (
        "code_challenge_method=S256" in loc
        and "redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fcallback" in loc
    )


def test_full_login_and_logout(anon):
    login(anon)
    me = anon.get("/api/v1/me").json()
    assert me["email"] == "owner@acme.test"
    r = anon.post("/auth/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/signed-out"
    assert anon.get("/api/v1/me").status_code == 401


def test_callback_with_forged_state_is_rejected(anon):
    r = anon.get("/auth/callback?code=x&state=forged", follow_redirects=False)
    assert r.status_code == 400


def test_token_lifecycle(user):
    created = user.post("/api/v1/tokens", json={"name": "skill"}).json()
    token = created["token"]
    assert token.startswith("smk_") and "token" not in user.get("/api/v1/tokens").json()[0]

    user.post("/auth/logout")
    h = {"Authorization": f"Bearer {token}"}
    assert user.get("/api/v1/sites", headers=h).status_code == 200
    # Token 只能读数据，不能碰凭证和 Token 管理
    assert user.get("/api/v1/setup/credentials", headers=h).status_code == 401
    assert user.post("/api/v1/tokens", json={"name": "x"}, headers=h).status_code == 401

    login(user)
    assert user.delete(f"/api/v1/tokens/{created['id']}").status_code == 204
    assert user.delete(f"/api/v1/tokens/{created['id']}").status_code == 404
    user.post("/auth/logout")
    assert user.get("/api/v1/sites", headers=h).status_code == 401


def test_garbage_bearer_is_rejected(anon):
    assert anon.get("/api/v1/sites", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_token_needs_a_name(user):
    assert user.post("/api/v1/tokens", json={"name": "  "}).status_code == 422
