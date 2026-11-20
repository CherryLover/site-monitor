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
    # ---- 未登录只看到登录页，点按钮才去 Pocket ID
    expect(page).to_have_url(f"{app_url}/login")
    page.get_by_role("link", name="用 Pocket ID 登录").click()
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
    page.locator("input[name=pick][value='1']").check()  # 第二个站点（只有 Umami）也加上，用来测排序
    page.get_by_role("button", name="添加选中站点").click()

    # ---- 概览：同步中局部刷新，整页不重载（用 window 上的标记验证）
    expect(page).to_have_url(re.compile(r"/\?site=acme\.test"))
    expect(page.get_by_role("heading", name=re.compile(r"acme\.test$"))).to_be_visible()
    page.evaluate("window.__noReload = 1")
    expect(page.get_by_text("冲前三：office to markdown → /")).to_be_visible(timeout=40000)
    expect(page.get_by_text("德语市场没有对应版本")).to_be_visible()
    expect(page.locator("#kpis")).to_contain_text("非品牌点击")
    expect(page.locator("#funnel table")).to_contain_text("convert_success")
    expect(page.locator("#index")).to_contain_text("/guides/compare/", timeout=40000)
    expect(page.locator("#health")).to_contain_text("91", timeout=40000)
    expect(page.get_by_role("button", name="立即同步")).to_be_enabled(timeout=40000)
    assert page.evaluate("window.__noReload") == 1, "同步过程中页面被整页刷新了"

    # ---- 切换时间范围：只刷新指标和漏斗，不重载
    page.get_by_role("button", name="7 天").click()
    expect(page).to_have_url(re.compile(r"range=7d"))
    expect(page.get_by_role("button", name="7 天")).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("window.__noReload") == 1

    # ---- 手动同步：按钮进入同步中，完成后恢复，仍不重载
    page.get_by_role("button", name="立即同步").click()
    expect(page.get_by_role("button", name="立即同步")).to_be_enabled(timeout=40000)
    assert page.evaluate("window.__noReload") == 1

    # ---- 机会清单：筛选；标记已处理 = 进入观察，不消失、可再次处理
    opps = page.locator("#opps")
    opps.get_by_role("button", name=re.compile("^抢词")).click()
    expect(opps.locator(".item")).to_have_count(1)
    opps.get_by_role("button", name=re.compile("^全部")).click()
    target = opps.locator(".item").first.locator(".t b").inner_text()
    opps.locator(".item").first.get_by_role("button", name="标记已处理").click()
    opps.get_by_label("做了什么").fill("首页 title 前置关键词")
    opps.get_by_role("button", name="标记已处理，开始观察").click()
    expect(opps.get_by_role("tab", name=re.compile("^观察中"))).to_have_attribute("aria-selected", "true")
    card = opps.locator(".item.watch").first
    expect(card).to_contain_text(target)
    expect(card).to_contain_text("首页 title 前置关键词")
    expect(card.locator(".dots-line .v")).to_have_count(1)
    expect(card.locator(".chip").first).to_contain_text(re.compile("等待数据|见效中|已验证|未见改善|复发"))
    # 再次处理：追加记录，仍是同一张卡
    card.get_by_role("button", name="再次处理").click()
    opps.get_by_label("这次又做了什么").fill("加内链")
    opps.get_by_role("button", name="记录并重新观察").click()
    expect(opps.locator(".item.watch")).to_have_count(1)
    expect(opps.locator(".item.watch .hist li")).to_have_count(2)
    assert page.evaluate("window.__noReload") == 1  # 标记与再次处理都是局部刷新
    # 刷新后依然在观察中；待处理里不再出现它
    page.reload()
    page.locator("#opps").get_by_role("tab", name=re.compile("^观察中")).click()
    expect(page.locator("#opps .item.watch")).to_have_count(1)
    page.locator("#opps").get_by_role("tab", name=re.compile("^待处理")).click()
    expect(page.locator("#opps .item", has_text=target)).to_have_count(0)

    # ---- 变更日志
    page.get_by_role("button", name="添加记录").click()
    page.get_by_label("改了什么").fill("首页 title 前置核心词")
    page.get_by_role("button", name="保存").click()
    expect(page.locator("#changes")).to_contain_text("首页 title 前置核心词")

    # ---- 设置：二级菜单，默认进入「站点」多维表格
    page.get_by_role("link", name="设置").click()
    expect(page).to_have_url(f"{app_url}/settings")
    expect(page.get_by_role("heading", name="站点", exact=True)).to_be_visible()
    nav = page.get_by_role("navigation", name="设置")
    for item in ("Umami", "Google Service Account", "Google API Key", "站点", "API Token"):
        expect(nav.get_by_role("link", name=item)).to_be_visible()
    rows = page.locator("#sites-table tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_have_attribute("data-domain", "acme.test")

    # ---- 单元格内编辑：回车保存，刷新后还在
    cell = page.get_by_label("acme.test 的品牌词", exact=True)
    cell.fill("acme, acme inc")
    cell.press("Enter")
    expect(cell).to_have_class(re.compile("saved"))
    page.reload()
    expect(page.get_by_label("acme.test 的品牌词", exact=True)).to_have_value("acme, acme inc")

    # ---- 拖拽排序：blog 拖到第一位，序号和概览页顺序都跟着变
    page.locator('tr[data-domain="blog.acme.test"] .drag').drag_to(
        page.locator('tr[data-domain="acme.test"]'), target_position={"x": 40, "y": 4}
    )
    expect(page.get_by_text("顺序已保存")).to_be_visible()
    expect(rows.nth(0)).to_have_attribute("data-domain", "blog.acme.test")
    expect(rows.nth(0).locator(".site-no")).to_have_text("1")
    page.reload()
    expect(page.locator("#sites-table tbody tr").nth(0)).to_have_attribute("data-domain", "blog.acme.test")
    page.get_by_role("link", name="概览").click()
    tabs = page.get_by_role("navigation", name="站点").get_by_role("link")
    expect(tabs.nth(0)).to_have_text(re.compile(r"1\s*blog\.acme\.test"))
    expect(tabs.nth(1)).to_have_text(re.compile(r"2\s*acme\.test"))
    expect(page.get_by_role("heading", name=re.compile(r"blog\.acme\.test$"))).to_be_visible()

    # ---- 键盘也能排序：在手柄上按 ↑ 把 acme 移回第一
    page.goto(f"{app_url}/settings/sites")
    page.locator('tr[data-domain="acme.test"] .drag').focus()
    page.keyboard.press("ArrowUp")
    expect(page.locator("#sites-table tbody tr").nth(0)).to_have_attribute("data-domain", "acme.test")
    expect(page.get_by_text("顺序已保存")).to_be_visible()

    # ---- API Token：表格里创建、用它调 API、两步吊销
    page.get_by_role("navigation", name="设置").get_by_role("link", name="API Token").click()
    expect(page).to_have_url(f"{app_url}/settings/tokens")
    page.get_by_placeholder("claude-skill").fill("e2e")
    page.get_by_role("button", name="创建 Token").click()
    token = page.locator("#token-value").inner_text()
    assert token.startswith("smk_")
    h = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"{app_url}/api/v1/sites/acme.test/opportunities", headers=h)
    assert r.status_code == 200 and any(o["type"] == "market" for o in r.json())
    page.get_by_role("button", name="吊销").click()
    page.get_by_role("button", name="确认吊销").click()
    expect(page.locator("#token-list")).to_contain_text("已吊销")
    assert httpx.get(f"{app_url}/api/v1/sites", headers=h).status_code == 401

    # ---- 数据源：单独一页，重新检测凭证
    page.get_by_role("navigation", name="设置").get_by_role("link", name="Umami").click()
    expect(page).to_have_url(f"{app_url}/settings/umami")
    expect(page.locator("form[data-step]")).to_have_count(1)
    page.get_by_role("button", name="用已保存的凭证重新检测").click()
    expect(page.locator("form[data-step=umami] .result.ok")).to_be_visible()
    expect(page.locator("form[data-step=umami] button[data-recheck]")).to_be_enabled()

    # ---- 退出
    page.get_by_role("button", name="退出").click()
    expect(page.get_by_text("你已退出登录。")).to_be_visible()
    expect(page.get_by_role("link", name="用 Pocket ID 登录")).to_be_visible()
    assert httpx.get(f"{app_url}/api/v1/sites").status_code == 401
