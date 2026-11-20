// ============================================================================
//  ui —— 页面共用的小工具：请求、转义、格式化、图表
//
//  渲染方式：模板字符串 → innerHTML。所有来自数据的字符串都必须经过 esc()。
//  没有框架、没有构建步骤；每个视图是一个 async render(root) 函数。
// ============================================================================

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail || body?.error || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) {
    location.href = "/login";
    throw new ApiError(401, { detail: "登录已过期" });
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export const num = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("zh-CN"));

export function delta(cur, prev, { invert = false, digits = 0 } = {}) {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return { text: "—", cls: "flat" };
  if (prev === 0) return { text: cur === 0 ? "持平" : `上期 0`, cls: cur > 0 ? (invert ? "bad" : "good") : "flat" };
  const p = ((cur - prev) / prev) * 100;
  if (Math.abs(p) < 0.5) return { text: "持平", cls: "flat" };
  const good = invert ? p < 0 : p > 0;
  return { text: `${p > 0 ? "+" : "−"}${Math.abs(p).toFixed(digits)}%`, cls: good ? "good" : "bad" };
}

export const shortDate = (iso) => (iso ? iso.slice(5, 10) : "—");

export function when(iso) {
  if (!iso) return "从未";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 1440) return `${Math.round(mins / 60)} 小时前`;
  return `${Math.round(mins / 1440)} 天前`;
}

export const path = (url) => {
  try { return new URL(url).pathname; } catch { return url; }
};

// ---------------------------------------------------------------- 图表：按周堆叠柱 + 排名折线
export function trendChart(weeks) {
  const H = 220;
  const stack = (w) => w.brand.impressions + w.nonbrand.impressions + w.anonymous.impressions;
  const max = Math.max(10, ...weeks.map(stack));
  const top = niceCeil(max);
  const px = (v) => ((v / top) * H).toFixed(1);
  const n = weeks.length;
  const ranks = weeks.map((w, i) => ({ i, p: w.nonbrand.position })).filter((r) => r.p !== null);
  const worst = Math.max(30, ...ranks.map((r) => r.p));
  const rankTop = Math.ceil(worst / 10) * 10;
  const y = (p) => ((p - 1) / (rankTop - 1)) * H;
  const pts = ranks.map((r) => `${((r.i + 0.5) / n) * 1000},${y(r.p).toFixed(1)}`).join(" ");

  const bars = weeks.map((w) => `
    <div class="bar-col" title="${esc(`${w.week} 周 · 非品牌 ${w.nonbrand.impressions} · 品牌 ${w.brand.impressions} · 匿名 ${w.anonymous.impressions}`)}">
      <div class="bar">
        <div style="height:${px(w.anonymous.impressions)}px" class="seg anon"></div>
        <div style="height:${px(w.brand.impressions)}px" class="seg brand"></div>
        <div style="height:${px(w.nonbrand.impressions)}px" class="seg non"></div>
      </div>
    </div>`).join("");
  const dots = ranks.map((r) => `
    <div class="dot" style="left:calc(${((r.i + 0.5) / n) * 100}% - 4px);top:${(y(r.p) - 4).toFixed(1)}px">
      <span>#${r.p}</span>
    </div>`).join("");
  const desc = ranks.length ? `非品牌平均排名从 ${ranks[0].p} 到 ${ranks[ranks.length - 1].p}` : "暂无非品牌排名数据";

  return `
  <div class="chart">
    <div class="axis left"><span>${top}</span><span>${top / 2}</span><span>0</span></div>
    <div class="plot-wrap">
      <div class="plot" style="--cols:${n}">
        <div class="bars">${bars}</div>
        <svg viewBox="0 0 1000 ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(desc)}">
          <polyline points="${pts}" fill="none" stroke="var(--rank)" stroke-width="2" vector-effect="non-scaling-stroke"/>
        </svg>
        ${dots}
      </div>
      <div class="xlabels" style="--cols:${n}">${weeks.map((w) => `<span>${shortDate(w.week)}</span>`).join("")}</div>
    </div>
    <div class="axis right"><span>#1</span><span>#${Math.round(rankTop / 2)}</span><span>#${rankTop}</span></div>
  </div>`;
}

function niceCeil(v) {
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function stackBar(parts) {
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  const label = parts.map((p) => `${p.label} ${p.value}`).join("，");
  return `<div class="stackbar" role="img" aria-label="${esc(label)}">${parts
    .map((p) => `<div class="${p.cls}" style="width:${((p.value / total) * 100).toFixed(1)}%"></div>`)
    .join("")}</div>`;
}

export function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", "status");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
