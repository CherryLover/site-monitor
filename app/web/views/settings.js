// ============================================================================
//  settings —— 凭证、监控站点、API Token
//
//  凭证表单直接复用引导流程的 credentialStep：同一套校验，同一种结果展示。
// ============================================================================
import { refreshStatus } from "/static/app.js";
import { api, esc, toast, when } from "/static/ui.js";
import { credentialStep } from "/static/views/setup.js";

const TITLES = { umami: "Umami", google_sa: "Google Service Account", google_key: "Google API Key" };

export async function render(root) {
  const [cred, status, sites, tokens] = await Promise.all([
    api("/api/v1/setup/credentials"), refreshStatus(), api("/api/v1/sites"), api("/api/v1/tokens")]);
  const forms = ["umami", "google_sa", "google_key"].map((id) => {
    const st = status.steps.find((s) => s.id === id);
    return { id, ...credentialStep(id, cred, st.configured ? { ok: st.ok, detail: st.detail } : null, { onSaved: refreshStatus }) };
  });

  root.innerHTML = `
    <h1>设置</h1>
    <section aria-labelledby="h-cred" style="display:flex;flex-direction:column;gap:12px">
      <h2 id="h-cred">上游凭证</h2>
      <p class="muted small">保存前都会真实调用一次上游校验；凭证加密存储，页面上只显示掩码。登录方式（Pocket ID）通过服务器环境变量配置，不在这里。</p>
      <div class="grid2">${forms.map((f) => `<div style="display:flex;flex-direction:column;gap:8px"><h3>${TITLES[f.id]}</h3>${f.html}</div>`).join("")}</div>
    </section>
    <section class="card flush" aria-labelledby="h-sites">
      <div class="card-head"><div><h2 id="h-sites">监控站点</h2><p class="muted small">品牌词决定哪些搜索算「老用户」；漏斗事件留空时自动取最常见的 4 个。</p></div>
        <a class="btn" href="/setup?step=sites">添加站点</a></div>
      <div class="list">${sites.map(siteRow).join("") || '<div class="empty">还没有监控站点</div>'}</div>
    </section>
    <section class="card flush" id="tokens" aria-labelledby="h-tokens">
      <div class="card-head"><div><h2 id="h-tokens">API Token</h2><p class="muted small">给 AI skill 或脚本用。Token 只能读数据、触发同步、写变更日志，不能改凭证或创建新 Token。</p></div>
        <form class="row" id="token-form"><label style="flex-direction:row;align-items:center;gap:8px">
          <span class="hint">名称</span><input name="name" required placeholder="claude-skill" style="width:180px"></label>
          <button class="btn primary" type="submit">创建 Token</button></form></div>
      <div id="new-token"></div>
      <div class="list" id="token-list">${tokens.map(tokenRow).join("") || '<div class="empty">还没有 Token</div>'}</div>
    </section>`;

  forms.forEach((f) => f.bind(root));
  bindSites(root);
  bindTokens(root);
  if (location.hash === "#tokens") root.querySelector("#tokens").scrollIntoView();
}

// ---------------------------------------------------------------- 站点
const siteRow = (s) => `
  <form class="item site-row" data-domain="${esc(s.domain)}">
    <div class="t"><b>${esc(s.domain)}</b>
      <span class="ev">${s.gsc_property ? esc(s.gsc_property) : "未关联 Search Console"} · Umami ${s.umami_website_id ? "已关联" : "未关联"}</span>
      <span class="small muted">搜索数据 ${when(s.jobs.gsc.last_ok)} · 收录 ${when(s.jobs.inspections.last_ok)} · PageSpeed ${when(s.jobs.pagespeed.last_ok)}</span></div>
    <label class="a" style="font-weight:500">品牌词<input name="brand_terms" value="${esc(s.brand_terms.join(", "))}"></label>
    <label class="a" style="font-weight:500">漏斗事件<input name="funnel_events" value="${esc(s.funnel_events.join(", "))}" placeholder="自动"></label>
    <div class="row"><button class="btn" type="submit">保存</button>
      <button class="btn" type="button" data-sync>同步</button>
      <button class="btn danger" type="button" data-del>移除</button></div>
  </form>`;

function bindSites(root) {
  root.querySelectorAll(".site-row").forEach((form) => {
    const d = encodeURIComponent(form.dataset.domain);
    const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      await api(`/api/v1/sites/${d}`, { method: "PATCH",
        body: { brand_terms: list(form.brand_terms.value), funnel_events: list(form.funnel_events.value) } });
      toast("已保存");
    });
    form.querySelector("[data-sync]").addEventListener("click", async () => {
      await api(`/api/v1/sites/${d}/sync`, { method: "POST" });
      toast("已开始同步");
    });
    form.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm(`移除 ${form.dataset.domain}？已同步的数据会一起删除。`)) return;
      await api(`/api/v1/sites/${d}`, { method: "DELETE" });
      await refreshStatus();
      render(root);
    });
  });
}

// ---------------------------------------------------------------- Token
const tokenRow = (t) => `
  <div class="item ${t.revoked_at ? "dim" : ""}">
    <div class="t"><b>${esc(t.name)}</b><span class="ev">${esc(t.prefix)}… · ${esc(t.created_by)}</span></div>
    <span class="a small muted">创建于 ${when(t.created_at)} · 最近使用 ${when(t.last_used_at)}</span>
    ${t.revoked_at ? '<span class="small muted">已吊销</span>' : `<button class="btn danger" type="button" data-revoke="${t.id}">吊销</button>`}
  </div>`;

function bindTokens(root) {
  root.querySelector("#token-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const t = await api("/api/v1/tokens", { method: "POST", body: { name: form.name.value } });
    root.querySelector("#new-token").innerHTML = `
      <div class="item" style="flex-direction:column;align-items:stretch;padding:14px 22px;gap:8px">
        <b>Token 已创建，只显示这一次，请立即复制保存：</b>
        <div class="secret-box" id="token-value">${esc(t.token)}</div>
        <div class="row"><button class="btn" type="button" id="copy">复制</button>
          <span class="muted small">示例：curl -H "Authorization: Bearer ${esc(t.prefix)}…" ${esc(location.origin)}/api/v1/sites</span></div>
      </div>`;
    root.querySelector("#copy").addEventListener("click", () => navigator.clipboard.writeText(t.token).then(() => toast("已复制")));
    form.reset();
    refreshTokens(root);
  });
  root.querySelector("#token-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-revoke]");
    if (!b || !confirm("吊销后，使用这个 Token 的程序会立刻失去访问权限。")) return;
    await api(`/api/v1/tokens/${b.dataset.revoke}`, { method: "DELETE" });
    refreshTokens(root);
  });
}

async function refreshTokens(root) {
  root.querySelector("#token-list").innerHTML = (await api("/api/v1/tokens")).map(tokenRow).join("");
}
