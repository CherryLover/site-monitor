# 引导流程：每一步「先校验、通过才保存」，以及进度判定
from __future__ import annotations

from app.core.secrets import SecretStore
from app.db import DB
from tests.fakes import upstream as F


def status(c):
    return {s["id"]: s for s in c.get("/api/v1/setup/status").json()["steps"]}


def test_fresh_install_is_incomplete(user):
    st = user.get("/api/v1/setup/status").json()
    assert st["complete"] is False and all(not s["configured"] for s in st["steps"])


def test_umami_bad_key_is_not_saved(user, fake):
    r = user.put("/api/v1/setup/umami", json={"base_url": f"{fake}/umami", "api_key": "wrong"}).json()
    assert r["ok"] is False and "401" in r["detail"]["error"]
    assert status(user)["umami"]["configured"] is False


def test_umami_bad_url_scheme(user):
    r = user.put("/api/v1/setup/umami", json={"base_url": "stats.example.com", "api_key": "k"}).json()
    assert r["ok"] is False and "http" in r["detail"]["error"]


def test_umami_unreachable(user):
    r = user.put("/api/v1/setup/umami", json={"base_url": "http://127.0.0.1:9", "api_key": "k"}).json()
    assert r["ok"] is False and "连不上" in r["detail"]["error"]


def test_umami_good_key_saved_and_masked(user, fake):
    r = user.put(
        "/api/v1/setup/umami", json={"base_url": f"{fake}/umami/", "api_key": F.GOOD_UMAMI_KEY}
    ).json()
    assert r["ok"] and {w["domain"] for w in r["detail"]["websites"]} == {"acme.test", "blog.acme.test"}
    cred = user.get("/api/v1/setup/credentials").json()["umami"]
    assert cred["base_url"] == f"{fake}/umami" and F.GOOD_UMAMI_KEY not in cred["api_key"]
    # 留空 key 时沿用已保存的
    assert user.put("/api/v1/setup/umami", json={"base_url": f"{fake}/umami", "api_key": ""}).json()["ok"]


def test_service_account_validation(user, fake):
    bad = [
        ("not json", "JSON"),
        ('{"type": "user"}', "缺少字段"),
        (F.service_account_json(fake, email="other@x.iam.gserviceaccount.com"), "400"),
    ]
    for raw, needle in bad:
        r = user.put("/api/v1/setup/google_sa", json={"json_text": raw}).json()
        assert r["ok"] is False and needle in r["detail"]["error"], (raw[:20], r)
    r = user.put("/api/v1/setup/google_sa", json={"json_text": F.service_account_json(fake)}).json()
    assert r["ok"] and r["detail"]["properties"][0]["site"] == F.PROP
    assert user.get("/api/v1/setup/credentials").json()["google_sa"]["email"] == F.SA_EMAIL


def test_api_key_validation(user):
    r = user.put("/api/v1/setup/google_key", json={"api_key": "bad"}).json()
    assert r["ok"] is False and all(not a["ok"] for a in r["detail"]["apis"])
    r = user.put("/api/v1/setup/google_key", json={"api_key": F.GOOD_GOOGLE_KEY}).json()
    assert r["ok"] and {a["api"] for a in r["detail"]["apis"]} == {"Chrome UX Report", "PageSpeed Insights"}


def test_recheck_and_candidates(user, fake):
    from tests.conftest import configure

    configure(user, fake)
    assert user.post("/api/v1/setup/umami/recheck").json()["ok"]
    assert user.post("/api/v1/setup/nope/recheck").status_code == 404
    cands = {c["domain"]: c for c in user.get("/api/v1/setup/site-candidates").json()}
    assert cands["acme.test"]["gsc_property"] == F.PROP and cands["acme.test"]["brand_terms"] == ["acme"]
    assert cands["blog.acme.test"]["gsc_property"] is None
    st = user.get("/api/v1/setup/status").json()
    assert not st["complete"] and status(user)["google_key"]["ok"]


def test_data_endpoints_before_setup_return_409(user):
    user.app.state.db.run(
        "INSERT INTO sites (domain, umami_website_id, gsc_property, hostname, created_at) "
        "VALUES ('x.test', 'w', NULL, 'x.test', 'now')"
    )
    r = user.get("/api/v1/sites/x.test/summary")
    assert r.status_code == 409 and r.json()["error"] == "setup_incomplete"


def test_secret_store_encrypts_and_survives_key_rotation(tmp_path):
    db = DB(str(tmp_path / "s.db"))
    SecretStore(db, "k1").set("a", "plain-value")
    raw = db.one("SELECT value FROM secrets WHERE key = 'a'")["value"]
    assert b"plain-value" not in raw
    assert SecretStore(db, "k1").get("a") == "plain-value"
    assert SecretStore(db, "k2").get("a") is None
