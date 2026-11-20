// ============================================================================
//  setup —— 首次登录的引导流程
//
//  四步：Umami → Google Service Account → Google API Key → 监控站点。
//  每一步都是「填写 → 真实调用校验 → 展示结果 → 通过才保存」。
//  凭证表单（credentialStep）也被设置页复用，同一套校验，同一种结果展示。
// ============================================================================
import { currentSite, go, refreshStatus, state } from "/static/app.js";
import { api, esc, toast } from "/static/ui.js";

let active = null;

// ---------------------------------------------------------------- 校验结果展示
export function resultBox(step, res) {
  if (!res) return "";
  const d = res.detail || {};
  if (!res.ok) {
    const extra = step === "google_key" && d.apis ? apiList(d.apis) : "";
    return `<div class="result err" role="alert"><b>校验未通过</b><span>${esc(d.error)}</span>${extra}</div>`;
  }
  let body = "";
  if (step === "umami") {
    body = `<span>已连接，身份：${esc(d.user)}（${esc(d.role)}），能看到 ${d.websites.length} 个网站：</span>
      <ul>${d.websites.map((w) => `<li>${esc(w.name)} · <span class="mono">${esc(w.domain)}</span></li>`).join("")}</ul>`;
  } else if (step === "google_sa") {
    body = `<span>授权成功：<span class="mono">${esc(d.email)}</span>，能访问 ${d.properties.length} 个 Search Console 资源：</span>
      <ul>${d.properties.map((p) => `<li><span class="mono">${esc(p.site)}</span> · ${esc(p.permission)}</li>`).join("")}</ul>`;
  } else if (step === "google_key") {
    body = `<span>API Key 可用：</span>${apiList(d.apis)}`;
  }
  return `<div class="result ok" role="status"><b>校验通过</b>${body}</div>`;
}

const apiList = (apis) =>
  `<ul>${apis.map((a) => `<li>${esc(a.api)}：${a.ok ? "可用" : esc(a.error)}</li>`).join("")}</ul>`;

// ---------------------------------------------------------------- 三类凭证表单
const FORMS = {
  umami: (cred) => `
    <label>Umami 地址<input name="base_url" type="url" required placeholder="https://stats.example.com" value="${esc(cred?.umami?.base_url || "")}"></label>
    <label>API Key
      <input name="api_key" type="password" autocomplete="off" placeholder="${cred?.umami?.api_key ? `已保存 ${esc(cred.umami.api_key)}，留空则沿用` : "umami_..."}">
      <span class="hint">在 Umami → 设置 → API Keys 里创建。</span>
    </label>`,
  google_sa: (cred) => `
    ${cred?.google_sa ? `<p class="muted small">当前：<span class="mono">${esc(cred.google_sa.email)}</span></p>` : ""}
    <label>Service Account JSON
      <input name="file" type="file" accept="application/json,.json">
      <textarea name="json_text" placeholder='{"type": "service_account", ...}' spellcheck="false"></textarea>
      <span class="hint">选文件或直接粘贴。记得先在 Search Console → 设置 → 用户和权限里，把它的 client_email 加为用户。</span>
    </label>`,
  google_key: (cred) => `
    <label>API Key
      <input name="api_key" type="password" autocomplete="off" required placeholder="${cred?.google_key?.api_key ? `已保存 ${esc(cred.google_key.api_key)}` : "AIza..."}">
      <span class="hint">需要启用 PageSpeed Insights API 和 Chrome UX Report API。校验会真实跑一次 PageSpeed，约 10–30 秒。</span>
    </label>`,
};

export function credentialStep(step, cred, lastCheck, { onSaved } = {}) {
  const html = `
    <form class="card cred-form" data-step="${step}" novalidate>
      ${FORMS[step](cred)}
      <div class="row">
        <button class="btn primary" type="submit">校验并保存</button>
        ${lastCheck ? `<button class="btn" type="button" data-recheck>用已保存的凭证重新检测</button>` : ""}
      </div>
      <div class="out">${resultBox(step, lastCheck)}</div>
    </form>`;
  const bind = (scope) => {
    const form = scope.querySelector(`form[data-step="${step}"]`);
    const out = form.querySelector(".out");
    const busy = (btn, on) => {
      btn.disabled = on;
      btn.innerHTML = on ? `<span class="spin" aria-hidden="true"></span>正在调用上游校验…` : btn.dataset.label;
    };
    form.querySelectorAll("button").forEach((b) => (b.dataset.label = b.innerHTML));
    form.querySelector("input[type=file]")?.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      if (f) form.json_text.value = await f.text();
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      const body = Object.fromEntries(new FormData(form));
      delete body.file;
      busy(btn, true);
      try {
        const res = await api(`/api/v1/setup/${step}`, { method: "PUT", body });
        out.innerHTML = resultBox(step, res);
        if (res.ok) {
          form.querySelectorAll("input[type=password], textarea").forEach((i) => (i.value = ""));
          onSaved?.(res);
        }
      } catch (err) {
        out.innerHTML = resultBox(step, { ok: false, detail: { error: err.message } });
      } finally {
        busy(btn, false);
      }
    });
    form.querySelector("[data-recheck]")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      busy(btn, true);
      try {
        out.innerHTML = resultBox(step, await api(`/api/v1/setup/${step}/recheck`, { method: "POST" }));
      } catch (err) {
        out.innerHTML = resultBox(step, { ok: false, detail: { error: err.message } });
      } finally {
        busy(btn, false);
      }
    });
  };
  return { html, bind };
}

// ---------------------------------------------------------------- 第 4 步：站点
async function sitesStep(body) {
  body.innerHTML = `<div class="card"><p class="muted">正在从 Umami 和 Search Console 读取站点…</p></div>`;
  const cands = await api("/api/v1/setup/site-candidates");
  const rows = cands.map((c, i) => `
    <div class="item">
      <label class="row" style="flex:1 1 220px;flex-direction:row;align-items:center;gap:10px">
        <input type="checkbox" name="pick" value="${i}" style="width:20px;min-height:20px" ${c.added ? "checked disabled" : c.gsc_property ? "checked" : ""}>
        <span><b>${esc(c.domain)}</b><br><span class="muted small">${esc(c.name)}</span></span>
      </label>
      <div class="t"><span class="small muted">Search Console</span>
        <span class="mono small">${c.gsc_property ? esc(c.gsc_property) : '<span class="warn">没有匹配的资源，只监控 Umami</span>'}</span></div>
      <label class="a" style="font-weight:500">品牌词（逗号分隔）
        <input name="brand_${i}" value="${esc(c.brand_terms.join(", "))}" ${c.added ? "disabled" : ""}></label>
      ${c.added ? '<span class="tag index">已在监控</span>' : ""}
    </div>`).join("");
  body.innerHTML = `
    <form class="card flush" id="sites-form">
      <div class="card-head"><div><h2>选择要监控的站点</h2>
        <p class="muted small">按域名把 Umami 网站和 Search Console 资源配对。品牌词用来区分「老用户搜品牌」和「SEO 带来的新流量」。</p></div></div>
      <div class="list">${rows || '<div class="empty">Umami 里还没有网站</div>'}</div>
      <div class="card-head" style="border-top:1px solid var(--line-soft);border-bottom:0">
        <span class="muted small">添加后会在后台拉取 16 个月的搜索数据、巡检收录并跑 PageSpeed。</span>
        <button class="btn primary" type="submit">添加选中站点</button>
      </div>
    </form>`;
  body.querySelector("#sites-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const picks = [...form.querySelectorAll("input[name=pick]:checked:not(:disabled)")].map((x) => +x.value);
    if (!picks.length && !cands.some((c) => c.added)) return toast("至少选一个站点", "error");
    form.querySelector("button[type=submit]").disabled = true;
    for (const i of picks) {
      const c = cands[i];
      await api("/api/v1/sites", { method: "POST", body: {
        domain: c.domain, umami_website_id: c.umami_website_id, gsc_property: c.gsc_property,
        brand_terms: form[`brand_${i}`].value.split(",").map((s) => s.trim()).filter(Boolean) } });
    }
    await refreshStatus();
    toast(`已添加 ${picks.length} 个站点，后台开始同步`);
    go(`/?site=${encodeURIComponent(currentSite()?.domain || "")}`);
  });
}

// ---------------------------------------------------------------- 主视图
export async function render(root) {
  const status = state.status || (await refreshStatus());
  const cred = await api("/api/v1/setup/credentials");
  const steps = status.steps;
  const want = new URLSearchParams(location.search).get("step");
  if (want && steps.some((s) => s.id === want)) active = want;
  if (!active || !steps.find((s) => s.id === active)) active = (steps.find((s) => !s.ok) || steps[0]).id;
  const idx = steps.findIndex((s) => s.id === active);
  root.innerHTML = `
    <div><h1>初始化配置</h1>
      <p class="muted">登录已经通过 Pocket ID 完成。再配好下面几项，监控就能开始工作。每一项都会真实调用一次上游来校验。</p></div>
    <div class="wizard">
      <nav class="steps" aria-label="配置步骤">${steps.map((s, i) => `
        <button type="button" data-go="${s.id}" ${s.id === active ? 'aria-current="step"' : ""}>
          <span class="n ${s.ok ? "ok" : s.configured ? "err" : ""}">${s.ok ? "✓" : i + 1}</span>
          <span>${esc(s.title)}<br><span class="small muted">${s.ok ? "已通过校验" : s.configured ? "需要重新检测" : "未配置"}</span></span>
        </button>`).join("")}
      </nav>
      <section class="step-body" id="step-body" aria-label="${esc(steps[idx].title)}"></section>
    </div>`;
  root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
    active = b.dataset.go;
    history.replaceState(null, "", "/setup");
    render(root);
  }));
  const body = root.querySelector("#step-body");
  if (active === "sites") {
    const blocked = steps.filter((s) => s.id !== "sites" && !s.ok);
    if (blocked.some((s) => s.id !== "google_key")) {
      body.innerHTML = `<div class="card"><p>先完成 ${blocked.map((s) => esc(s.title)).join("、")}，才能读取站点列表。</p></div>`;
      return;
    }
    return sitesStep(body);
  }
  const step = steps[idx];
  const form = credentialStep(active, cred, step.configured ? { ok: step.ok, detail: step.detail } : null, {
    onSaved: async () => {
      await refreshStatus();
      setTimeout(() => { active = (state.status.steps.find((s) => !s.ok) || { id: "sites" }).id; render(root); }, 900);
    },
  });
  body.innerHTML = form.html;
  form.bind(body);
}
