# ============================================================================
#  e2e —— 真浏览器走完整条用户链路
#
#  首次登录 → 引导流程（每步先失败再成功）→ 添加站点 → 后台同步 → 概览
#  → 机会清单交互 → 变更日志 → 创建 Token → 用 Token 调 API → 吊销 → 退出
# ============================================================================
from __future__ import annotations

import re

import httpx
import pytest
from playwright.sync_api import Page, expect

from app.main import create_app
from tests.conftest import make_cfg
from tests.fakes import free_port, serve
from tests.fakes import upstream as F

pytestmark = pytest.mark.e2e


@pytest.fixture
def app_url(fake, tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    server = serve(create_app(make_cfg(fake, str(tmp_path / "e2e.db"), base_url=url)), port)
    yield url
    server.should_exit = True


def check(page: Page, button: str = "校验并保存"):
    page.get_by_role("button", name=button).click()


def test_first_login_to_api_token(page: Page, app_url: str, fake: str):
    page.set_default_timeout(15000)
    page.goto(app_url)
    # ---- 登录后第一次进入，被引导到配置流程
    expect(page).to_have_url(f"{app_url}/setup")
    expect(page.get_by_role("heading", name="初始化配置")).to_be_visible()

    # ---- 第 1 步：Umami，错误 key → 展示原因；正确 → 展示拿到的网站
    page.get_by_label("Umami 地址").fill(f"{fake}/umami")
    page.locator("input[name=api_key]").fill("wrong-key")
    check(page)
    expect(page.get_by_role("alert")).to_contain_text("凭证无效")
    page.locator("input[name=api_key]").fill(F.GOOD_UMAMI_KEY)
    check(page)
    expect(page.get_by_role("status").filter(has_text="校验通过")).to_contain_text("blog.acme.test")

    # ---- 第 2 步：Service Account，错误账号 → 失败；正确 → 列出资源
    expect(page.get_by_label("Service Account JSON")).to_be_visible()
    page.locator("textarea[name=json_text]").fill(
        F.service_account_json(fake, email="x@y.iam.gserviceaccount.com")
    )
    check(page)
    expect(page.get_by_role("alert")).to_contain_text("HTTP 400")
    page.locator("textarea[name=json_text]").fill(F.service_account_json(fake))
    check(page)
    expect(page.get_by_role("status").filter(has_text="校验通过")).to_contain_text(F.PROP)

    # ---- 第 3 步：API Key
    expect(page.locator("input[name=api_key]")).to_be_visible()
    page.locator("input[name=api_key]").fill("bad")
    check(page)
    expect(page.get_by_role("alert")).to_contain_text("PageSpeed Insights")
    page.locator("input[name=api_key]").fill(F.GOOD_GOOGLE_KEY)
    check(page)

    # ---- 第 4 步：站点，自动配对 Umami 网站与 GSC 资源
    expect(page.get_by_role("heading", name="选择要监控的站点")).to_be_visible()
    expect(page.get_by_text("没有匹配的资源，只监控 Umami")).to_be_visible()
    page.get_by_role("button", name="添加选中站点").click()

    # ---- 概览：同步完成后数据出现
    expect(page).to_have_url(re.compile(r"/\?site=acme\.test"))
    expect(page.get_by_role("heading", name="acme.test")).to_be_visible()
    expect(page.get_by_text("冲前三：office to markdown → /")).to_be_visible(timeout=40000)
    expect(page.locator("#kpis")).to_contain_text("非品牌点击")
    expect(page.locator("#funnel table")).to_contain_text("convert_success")
    expect(page.locator("#index")).to_contain_text("/guides/compare/", timeout=40000)
    expect(page.locator("#health")).to_contain_text("91", timeout=40000)
    expect(page.get_by_role("button", name="立即同步")).to_be_enabled(timeout=40000)

    # ---- 机会清单：筛选 + 标记已处理（持久化）
    page.get_by_role("button", name=re.compile("^抢词")).click()
    expect(page.locator("#opps .item")).to_have_count(1)
    page.get_by_role("button", name=re.compile("^全部")).click()
    first = page.locator("#opps .item").first
    first.get_by_role("button", name="标记已处理").click()
    expect(page.locator("#opps .item.dim")).to_have_count(1)
    page.reload()
    expect(page.locator("#opps .item.dim")).to_have_count(1)

    # ---- 变更日志
    page.get_by_role("button", name="添加记录").click()
    page.get_by_label("改了什么").fill("首页 title 前置核心词")
    page.get_by_role("button", name="保存").click()
    expect(page.locator("#changes")).to_contain_text("首页 title 前置核心词")

    # ---- 设置：创建 Token，用它调 API，再吊销
    page.get_by_role("link", name="设置").click()
    expect(page.get_by_role("heading", name="设置", exact=True)).to_be_visible()
    page.get_by_placeholder("claude-skill").fill("e2e")
    page.get_by_role("button", name="创建 Token").click()
    token = page.locator("#token-value").inner_text()
    assert token.startswith("smk_")
    h = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{app_url}/api/v1/sites/acme.test/opportunities", headers=h)
    assert r.status_code == 200 and any(o["type"] == "rank" for o in r.json())
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="吊销").click()
    expect(page.locator("#token-list")).to_contain_text("已吊销")
    assert httpx.get(f"{app_url}/api/v1/sites", headers=h).status_code == 401

    # ---- 凭证在设置页可以重新检测
    page.get_by_role("button", name="用已保存的凭证重新检测").first.click()
    expect(page.locator("form[data-step=umami] .result.ok")).to_be_visible()
    expect(page.locator("form[data-step=umami] button[data-recheck]")).to_be_enabled()

    # ---- 退出
    page.get_by_role("button", name="退出").click()
    expect(page.get_by_role("heading", name="已退出")).to_be_visible()
    assert httpx.get(f"{app_url}/api/v1/sites").status_code == 401
