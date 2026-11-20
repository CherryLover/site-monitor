// ============================================================================
//  app —— 启动 + 路由
//
//  启动顺序：拿当前用户 → 拿引导进度 → 没配完就强制进 /setup → 渲染当前路由。
//  路由只有三个，用 history API；站点切换通过 ?site= 参数。
// ============================================================================
import { api, esc } from "/static/ui.js";
import * as dashboard from "/static/views/dashboard.js";
import * as settings from "/static/views/settings.js";
import * as setup from "/static/views/setup.js";

const VIEWS = { "/": dashboard, "/setup": setup, "/settings": settings };
const root = document.getElementById("root");

export const state = { me: null, status: null, sites: [] };

export function go(url) {
  history.pushState(null, "", url);
  render();
}

export async function refreshStatus() {
  state.status = await api("/api/v1/setup/status");
  state.sites = state.status.complete ? await api("/api/v1/sites") : [];
  return state.status;
}

export function currentSite() {
  const want = new URLSearchParams(location.search).get("site");
  return state.sites.find((s) => s.domain === want) || state.sites[0] || null;
}

function chrome() {
  const here = location.pathname;
  document.querySelectorAll("[data-nav]").forEach((a) => {
    a.toggleAttribute("aria-current", a.dataset.nav === here);
    if (a.dataset.nav === here) a.setAttribute("aria-current", "page");
  });
  const site = currentSite();
  document.getElementById("site-tabs").innerHTML = here === "/" ? state.sites.map((s) =>
    `<a href="/?site=${encodeURIComponent(s.domain)}" ${s === site ? 'aria-current="page"' : ""}>${esc(s.domain)}</a>`).join("") : "";
  document.getElementById("user").innerHTML = state.me ? `
    <span>${esc(state.me.name || state.me.email)}</span>
    <form method="post" action="/auth/logout"><button class="btn" type="submit">退出</button></form>` : "";
}

async function render() {
  if (state.status && !state.status.complete && location.pathname !== "/setup") {
    history.replaceState(null, "", "/setup");
  }
  chrome();
  const view = VIEWS[location.pathname] || dashboard;
  try {
    await view.render(root);
  } catch (e) {
    root.innerHTML = `<section class="card"><h2>出错了</h2><p class="bad">${esc(e.message)}</p>
      <p class="muted small">如果是上游服务的问题，可以去「设置」里重新检测凭证。</p></section>`;
  }
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='/']");
  if (!a || a.target || e.metaKey || e.ctrlKey || a.getAttribute("href").startsWith("/auth") ||
      a.getAttribute("href").startsWith("/api")) return;
  e.preventDefault();
  go(a.getAttribute("href"));
});
window.addEventListener("popstate", render);

(async () => {
  state.me = await api("/api/v1/me");
  await refreshStatus();
  await render();
})();
