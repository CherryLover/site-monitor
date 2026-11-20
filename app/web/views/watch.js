// ============================================================================
//  watch —— 机会清单里的「观察中」：已处理的问题不消失，持续用数据验证
//
//  每条观察一张卡：做了什么（处理记录）、修复前后同口径对比、当前判定、
//  以及按天的判定时间线（一格一天）。没见效可以「再次处理」，观察期从新日期重算。
//  标记表单也在这里：标记已处理和再次处理共用同一个内联表单。
// ============================================================================
import { api, esc, shortDate, toast } from "/static/ui.js";

export const VERDICT_CLS = { verified: "ok", improving: "run", waiting: "", no_change: "warn", regressed: "err" };
const GSC_TYPES = new Set(["rank", "gap", "cannibal", "market"]);
const today = () => new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------- 内联表单
export function markForm(key, again = false) {
  return `<form class="mark-form" data-key="${esc(key)}">
    <label>${again ? "这次又做了什么" : "做了什么"}<input name="note" required maxlength="300"
      placeholder="例如：首页 title 前置 Office to Markdown，已上线"></label>
    <label>上线日期<input name="fixed_at" type="date" value="${today()}" required></label>
    <div class="row"><button class="btn primary sm" type="submit">${again ? "记录并重新观察" : "标记已处理，开始观察"}</button>
      <button class="btn sm" type="button" data-cancel>取消</button></div>
    <p class="muted small">不会删除这条机会：之后每天用修复前 28 天的数据做基线自动对比，直到数据证明问题解决。</p>
  </form>`;
}

export function bindMarkForms(root, base, onDone) {
  root.querySelectorAll(".mark-form").forEach((form) => {
    form.querySelector("[data-cancel]").addEventListener("click", () => onDone(null));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("[type=submit]");
      btn.disabled = true;
      const body = { key: form.dataset.key, ...Object.fromEntries(new FormData(form)) };
      try {
        const w = await api(`${base}/watches`, { method: "POST", body });
        toast(`已进入观察：${w.verdict_label}`);
        onDone(w);
      } catch (err) {
        btn.disabled = false;
        toast(err.message, "error");
      }
    });
  });
}

// ---------------------------------------------------------------- 观察卡
function timeline(points) {
  if (!points.length) return "";
  return `<div class="dots-line" aria-label="每日判定"><span class="lbl">每日判定</span>${points.map((p) =>
    `<i class="v ${VERDICT_CLS[p.verdict]}" title="${esc(p.date)} · ${esc(p.summary || "")}"></i>`).join("")}</div>`;
}

function history(w) {
  return w.history.map((h, i) => `<li><span class="mono">${esc(shortDate(h.date))}</span>
    ${i ? "再次处理：" : "处理："}${esc(h.note)}</li>`).join("");
}

function firingNote(w) {
  if (!w.rule_firing) return '<span class="chip ok">规则已不再触发</span>';
  const why = GSC_TYPES.has(w.type) ? "规则看最近 28 天，修复前的数据滑出窗口前会一直触发" : "规则当前仍在触发";
  return `<span class="chip" title="${why}">规则仍在触发</span>`;
}

export function watchCard(w, types, editing) {
  const day = w.days ? `观察第 ${w.days} 天` : "等待修复后的数据";
  return `<div class="item watch" data-watch="${esc(w.key)}">
    <div style="flex:0 0 52px"><span class="tag ${w.type}">${types[w.type] || w.type}</span></div>
    <div class="t"><b>${esc(w.target)}</b>
      <span class="small">${esc(w.summary || "")}</span>
      <span class="ev">标记时：${esc(w.evidence_at_mark)}</span>
      <ul class="hist">${history(w)}</ul>
      ${timeline(w.points)}
    </div>
    <div class="col">
      <span class="chip ${VERDICT_CLS[w.verdict]}"><b>${esc(w.verdict_label)}</b></span>
      <span class="muted small">${day}${w.verified_at ? ` · ${esc(shortDate(w.verified_at))} 首次验证` : ""}</span>
      ${firingNote(w)}
      ${editing ? "" : `<button class="btn sm" type="button" data-again="${esc(w.key)}">再次处理</button>`}
    </div>
    ${editing ? `<div class="full">${markForm(w.key, true)}</div>` : ""}
  </div>`;
}

export const watchLegend = `<p class="muted small legend-v">
  <span class="chip">等待数据</span> 观察期太短或样本不够 · <span class="chip run">见效中</span> 已改善未达标 · <span class="chip ok">已验证</span> 达标且观察期够长 ·
  <span class="chip warn">未见改善</span> 观察够久仍没变化 · <span class="chip err">复发</span> 验证过又回落 · 时间线一格一天</p>`;
