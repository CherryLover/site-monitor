// ============================================================================
//  dashboard —— 单站点概览（对应设计稿）
//
//  页面只搭一次骨架，之后一律局部刷新：每个区块自己调 JSON 接口、只重绘自己。
//    切换时间范围 → 只刷新依赖范围的「指标」「漏斗」
//    同步进行中   → 每 4 秒拉一次站点状态；某类作业跑完，只重绘依赖它的区块
//  不再有任何 location.reload：滚动位置、展开的对话框、筛选状态都不会丢。
// ============================================================================
import { currentSite, go, state } from "/static/app.js";
import { api, delta, esc, num, path, shortDate, stackBar, toast, trendChart, when } from "/static/ui.js";

const RANGES = [["7d", "7 天"], ["28d", "28 天"], ["8w", "8 周"], ["90d", "90 天"]];
const TYPES = { rank: "排名", cannibal: "抢词", gap: "缺页", market: "市场", index: "收录", bug: "故障", data: "数据" };
const IMPACT_CLS = { 高: "bad", 中: "warn", 低: "flat" };
const KINDS = ["gsc", "inspections", "pagespeed"];
const DEPENDS = { gsc: ["kpis", "trend", "opps", "funnel"], inspections: ["index", "opps"], pagespeed: ["health"] };
const POLL_MS = 4000;
let oppFilter = "all";
let board = null;

const param = (k, d) => new URLSearchParams(location.search).get(k) || d;

export function stop() {
  board?.stop();
  board = null;
}

export async function render(root) {
  board?.stop();
  const site = currentSite();
  if (!site) return go("/setup");
  board = new Board(root, site);
  await board.mount();
}

class Board {
  constructor(root, site) {
    this.root = root;
    this.site = site;
    this.range = param("range", "28d");
    this.base = `/api/v1/sites/${encodeURIComponent(site.domain)}`;
    this.alive = true;
    this.timer = null;
    this.gscThrough = null;
  }

  stop() {
    this.alive = false;
    clearTimeout(this.timer);
  }

  $(sel) {
    return this.root.querySelector(sel);
  }

  async mount() {
    this.root.innerHTML = `
      <div class="row between">
        <div><h1><span class="site-no">${this.site.number}</span>${esc(this.site.domain)}</h1>
          <p class="muted small" id="fresh"></p></div>
        <div class="row">
          <div class="segmented" role="group" aria-label="时间范围">${RANGES.map(([id, l]) =>
            `<button class="pill" type="button" data-range="${id}" aria-pressed="${id === this.range}">${l}</button>`).join("")}</div>
          <button class="btn" type="button" id="sync"></button>
        </div>
      </div>
      <div id="sync-note"></div>
      <section class="kpis" id="kpis" aria-label="核心指标"><div class="kpi"><span class="muted">加载中…</span></div></section>
      <section class="grid2">
        <div class="card wide" id="trend"></div>
        <div class="card" id="mix"></div>
      </section>
      <section class="card flush" id="opps"></section>
      <section class="card flush" id="funnel"></section>
      <section class="grid2"><div class="card" id="index"></div><div class="card" id="health"></div></section>
      <section class="card flush" id="changes"></section>
      ${apiCard(this.site.domain)}`;
    this.root.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => this.setRange(b.dataset.range)));
    this.$("#sync").addEventListener("click", () => this.sync());
    this.paintStatus();
    await this.refresh(["kpis", "trend", "opps", "funnel", "index", "health", "changes"]);
    if (this.syncing()) this.watch();
  }

  syncing() {
    return KINDS.some((k) => this.site.jobs[k].running);
  }

  setRange(id) {
    this.range = id;
    history.replaceState(null, "", `/?site=${encodeURIComponent(this.site.domain)}&range=${id}`);
    this.root.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.range === id));
    this.refresh(["kpis", "funnel"]);
  }

  async refresh(names) {
    const loaders = { kpis, trend, opps, funnel, index: indexStatus, health, changes };
    await Promise.all([...new Set(names)].map((n) => loaders[n](this).catch((e) => this.fail(n, e))));
  }

  fail(name, e) {
    const el = this.$(`#${name}`);
    if (el) el.innerHTML = `<div class="${el.classList.contains("flush") ? "empty" : ""}"><p class="bad">这一块加载失败：${esc(e.message)}</p></div>`;
  }

  paintStatus() {
    const j = this.site.jobs;
    const syncing = this.syncing();
    this.$("#fresh").textContent = [
      `搜索数据 ${when(j.gsc.last_ok)}`, `收录巡检 ${when(j.inspections.last_ok)}`,
      `PageSpeed ${when(j.pagespeed.last_ok)}`, "Umami 实时",
      this.gscThrough ? `GSC 数据截至 ${shortDate(this.gscThrough)}` : null,
    ].filter(Boolean).join(" · ");
    const btn = this.$("#sync");
    btn.disabled = syncing;
    btn.innerHTML = syncing ? '<span class="spin" aria-hidden="true"></span>同步中' : "立即同步";
    const running = KINDS.filter((k) => j[k].running).map((k) => ({ gsc: "搜索数据", inspections: "收录巡检", pagespeed: "PageSpeed" })[k]);
    const note = !j.gsc.last_ok && !syncing
      ? `<div class="callout" role="status">还没有成功同步过搜索数据。${esc(j.gsc.detail || "")}</div>`
      : syncing ? `<div class="callout" role="status">正在同步：${running.join("、")}。完成的部分会自动更新，不用刷新页面。</div>` : "";
    this.$("#sync-note").innerHTML = note;
  }

  async sync() {
    await api(`${this.base}/sync`, { method: "POST" });
    toast("已开始同步：搜索数据 → 收录巡检 → PageSpeed");
    this.site = await api(this.base);
    this.paintStatus();
    this.watch();
  }

  watch() {
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      if (!this.alive) return;
      const prev = this.site;
      try {
        this.site = await api(this.base);
      } catch {
        return this.watch();
      }
      if (!this.alive) return;
      const i = state.sites.findIndex((s) => s.domain === this.site.domain);
      if (i >= 0) state.sites[i] = this.site;
      const done = KINDS.filter((k) => prev.jobs[k].last_ok !== this.site.jobs[k].last_ok
        || (prev.jobs[k].running && !this.site.jobs[k].running));
      this.paintStatus();
      if (done.length) await this.refresh(done.flatMap((k) => DEPENDS[k]));
      if (this.syncing()) this.watch();
    }, POLL_MS);
  }
}

// ---------------------------------------------------------------- 指标
async function kpis(b) {
  const s = await api(`${b.base}/summary?range=${b.range}`);
  const c = s.search.current, p = s.search.previous;
  const t = s.traffic.current || {}, tp = s.traffic.previous || {};
  const failSum = (ev) => Object.entries(ev || {}).filter(([k]) => /fail|error/i.test(k)).reduce((a, [, v]) => a + v, 0);
  const posDelta = c.nonbrand.position && p.nonbrand.position ? p.nonbrand.position - c.nonbrand.position : null;
  const cards = [
    ["非品牌点击", num(c.nonbrand.clicks), delta(c.nonbrand.clicks, p.nonbrand.clicks), `上期 ${num(p.nonbrand.clicks)} · SEO 带来的新用户`, "GSC"],
    ["非品牌展示", num(c.nonbrand.impressions), delta(c.nonbrand.impressions, p.nonbrand.impressions), `上期 ${num(p.nonbrand.impressions)}`, "GSC"],
    ["非品牌平均排名", c.nonbrand.position ?? "—",
      posDelta === null ? { text: "—", cls: "flat" } : { text: `${posDelta >= 0 ? "↑" : "↓"} ${Math.abs(posDelta).toFixed(1)}`, cls: posDelta >= 0 ? "good" : "bad" },
      `上期 ${p.nonbrand.position ?? "—"}${c.nonbrand.impressions < 50 ? " · 样本不足 50" : ""}`, "GSC"],
    ["品牌点击", num(c.brand.clicks), delta(c.brand.clicks, p.brand.clicks), `上期 ${num(p.brand.clicks)} · 搜品牌名的老用户`, "GSC"],
    ["访客", num(t.visitors), delta(t.visitors, tp.visitors), `上期 ${num(tp.visitors)} · 只统计正式域名`, "Umami"],
    ["失败事件", num(failSum(t.events)), delta(failSum(t.events), failSum(tp.events), { invert: true }), `上期 ${num(failSum(tp.events))}`, "Umami"],
  ];
  b.$("#kpis").innerHTML = cards.map(([label, v, d, note, src]) => `
    <div class="kpi"><div class="top"><span>${label}</span><span class="badge ${src.toLowerCase()}">${src}</span></div>
      <div class="val"><b>${esc(v)}</b><span class="${d.cls}">${esc(d.text)}</span></div>
      <span class="muted small">${esc(note)}</span></div>`).join("");
  b.gscThrough = s.gsc_through;
  b.paintStatus();
}

// ---------------------------------------------------------------- 趋势与构成
async function trend(b) {
  const weeks = await api(`${b.base}/trend?weeks=8`);
  b.$("#trend").innerHTML = `
    <div class="row between"><h2>搜索展示与非品牌排名 · 近 8 周</h2>
      <div class="legend"><span><i style="background:var(--accent)"></i>非品牌展示</span><span><i style="background:var(--brand)"></i>品牌展示</span>
        <span><i style="background:var(--anon)"></i>匿名长尾</span><span><i class="line"></i>非品牌平均排名</span></div></div>
    ${trendChart(weeks)}
    <p class="muted small">右轴是排名，越靠上越好。匿名长尾是 Google 不公开具体搜索词的部分。</p>`;
  const sum = (k, f) => weeks.reduce((a, w) => a + w[k][f], 0);
  const parts = [
    { label: "品牌词", value: sum("brand", "clicks"), cls: "brand" },
    { label: "非品牌词", value: sum("nonbrand", "clicks"), cls: "non" },
    { label: "匿名长尾", value: sum("anonymous", "clicks"), cls: "anon" },
  ];
  const total = parts.reduce((a, p) => a + p.value, 0);
  b.$("#mix").innerHTML = `
    <h2>点击从哪来 · 近 8 周</h2>${stackBar(parts)}
    ${parts.map((p) => `<div class="row"><i class="seg ${p.cls}" style="width:10px;height:10px;border-radius:2px;display:inline-block"></i>
      <span class="spacer">${p.label}</span><span class="mono">${num(p.value)}</span>
      <span class="mono muted" style="width:44px;text-align:right">${total ? Math.round((p.value / total) * 100) : 0}%</span></div>`).join("")}
    <p class="muted small">非品牌点击才是 SEO 的成果。品牌点击来自已经认识你的人。</p>`;
}

// ---------------------------------------------------------------- 机会清单
async function opps(b) {
  const base = b.base;
  const items = await api(`${base}/opportunities`);
  const el = b.$("#opps");
  const draw = () => {
    const shown = items.filter((o) => oppFilter === "all" || o.type === oppFilter);
    const open = items.filter((o) => o.status === "open").length;
    el.innerHTML = `
      <div class="card-head"><div><h2>机会清单</h2><p class="muted small">按规则从最近 28 天的数据生成 · 待处理 ${open} / 共 ${items.length}</p></div>
        <div class="row" role="group" aria-label="按类型筛选">${[["all", "全部"], ...Object.entries(TYPES)].map(([id, l]) => {
          const n = id === "all" ? items.length : items.filter((o) => o.type === id).length;
          return n || id === "all" ? `<button class="pill" type="button" data-f="${id}" aria-pressed="${oppFilter === id}">${l} <span class="mono">${n}</span></button>` : "";
        }).join("")}</div></div>
      <div class="list">${shown.length ? shown.map((o) => `
        <div class="item ${o.status !== "open" ? "dim" : ""}">
          <div style="flex:0 0 52px"><span class="tag ${o.type}">${TYPES[o.type]}</span></div>
          <div class="t"><b>${esc(o.target)}</b><span class="ev">${esc(o.evidence)}</span></div>
          <div class="a">${esc(o.action)}</div>
          <div class="row"><span class="small ${IMPACT_CLS[o.impact]}"><b>影响 ${o.impact}</b></span>
            ${o.status === "open"
              ? `<button class="btn" type="button" data-k="${esc(o.key)}" data-s="done">标记已处理</button>
                 <button class="btn" type="button" data-k="${esc(o.key)}" data-s="ignored">忽略</button>`
              : `<button class="btn" type="button" data-k="${esc(o.key)}" data-s="open">${o.status === "done" ? "已处理" : "已忽略"} · 撤销</button>`}
          </div></div>`).join("") : '<div class="empty">没有符合条件的机会。数据量太小时规则不会触发，这是正常的。</div>'}</div>`;
    el.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { oppFilter = b.dataset.f; draw(); }));
    el.querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", async () => {
      await api(`${base}/opportunities`, { method: "PATCH", body: { key: b.dataset.k, status: b.dataset.s } });
      items.find((o) => o.key === b.dataset.k).status = b.dataset.s;
      draw();
    }));
  };
  draw();
}

// ---------------------------------------------------------------- 漏斗
async function funnel(b) {
  const f = await api(`${b.base}/pages?range=${b.range}`);
  b.$("#funnel").innerHTML = `
    <div class="card-head"><div><h2>落地页漏斗</h2>
      <p class="muted small">左边来自 Search Console，右边来自 Umami，按 URL 路径拼接。事件数可以多于访问数，因为同一访客会重复操作。</p></div></div>
    <div class="scroll-x"><table>
      <thead><tr><th scope="col">落地页</th><th class="gsc-col" scope="col">展示</th><th class="gsc-col" scope="col">排名</th>
        <th class="gsc-col" scope="col">点击</th><th scope="col">访问</th>${f.events.map((e) => `<th scope="col">${esc(e)}</th>`).join("")}<th scope="col">失败</th></tr></thead>
      <tbody>${f.rows.map((r) => `<tr><th scope="row">${esc(r.path)}</th><td class="gsc-col">${num(r.impressions)}</td>
        <td class="gsc-col ${r.position > 20 ? "warn" : ""}">${r.position}</td><td class="gsc-col">${num(r.clicks)}</td><td>${num(r.visits)}</td>
        ${f.events.map((e) => `<td>${num(r.events[e])}</td>`).join("")}<td class="${r.failures ? "bad" : ""}">${num(r.failures)}</td></tr>`).join("")
        || `<tr><td colspan="${5 + f.events.length}" class="empty">还没有搜索数据</td></tr>`}</tbody>
    </table></div>`;
}

// ---------------------------------------------------------------- 收录 / 健康
async function indexStatus(b) {
  const s = await api(`${b.base}/index-status`);
  b.$("#index").innerHTML = `
    <div class="row between"><h2>收录状态</h2><span class="muted small">巡检于 ${when(s.checked_at)}</span></div>
    ${s.total ? `<div class="row"><span class="mono" style="font-size:28px">${s.indexed}<span class="muted" style="font-size:18px"> / ${s.total}</span></span>
      <span class="muted">sitemap URL 已收录</span></div>
      ${stackBar([{ label: "已收录", value: s.indexed, cls: "ok" }, { label: "未收录", value: s.total - s.indexed, cls: "rest" }])}
      ${s.problems.map((p) => `<div class="row between small"><span class="mono">${esc(path(p.url))}</span><span class="warn">${esc(p.coverage)}</span></div>`).join("")}`
      : '<p class="muted">还没有巡检结果。</p>'}
    <p class="muted small">以逐个 URL 的 Inspection 结果为准，不用 sitemap 接口的 indexed 字段（Google 已不维护）。</p>`;
}

async function health(b) {
  const h = await api(`${b.base}/health`);
  const ps = (strategy) => h.pagespeed.filter((r) => r.strategy === strategy && path(r.url) === "/")[0] || h.pagespeed.find((r) => r.strategy === strategy);
  const card = (label, v, note, cls = "") => `<div class="kpi" style="padding:12px 14px"><span class="muted small">${label}</span>
    <b class="mono ${cls}" style="font-size:22px;font-weight:500">${esc(v)}</b><span class="muted small">${esc(note)}</span></div>`;
  const m = ps("mobile"), d = ps("desktop");
  const fails = Object.entries(h.fail_events_28d);
  b.$("#health").innerHTML = `
    <h2>站点健康</h2>
    <div class="kpis" style="grid-template-columns:repeat(2,minmax(0,1fr))">
      ${card("PageSpeed 手机", m?.performance ?? "—", m ? `LCP ${(m.lcp_ms / 1000).toFixed(1)} s` : "未测试", m && m.performance < 90 ? "warn" : "")}
      ${card("PageSpeed 桌面", d?.performance ?? "—", d ? `LCP ${(d.lcp_ms / 1000).toFixed(1)} s` : "未测试")}
      ${card("SEO 得分", m?.seo ?? "—", "Lighthouse")}
      ${card("失败事件 · 28 天", fails.reduce((a, [, v]) => a + v, 0), fails.map(([k, v]) => `${k} ${v}`).join(" · ") || "无", fails.length ? "bad" : "")}
    </div>`;
}

// ---------------------------------------------------------------- 变更日志
async function changes(b) {
  const base = b.base;
  const list = await api(`${base}/changelog`);
  const el = b.$("#changes");
  el.innerHTML = `
    <div class="card-head"><div><h2>变更日志</h2><p class="muted small">每次改动记一条，几周后对照数据才知道改动有没有用。也可以通过 API 写入。</p></div>
      <button class="btn" type="button" id="add-change">添加记录</button></div>
    <div class="list">${list.map((c) => `<div class="item"><span class="mono small muted" style="flex:0 0 90px">${esc(c.date)}</span>
      <span class="t">${esc(c.summary)}${c.page ? ` <span class="mono small muted">${esc(c.page)}</span>` : ""}</span>
      <span class="small muted">${esc(c.source)}</span></div>`).join("") || '<div class="empty">还没有记录</div>'}</div>
    <dialog id="change-dialog"><form method="dialog" class="card" style="border:0;padding:0">
      <h2>添加变更记录</h2>
      <label>日期<input name="date" type="date" value="${new Date().toISOString().slice(0, 10)}" required></label>
      <label>页面（可选）<input name="page" placeholder="/pdf-to-markdown/"></label>
      <label>改了什么<input name="summary" required placeholder="首页 title 前置 Office to Markdown"></label>
      <div class="row"><button class="btn primary" value="save">保存</button><button class="btn" value="cancel" formnovalidate>取消</button></div>
    </form></dialog>`;
  const dlg = el.querySelector("#change-dialog");
  el.querySelector("#add-change").addEventListener("click", () => dlg.showModal());
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "save") return;
    const body = Object.fromEntries(new FormData(dlg.querySelector("form")));
    await api(`${base}/changelog`, { method: "POST", body: { ...body, page: body.page || null } });
    toast("已记录");
    changes(b);
  });
}

// ---------------------------------------------------------------- API 卡
function apiCard(domain) {
  const d = encodeURIComponent(domain);
  const eps = [
    ["GET", "/api/v1/sites", "所有站点与同步状态"],
    ["GET", `/api/v1/sites/${d}/summary?range=7d`, "核心指标与环比"],
    ["GET", `/api/v1/sites/${d}/opportunities`, "机会清单"],
    ["GET", `/api/v1/sites/${d}/queries?min_impressions=10`, "搜索词明细（含每个词的全部落地页）"],
    ["GET", `/api/v1/sites/${d}/pages`, "落地页漏斗"],
    ["GET", `/api/v1/sites/${d}/index-status`, "逐 URL 收录状态"],
    ["GET", `/api/v1/sites/${d}/events?name=convert_fail`, "事件属性分布"],
    ["POST", `/api/v1/sites/${d}/changelog`, "记录一次改动"],
  ];
  return `<section class="api-card" id="api">
    <div style="flex:1 1 300px;display:flex;flex-direction:column;gap:10px">
      <h2>API · 给 AI 用</h2>
      <p class="muted">页面上的每一块都来自这些接口，接口还返回页面放不下的明细。在「设置」里创建 Token，注册成 skill 后就能在对话里直接问。</p>
      <p class="muted small">鉴权：<span class="mono" style="color:#fff">Authorization: Bearer smk_…</span></p>
      <div class="row"><a class="btn" href="/settings/tokens">管理 Token</a><a class="btn" href="/api/docs" target="_blank" rel="noopener">完整接口文档</a></div>
    </div>
    <div class="eps">${eps.map(([m, p, desc]) => `<div class="ep"><span class="m ${m.toLowerCase()}">${m}</span>
      <span class="p">${esc(p)}</span><span class="d">${desc}</span></div>`).join("")}</div>
  </section>`;
}
