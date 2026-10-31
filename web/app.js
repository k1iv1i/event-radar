/* 事件雷达前端 v2：首页事件卡片 → 事件详情（四时间戳 / 时间轴 / 结论变化与通知 / 命题 / 明细折叠） */
const $ = (id) => document.getElementById(id);
const TYPE_CN = { fact: "事实", opinion: "观点", speculation: "推测", rumor: "传闻" };
const STATUS_CN = { emerging: "传闻阶段", developing: "发展中", confirmed: "已证实", denied: "已否认",
  corrected: "已更正", disputed: "证据冲突", closed: "已完结", expired: "已过期" };
const STATUS_ICON = { emerging: "◌", developing: "◔", confirmed: "✓", denied: "✕",
  corrected: "↻", disputed: "⚠", closed: "●", expired: "○" };
const VERDICT_ICON = { confirmed: "✓", denied: "✕", corrected: "↻", closed: "●",
  disputed: "⚠", unverified: "·" };
const CHANGE_CN = { create: "事件创建", proposition_added: "新增命题", update: "证据更新", rumor: "传闻证据",
  confirmed: "证实", denied: "否认", corrected: "更正", closed: "完结", disputed: "证据冲突", expired: "过期",
  merge: "事件合并" };
const FLIP_TYPES = ["confirmed", "denied", "corrected", "closed", "disputed", "expired", "merge"];
const TYPE_COLOR = { fact: "#199e70", opinion: "#898781", speculation: "#3987e5", rumor: "#e66767" };
const VERDICT_COLOR = { confirmed: "#0ca30c", denied: "#d03b3b", corrected: "#ec835a",
  closed: "#c3c2b7", disputed: "#fab219", unverified: "#898781" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function verdictCn(v) { return { confirmed: "已证实", denied: "已否认", corrected: "已更正", closed: "已完结", disputed: "证据冲突" }[v] || "待证实"; }
function fmtTime(t) { return t ? String(t).replace("T", " ").slice(5, 16) : "—"; }
function fmtDate(t) { return t ? String(t).slice(0, 10) : "—"; }
function ts(t) { return t ? new Date(t).getTime() : NaN; }
const badge = (cls, icon, text) => `<span class="status-badge ${cls}">${icon || ""}${esc(text)}</span>`;

/* ================= 首页 ================= */
let events = [];
let selectedId = null;

async function loadHome() {
  const data = await api("/api/events");
  events = data.events;
  $("eventGrid").innerHTML = data.events.map((e) => {
    const concl = e.conclusion || {};
    const props = concl.propositions ? Object.values(concl.propositions) : [];
    const main = props.find((p) => p.verdict !== "unverified") || props[0] || null;
    const mainLine = main
      ? `核心命题「${esc(main.proposition)}」：${verdictCn(main.verdict)}`
      : "尚无权威判定，处于待证实状态";
    const top = props.slice(0, 3);
    return `<article class="ev-card" data-id="${e.event_id}">
      <div class="ev-card-head">
        <h3>${esc(e.title)}</h3>
        ${badge(`status-${e.status}`, STATUS_ICON[e.status] || "·", STATUS_CN[e.status] || e.status)}
        ${concl.expired ? badge("status-expired", "○", "已过期") : ""}
      </div>
      <p class="ev-card-main">${mainLine}</p>
      <div class="ev-card-chips">
        ${top.map((p) => `<span class="verdict-badge verdict-${p.verdict}">${VERDICT_ICON[p.verdict] || "·"}${verdictCn(p.verdict)}</span>`).join("")}
      </div>
      <div class="ev-card-meta">
        <span>披露 ${fmtDate(e.event_time) || "—"} ~ ${fmtDate(e.latest_disclose) || "—"}</span>
        <span>主张 ${concl.evidence_counts ? Object.values(concl.evidence_counts).reduce((a, b) => a + b, 0) : 0} 条</span>
        <span>更新 ${fmtTime(e.updated_at)}</span>
        <span class="ev-card-go">进入时间线 →</span>
      </div>
    </article>`;
  }).join("");
  document.querySelectorAll(".ev-card").forEach((el) =>
    el.addEventListener("click", () => openEvent(el.dataset.id)));

  const s = await api("/api/stats");
  $("homeStats").innerHTML = [
    `事件 <b>${s.events}</b>`, `文档 <b>${s.documents}</b>`, `主张 <b>${s.claims}</b>`,
    `事实 <b>${s.claim_types.fact}</b> / 观点 <b>${s.claim_types.opinion}</b> / 推测 <b>${s.claim_types.speculation}</b> / 传闻 <b>${s.claim_types.rumor}</b>`,
  ].map((x) => `<span>${x}</span>`).join(" · ");
  $("footerDisclaimer").textContent = s.disclaimer;
}

function showHome() {
  $("eventView").classList.add("hidden");
  $("homeView").classList.remove("hidden");
  selectedId = null;
  if (location.hash) history.replaceState(null, "", location.pathname);
  loadHome();
}

/* ================= 事件详情 ================= */
async function openEvent(id) {
  selectedId = id;
  location.hash = "#/event/" + id;
  const d = await api(`/api/events/${id}`);
  const e = d.event, concl = e.conclusion || {};
  $("homeView").classList.add("hidden");
  $("eventView").classList.remove("hidden");
  window.scrollTo(0, 0);

  const flipDocs = new Set(d.versions.filter((v) => FLIP_TYPES.includes(v.change_type)).map((v) => v.trigger_doc_id));
  const flipClaims = d.claims.filter((c) => flipDocs.has(c.doc_id));
  const versionByDoc = {};
  for (const v of d.versions) if (FLIP_TYPES.includes(v.change_type) && v.trigger_doc_id && !versionByDoc[v.trigger_doc_id]) versionByDoc[v.trigger_doc_id] = v;

  const tsTiles = [
    ["事件发生", e.event_time, "最早的事件时间"],
    ["最新披露", e.latest_disclose, "最近一份披露材料"],
    ["首次抓取", e.created_at, "事件进入系统"],
    ["最近更新", e.updated_at, "结论最近一次变化"],
  ].map(([lab, val, hint]) => `
    <div class="ts-tile">
      <span class="ts-tile-lab">${lab}</span>
      <b>${fmtTime(val) || "—"}</b>
      <span class="ts-tile-hint">${hint}</span>
    </div>`).join("");

  const flipRows = d.versions
    .filter((v) => FLIP_TYPES.includes(v.change_type))
    .slice().reverse()
    .map((v) => {
      const src = d.documents.find((x) => x.doc_id === v.trigger_doc_id);
      return `<div class="flip-row">
        <span class="flip-time">${fmtDate(v.created_at)}</span>
        <span class="change-badge change-${v.change_type}">${CHANGE_CN[v.change_type] || v.change_type}</span>
        <span class="flip-summary">${esc(v.summary)}</span>
        ${src ? `<span class="flip-src">依据：${esc(src.title)}（${fmtDate(src.disclose_time)}）</span>` : ""}
        <span class="flip-notify">🔔 已推送通知</span>
      </div>`;
    }).join("");

  const propCards = (concl.propositions ? Object.values(concl.propositions) : [])
    .map((p) => `
    <div class="prop-row">
      <span class="verdict-badge verdict-${p.verdict}">${VERDICT_ICON[p.verdict] || "·"}${verdictCn(p.verdict)}</span>
      <span class="prop-name">${esc(p.proposition)}</span>
      <span class="prop-basis">${p.by_doc ? `${esc(p.by_source)} · ${fmtDate(p.by_time)}` : "待证实"}</span>
    </div>`).join("");

  const claimsHtml = d.claims.map((c) => {
    const doc = c.doc || {};
    return `<div class="tl-node type-${c.claim_type}">
      <div class="tl-body">
        <div class="tl-head">
          <span class="type-badge type-${c.claim_type}">${TYPE_CN[c.claim_type] || c.claim_type}</span>
          <span class="summary">${esc(c.summary)}</span>
          <span class="weight-num">权重 ${c.evidence_weight}</span>
          ${doc.fetch_status === "degraded" ? `<span class="warn">⚠ 接口降级</span>` : ""}
        </div>
        ${c.quote ? `<div class="tl-quote">${esc(c.quote)}</div>` : ""}
        <div class="tl-meta">
          <span>${esc(doc.publisher || "未知")} · ${esc(doc.source || "")}</span>
          ${doc.url ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener">原文 ↗</a>` : ""}
          <span><span class="ts-label">披露</span><b>${fmtTime(doc.disclose_time)}</b></span>
          <span><span class="ts-label">事件</span><b>${fmtTime(c.event_time || doc.event_time)}</b></span>
          <span><span class="ts-label">抓取</span><b>${fmtTime(doc.crawl_time)}</b></span>
        </div>
      </div>
    </div>`;
  }).join("");

  const versionsHtml = d.versions.slice().reverse().map((v) => `
    <div class="ver-item">
      <span class="ver-no">v${v.version_no}</span>
      <span class="change-badge change-${v.change_type}">${CHANGE_CN[v.change_type] || v.change_type}</span>
      <span class="ver-summary">${esc(v.summary)}</span>
      <span class="ver-time">${fmtTime(v.created_at)}</span>
    </div>`).join("");

  const docsHtml = d.documents.map((doc) => `
    <details class="doc-item">
      <summary>${esc(doc.title)}<span class="doc-meta">${esc(doc.publisher)} · 披露 ${fmtTime(doc.disclose_time)} · 抓取 ${fmtTime(doc.crawl_time)}</span></summary>
      <pre>${esc(doc.body)}</pre>
    </details>`).join("");

  const expiredBadge = concl.expired ? badge("status-expired", "○", "已过期") : "";

  $("detail").innerHTML = `
    <div class="ev-header">
      <h2>${esc(e.title)}</h2>
      <div class="ev-header-badges">
        ${badge(`status-${e.status}`, STATUS_ICON[e.status] || "·", STATUS_CN[e.status] || e.status)}
        ${expiredBadge}
      </div>
      <p class="entity-tags">涉事主体：${esc((JSON.parse(e.entity_tags || "[]") || []).slice(0, 8).join("、") || "—")}</p>
    </div>

    <div class="ts-tiles">${tsTiles}</div>

    <section class="section">
      <h4>当前结论</h4>
      <div class="panel statement">${esc(concl.statement || "（暂无结论）")}</div>
    </section>

    <section class="section">
      <h4>证据时间轴（披露时间为轴）</h4>
      <div class="panel" id="timelinePanel">${renderTimeline(d.claims, flipClaims, versionByDoc, concl)}</div>
    </section>

    <section class="section">
      <h4>结论变化与通知</h4>
      <div class="panel flip-list">${flipRows || `<div class="empty">暂无结论变化</div>`}</div>
    </section>

    <section class="section">
      <h4>命题判定</h4>
      <div class="panel prop-list">${propCards || `<div class="empty">暂无命题</div>`}</div>
    </section>

    <details class="section fold">
      <summary><h4>证据明细（${d.claims.length} 条主张）</h4></summary>
      <div class="timeline">${claimsHtml}</div>
    </details>

    <details class="section fold">
      <summary><h4>版本演化（${d.versions.length} 个版本）</h4></summary>
      <div class="ver-list">${versionsHtml}</div>
    </details>

    <details class="section fold">
      <summary><h4>原始文档（${d.documents.length} 篇）</h4></summary>
      <div class="panel">${docsHtml}</div>
    </details>`;
}

/* ================= 时间轴图（SVG） ================= */
function renderTimeline(claims, flipClaims, versionByDoc, concl) {
  const w = 900, h = 240, padL = 46, padR = 30, axisY = 208;
  const dates = [];
  for (const c of claims) {
    const t = ts(c.doc?.disclose_time); if (!isNaN(t)) dates.push(t);
    const et = ts(c.event_time || c.doc?.event_time); if (!isNaN(et)) dates.push(et);
  }
  if (!dates.length) return `<div class="empty">暂无时间数据</div>`;
  const min = Math.min(...dates), max = Math.max(...dates);
  const span = Math.max(max - min, 24 * 3600 * 1000);
  const X = (t) => padL + ((t - min) / span) * (w - padL - padR);

  // 年份刻度
  let ticks = "";
  const y0 = new Date(min).getFullYear(), y1 = new Date(max).getFullYear();
  for (let y = y0; y <= y1; y++) {
    const t = new Date(y, 6, 1).getTime();
    if (t < min || t > max) continue;
    ticks += `<line x1="${X(t)}" y1="${axisY}" x2="${X(t)}" y2="${axisY + 4}" stroke="#383835" stroke-width="1"/>
      <text x="${X(t)}" y="${axisY + 17}" fill="#898781" font-size="11" text-anchor="middle">${y}</text>`;
  }

  // 节点分行（翻转点占顶层）
  const lanes = [0, 1, 2, 3];
  let laneIdx = 0;
  let nodes = "";
  const flipSet = new Set(flipClaims.map((c) => c.claim_id));
  for (const c of claims) {
    const dt = ts(c.doc?.disclose_time);
    if (isNaN(dt)) continue;
    const isFlip = flipSet.has(c.claim_id);
    const lane = isFlip ? 0 : lanes[1 + (laneIdx++ % 3)];
    const y = 70 + lane * 34;
    const x = X(dt);
    const color = TYPE_COLOR[c.claim_type] || "#898781";
    const tip = `${TYPE_CN[c.claim_type] || c.claim_type} · ${c.summary || ""}\n披露 ${fmtDate(c.doc?.disclose_time)}\n抓取 ${fmtDate(c.doc?.crawl_time)}${c.quote ? "\n" + c.quote.slice(0, 120) : ""}`;
    if (isFlip) {
      const v = versionByDoc[c.doc_id];
      const vcolor = VERDICT_COLOR[v?.change_type] || "#3987e5";
      nodes += `<g><path d="M${x} ${y - 6} L${x + 6} ${y} L${x} ${y + 6} L${x - 6} ${y} Z" fill="${vcolor}"><title>${esc(tip)}</title></path>
        <text x="${x}" y="${y - 12}" fill="${vcolor}" font-size="11" text-anchor="middle">${CHANGE_CN[v?.change_type] || "翻转"}</text></g>`;
    } else {
      nodes += `<circle cx="${x}" cy="${y}" r="4" fill="${color}"><title>${esc(tip)}</title></circle>`;
    }
    // 事件发生时间 vs 披露时间：偏移虚线
    const et = ts(c.event_time || c.doc?.event_time);
    if (!isNaN(et) && Math.abs(et - dt) > 24 * 3600 * 1000) {
      const ex = X(et);
      nodes += `<circle cx="${ex}" cy="${y}" r="3" fill="none" stroke="${color}" stroke-width="1"/>
        <line x1="${ex}" y1="${y}" x2="${x}" y2="${y}" stroke="${color}" stroke-width="0.8" stroke-dasharray="3 3"/>`;
    }
  }

  // 过期断点
  let expiryMark = "";
  if (concl.expired) {
    const ex = Math.min(X(max) + 8, w - padR);
    expiryMark = `<g><line x1="${ex}" y1="70" x2="${ex}" y2="172" stroke="#6e6d68" stroke-width="1.4" stroke-dasharray="4 4"/>
      <text x="${ex}" y="192" fill="#6e6d68" font-size="11" text-anchor="middle">60 天无更新 · 已过期</text></g>`;
  }

  const legend = ["fact", "opinion", "speculation", "rumor"].map((t, i) => `
    <circle cx="${padL + i * 78}" cy="232" r="4" fill="${TYPE_COLOR[t]}"/>
    <text x="${padL + i * 78 + 9}" y="236" fill="#c3c2b7" font-size="11">${TYPE_CN[t]}</text>`).join("")
    + `<text x="${padL + 4 * 78 + 20}" y="236" fill="#c3c2b7" font-size="11">◆ 结论翻转 · 虚线＝事件发生→披露偏移</text>`;

  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="证据时间轴">
    <line x1="${padL}" y1="${axisY}" x2="${w - padR}" y2="${axisY}" stroke="#383835" stroke-width="1"/>
    ${ticks}${nodes}${expiryMark}${legend}
  </svg>`;
}

/* ================= 通知 ================= */
async function loadNotifs() {
  const data = await api("/api/notifications");
  const unread = data.notifications.filter((n) => !n.read).length;
  const b = $("unreadBadge");
  b.textContent = unread;
  b.classList.toggle("hidden", unread === 0);
  $("notifList").innerHTML = data.notifications.map((n) => `
    <div class="notif ${n.read ? "" : "unread"}" data-id="${n.notif_id}" data-event="${esc(n.event_id || "")}">
      <div class="n-title">${esc(n.title)}</div>
      <div class="n-msg">${esc(n.message)}</div>
      <div class="n-time">${fmtTime(n.created_at)}</div>
    </div>`).join("") || `<div class="empty">暂无通知</div>`;
  document.querySelectorAll(".notif").forEach((el) => el.addEventListener("click", async () => {
    await api(`/api/notifications/${el.dataset.id}/read`, { method: "POST" });
    closeDrawer();
    if (el.dataset.event) openEvent(el.dataset.event);
  }));
}
function openDrawer() { $("notifDrawer").classList.remove("hidden"); loadNotifs(); }
function closeDrawer() { $("notifDrawer").classList.add("hidden"); }

/* ================= 在线入库 ================= */
function openIngest() { $("ingestModal").classList.remove("hidden"); $("ingestResult").classList.add("hidden"); }
function closeIngest() { $("ingestModal").classList.add("hidden"); }
async function submitIngest() {
  const payload = {
    title: $("ingTitle").value.trim(),
    source: $("ingSource").value,
    publisher: $("ingPublisher").value.trim(),
    publish_time: $("ingPublishTime").value.trim(),
    event_time: $("ingEventTime").value.trim(),
    url: $("ingUrl").value.trim(),
    body: $("ingBody").value.trim(),
  };
  if (!payload.title || !payload.body) { alert("请填写标题与正文"); return; }
  const btn = $("submitIngest");
  btn.disabled = true; btn.textContent = "分析中…";
  try {
    const r = await api("/api/documents", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const box = $("ingestResult");
    box.classList.remove("hidden");
    box.innerHTML = `
      <div class="ir-item">✅ 已入库并抽取 <b>${r.claims}</b> 条主张（抽取方式：<b>${r.extraction_method}</b>${r.fetch_status === "degraded" ? "，⚠ LLM 接口异常已降级为规则抽取" : ""}）</div>
      ${r.changes.map((c) => `<div class="ir-item">· 事件「${esc(c.event_id)}」：命题「${esc(c.proposition)}」记为 ${esc(TYPE_CN[c.claim_type])}</div>`).join("")}
      ${(r.merged_events || []).map(() => `<div class="ir-item">🔗 触发事件合并</div>`).join("")}
      ${r.notifications.length ? `<div class="ir-item">🔔 触发 ${r.notifications.length} 条通知（${r.notifications.map((n) => esc(n.message)).join("；")}）</div>` : `<div class="ir-item">🔕 未改变任何事件结论，无新通知</div>`}
    `;
    if (r.changes[0]) openEvent(r.changes[0].event_id);
    closeIngest();
  } catch (e) {
    alert("提交失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "提交并分析";
  }
}

/* ================= 路由与启动 ================= */
function route() {
  const m = location.hash.match(/^#\/event\/(.+)$/);
  if (m) openEvent(m[1]);
  else showHome();
}
$("bellBtn").addEventListener("click", openDrawer);
$("closeNotif").addEventListener("click", closeDrawer);
$("addDocBtn").addEventListener("click", openIngest);
$("closeIngest").addEventListener("click", closeIngest);
$("cancelIngest").addEventListener("click", closeIngest);
$("submitIngest").addEventListener("click", submitIngest);
$("backBtn").addEventListener("click", () => { location.hash = ""; });
window.addEventListener("hashchange", route);
route();
