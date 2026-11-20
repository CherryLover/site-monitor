// ============================================================================
//  settings —— 左侧二级菜单 + 右侧单一面板
//
//    数据源   Umami / Google Service Account / Google API Key（复用引导流程的凭证表单）
//    监控     站点：多维表格，单元格内直接编辑，拖拽手柄排序（序号和全站顺序都跟着变）
//    访问     API Token：表格
//
//  地址即状态：/settings/<section>，刷新和分享都能回到同一块。
// ============================================================================
import { chrome, go, refreshStatus, state } from "/static/app.js";
import { api, esc, toast, when } from "/static/ui.js";
import { credentialStep } from "/static/views/setup.js";

const MENU = [
  { group: "数据源", items: [
    { id: "umami", title: "Umami", desc: "站内行为数据：访问、事件、来源。查询时实时读取。" },
    { id: "google_sa", title: "Google Service Account", desc: "读取 Search Console：搜索词、排名、收录状态。只申请只读权限。" },
    { id: "google_key", title: "Google API Key", desc: "PageSpeed Insights 与 Chrome UX Report，用于性能测试。" },
  ] },
  { group: "监控", items: [{ id: "sites", title: "站点", desc: "要监控的网站。拖动左侧手柄调整顺序，概览页的顺序和序号随之改变。" }] },
  { group: "访问", items: [{ id: "tokens", title: "API Token", desc: "给 AI skill 或脚本用。Token 只能读数据、触发同步、写变更日志，不能改凭证或创建新 Token。" }] },
];
const ITEMS = MENU.flatMap((g) => g.items);
const CRED = new Set(["umami", "google_sa", "google_key"]);

export async function render(root) {
  const id = location.pathname.split("/")[2] || "sites";
  const item = ITEMS.find((i) => i.id === id);
  if (!item) return go("/settings/sites");
  const status = await refreshStatus();
  const dot = (sid) => {
    const st = status.steps.find((s) => s.id === sid);
    if (!st || sid === "sites") return "";
    const cls = st.ok ? "ok" : st.configured ? "err" : "off";
    return `<span class="dot ${cls}" role="img" aria-label="${st.ok ? "已通过校验" : st.configured ? "需要重新检测" : "未配置"}"></span>`;
  };
  root.innerHTML = `
    <div class="settings-shell">
      <nav class="side-nav" aria-label="设置">
        ${MENU.map((g) => `<div class="side-group"><span class="side-label">${g.group}</span>
          ${g.items.map((i) => `<a href="/settings/${i.id}" ${i.id === id ? 'aria-current="page"' : ""}>
            <span>${i.title}</span>${dot(i.id)}</a>`).join("")}</div>`).join("")}
      </nav>
      <section class="settings-main" aria-labelledby="panel-title">
        <header class="panel-head"><h1 id="panel-title">${item.title}</h1><p class="muted">${item.desc}</p></header>
        <div id="panel"></div>
      </section>
    </div>`;
  const panel = root.querySelector("#panel");
  if (CRED.has(id)) return credentialPanel(panel, id, status);
  if (id === "sites") return sitesPanel(panel);
  return tokensPanel(panel);
}

// ---------------------------------------------------------------- 凭证
async function credentialPanel(panel, id, status) {
  const cred = await api("/api/v1/setup/credentials");
  const st = status.steps.find((s) => s.id === id);
  const f = credentialStep(id, cred, st.configured ? { ok: st.ok, detail: st.detail } : null, {
    onSaved: async () => {
      await refreshStatus();
      render(document.getElementById("root"));
    },
  });
  panel.innerHTML = `<p class="small muted">${st.checked_at ? `最近一次检测：${when(st.checked_at)}` : "尚未配置"}</p>${f.html}`;
  f.bind(panel);
}

// ---------------------------------------------------------------- 站点：多维表格
const JOB_LABEL = { gsc: "搜索", inspections: "收录", pagespeed: "PSI" };

function jobChip(kind, j) {
  const cls = j.running ? "run" : j.ok === false ? "err" : j.last_ok ? "ok" : "off";
  const tip = j.running ? "同步中" : j.ok === false ? `失败：${j.detail || ""}` : `成功于 ${when(j.last_ok)}`;
  return `<span class="chip ${cls}" title="${esc(tip)}"><span class="dot ${cls}"></span>${JOB_LABEL[kind]}</span>`;
}

const source = (name, id) => (id
  ? `<span class="chip ok" title="${esc(id)}"><span class="dot ok"></span>${name}</span>`
  : `<span class="chip off" title="未关联"><span class="dot off"></span>${name}</span>`);

const siteRow = (s) => `
  <tr data-domain="${esc(s.domain)}">
    <td class="drag-cell"><span class="drag" role="button" tabindex="0" draggable="true"
      aria-label="调整 ${esc(s.domain)} 的顺序：拖动，或用上下方向键">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/>
        <circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg></span></td>
    <td class="no"><span class="site-no">${s.number}</span></td>
    <th scope="row"><a href="/?site=${encodeURIComponent(s.domain)}">${esc(s.domain)}</a></th>
    <td><span class="chips">${source("GSC", s.gsc_property)}${source("Umami", s.umami_website_id)}</span></td>
    <td><input class="cell-input" data-field="brand_terms" aria-label="${esc(s.domain)} 的品牌词"
      value="${esc(s.brand_terms.join(", "))}" placeholder="逗号分隔"></td>
    <td><input class="cell-input" data-field="funnel_events" aria-label="${esc(s.domain)} 的漏斗事件"
      value="${esc(s.funnel_events.join(", "))}" placeholder="自动"></td>
    <td><span class="chips">${Object.entries(s.jobs).map(([k, j]) => jobChip(k, j)).join("")}</span></td>
    <td class="actions"><button class="btn sm" type="button" data-sync>同步</button>
      <button class="btn sm danger" type="button" data-del>移除</button></td>
  </tr>`;

async function sitesPanel(panel) {
  const sites = await api("/api/v1/sites");
  panel.innerHTML = `
    <div class="card flush">
      <div class="card-head"><span class="muted small">${sites.length} 个站点 · 品牌词和漏斗事件直接在格子里改，回车或离开格子即保存；漏斗事件留空时自动取最常见的 4 个</span>
        <a class="btn primary" href="/setup?step=sites">添加站点</a></div>
      <div class="scroll-x"><table class="grid-table" id="sites-table">
        <thead><tr><th class="drag-cell"><span class="sr-only">排序</span></th><th scope="col">#</th><th scope="col">站点</th>
          <th scope="col">数据源</th><th scope="col">品牌词</th>
          <th scope="col">漏斗事件</th><th scope="col">同步状态</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
        <tbody>${sites.map(siteRow).join("") || '<tr><td colspan="8" class="empty">还没有监控站点</td></tr>'}</tbody>
      </table></div>
    </div>`;
  const tbody = panel.querySelector("tbody");
  bindCells(tbody);
  bindRowActions(panel, tbody);
  bindDrag(tbody);
  if (sites.some((s) => Object.values(s.jobs).some((j) => j.running))) refreshJobs(tbody);
}

function bindCells(tbody) {
  tbody.querySelectorAll(".cell-input").forEach((input) => {
    input.dataset.saved = input.value;
    const save = async () => {
      if (input.value === input.dataset.saved) return;
      const domain = input.closest("tr").dataset.domain;
      const list = input.value.split(",").map((x) => x.trim()).filter(Boolean);
      input.classList.remove("saved", "error");
      input.classList.add("saving");
      try {
        const s = await api(`/api/v1/sites/${encodeURIComponent(domain)}`, {
          method: "PATCH", body: { [input.dataset.field]: list } });
        input.value = s[input.dataset.field].join(", ");
        input.dataset.saved = input.value;
        input.classList.replace("saving", "saved");
        setTimeout(() => input.classList.remove("saved"), 1500);
      } catch (e) {
        input.classList.replace("saving", "error");
        toast(`保存失败：${e.message}`, "error");
      }
    };
    input.addEventListener("blur", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") input.blur();
      if (e.key === "Escape") {
        input.value = input.dataset.saved;
        input.blur();
      }
    });
  });
}

function bindRowActions(panel, tbody) {
  tbody.addEventListener("click", async (e) => {
    const tr = e.target.closest("tr[data-domain]");
    if (!tr) return;
    const d = encodeURIComponent(tr.dataset.domain);
    if (e.target.closest("[data-sync]")) {
      await api(`/api/v1/sites/${d}/sync`, { method: "POST" });
      toast(`${tr.dataset.domain} 已开始同步`);
      return refreshJobs(tbody);
    }
    if (e.target.closest("[data-cancel]")) {
      const b = tr.querySelector("[data-del]");
      delete b.dataset.confirm;
      b.textContent = "移除";
      return e.target.closest("[data-cancel]").remove();
    }
    const del = e.target.closest("[data-del]");
    if (!del) return;
    if (!del.dataset.confirm) {
      del.dataset.confirm = "1";
      del.textContent = "确认移除";
      del.insertAdjacentHTML("afterend", '<button class="btn sm" type="button" data-cancel>取消</button>');
      return;
    }
    await api(`/api/v1/sites/${d}`, { method: "DELETE" });
    toast(`已移除 ${tr.dataset.domain}，同步过的数据一并删除`);
    await refreshStatus();
    sitesPanel(panel);
  });
}

async function refreshJobs(tbody) {
  if (!tbody.isConnected) return;
  const sites = await api("/api/v1/sites");
  for (const s of sites) {
    const cell = tbody.querySelector(`tr[data-domain="${CSS.escape(s.domain)}"] .chips`);
    if (cell) cell.innerHTML = Object.entries(s.jobs).map(([k, j]) => jobChip(k, j)).join("");
  }
  if (sites.some((s) => Object.values(s.jobs).some((j) => j.running))) setTimeout(() => refreshJobs(tbody), 4000);
}

// ---------------------------------------------------------------- 拖拽排序
function bindDrag(tbody) {
  let dragging = null;
  const order = () => [...tbody.querySelectorAll("tr[data-domain]")].map((tr) => tr.dataset.domain);
  let saved = order();

  const commit = async () => {
    const now = order();
    if (now.join() === saved.join()) return;
    try {
      state.sites = await api("/api/v1/sites/order", { method: "PUT", body: { domains: now } });
      saved = now;
      for (const s of state.sites) {
        const no = tbody.querySelector(`tr[data-domain="${CSS.escape(s.domain)}"] .site-no`);
        if (no) no.textContent = s.number;
      }
      chrome();
      toast("顺序已保存");
    } catch (e) {
      toast(`排序保存失败：${e.message}`, "error");
    }
  };

  tbody.addEventListener("dragstart", (e) => {
    const handle = e.target.closest(".drag");
    if (!handle) return;
    dragging = handle.closest("tr");
    dragging.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragging.dataset.domain);
    e.dataTransfer.setDragImage(dragging, 20, 20);
  });
  tbody.addEventListener("dragover", (e) => {
    if (!dragging) return;
    e.preventDefault();
    const over = e.target.closest("tr[data-domain]");
    if (!over || over === dragging) return;
    const r = over.getBoundingClientRect();
    tbody.insertBefore(dragging, e.clientY < r.top + r.height / 2 ? over : over.nextSibling);
  });
  tbody.addEventListener("drop", (e) => e.preventDefault());
  tbody.addEventListener("dragend", () => {
    dragging?.classList.remove("dragging");
    dragging = null;
    commit();
  });
  tbody.addEventListener("keydown", (e) => {
    const handle = e.target.closest(".drag");
    if (!handle || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const tr = handle.closest("tr");
    const sib = e.key === "ArrowUp" ? tr.previousElementSibling : tr.nextElementSibling;
    if (!sib) return;
    tbody.insertBefore(tr, e.key === "ArrowUp" ? sib : sib.nextSibling);
    handle.focus();
    commit();
  });
}

// ---------------------------------------------------------------- API Token
const tokenRow = (t) => `
  <tr class="${t.revoked_at ? "dim" : ""}">
    <th scope="row">${esc(t.name)}</th>
    <td class="mono small">${esc(t.prefix)}…</td>
    <td class="small">${esc(t.created_by)}</td>
    <td class="small">${when(t.created_at)}</td>
    <td class="small">${when(t.last_used_at)}</td>
    <td>${t.revoked_at ? '<span class="chip off"><span class="dot off"></span>已吊销</span>'
      : '<span class="chip ok"><span class="dot ok"></span>有效</span>'}</td>
    <td class="actions">${t.revoked_at ? "" : `<button class="btn sm danger" type="button" data-revoke="${t.id}">吊销</button>`}</td>
  </tr>`;

async function tokensPanel(panel) {
  panel.innerHTML = `
    <div class="card flush">
      <div class="card-head">
        <form class="row" id="token-form">
          <label class="inline-label" for="token-name">名称</label>
          <input id="token-name" name="name" required placeholder="claude-skill" class="w-200">
          <button class="btn primary" type="submit">创建 Token</button>
        </form>
        <span class="muted small mono">curl -H "Authorization: Bearer smk_…" ${esc(location.origin)}/api/v1/sites</span>
      </div>
      <div id="new-token"></div>
      <div class="scroll-x"><table class="grid-table">
        <thead><tr><th scope="col">名称</th><th scope="col">前缀</th><th scope="col">创建人</th><th scope="col">创建时间</th>
          <th scope="col">最近使用</th><th scope="col">状态</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
        <tbody id="token-list"></tbody>
      </table></div>
    </div>`;
  const list = panel.querySelector("#token-list");
  const load = async () => {
    const tokens = await api("/api/v1/tokens");
    list.innerHTML = tokens.map(tokenRow).join("") || '<tr><td colspan="7" class="empty">还没有 Token</td></tr>';
  };
  await load();
  panel.querySelector("#token-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const t = await api("/api/v1/tokens", { method: "POST", body: { name: form.name.value } });
    panel.querySelector("#new-token").innerHTML = `
      <div class="reveal">
        <b>Token 已创建，只显示这一次，请立即复制保存：</b>
        <div class="secret-box" id="token-value">${esc(t.token)}</div>
        <div class="row"><button class="btn" type="button" id="copy">复制</button></div>
      </div>`;
    panel.querySelector("#copy").addEventListener("click", () =>
      navigator.clipboard.writeText(t.token).then(() => toast("已复制"), () => toast("复制失败，请手动选中复制", "error")));
    form.reset();
    load();
  });
  list.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-revoke]");
    if (!b) return;
    if (!b.dataset.confirm) {
      b.dataset.confirm = "1";
      b.textContent = "确认吊销";
      return;
    }
    await api(`/api/v1/tokens/${b.dataset.revoke}`, { method: "DELETE" });
    toast("已吊销");
    load();
  });
}
