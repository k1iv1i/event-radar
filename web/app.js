/* 事件雷达前端：事件列表 / 证据时间线 / 版本演化 / 通知中心 / 在线文档入库 */
const $ = (id) => document.getElementById(id);
const TYPE_CN = { fact: "事实", opinion: "观点", speculation: "推测", rumor: "传闻" };
const STATUS_CN = { emerging: "传闻阶段", developing: "发展中", confirmed: "已证实", denied: "已否认",
  corrected: "已更正", disputed: "证据冲突", closed: "已完结", expired: "已过期" };
// 状态色板规则：状态永远 icon + label，颜色不单独表义
const STATUS_ICON = { emerging: "◌", developing: "◔", confirmed: "✓", denied: "✕",
  corrected: "↻", disputed: "⚠", closed: "●", expired: "○" };
const VERDICT_ICON = { confirmed: "✓", denied: "✕", corrected: "↻", closed: "●",
  disputed: "⚠", unverified: "·" };
const CHANGE_CN = { create: "事件创建", proposition_added: "新增命题", update: "证据更新", rumor: "传闻证据",
  confirmed: "证实", denied: "否认", corrected: "更正", closed: "完结", disputed: "证据冲突", expired: "过期" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

/* ---------- 事件列表与统计 ---------- */
let events = [];

async function loadStats() {
  try {
    const s = await api("/api/stats");
    const chips = [
      `<span class="chip">事件 <b>${s.events}</b></span>`,
      `<span class="chip">文档 <b>${s.documents}</b></span>`,
      `<span class="chip">主张 <b>${s.claims}</b></span>`,
      `<span class="chip">事实 <b>${s.claim_types.fact}</b> / 观点 <b>${s.claim_types.opinion}</b> / 推测 <b>${s.claim_types.speculation}</b> / 传闻 <b>${s.claim_types.rumor}</b></span>`,
      `<span class="chip">AI抽取 llm<b>${s.extraction_methods.llm || 0}</b>·cache<b>${s.extraction_methods.cache || 0}</b>·rule<b>${s.extraction_methods.rule || 0}</b></span>`,
    ];
    $("statChips").innerHTML = chips.join("");
    $("footerDisclaimer").textContent = s.disclaimer;
  } catch (e) { console.error(e); }
}

async function loadEvents(selectId) {
  const data = await api("/api/events");
  events = data.events;
  $("eventList").innerHTML = data.events.map((e) => {
    const props = (e.conclusion.propositions ? Object.values(e.conclusion.propositions) : [])
      .slice(0, 3)
      .map((p) => `<div class="prop-line">
          <span class="verdict-badge verdict-${p.verdict}">${VERDICT_ICON[p.verdict] || "·"}${verdictCn(p.verdict)}</span>
          <span class="prop-text" title="${esc(p.proposition)}">${esc(p.proposition)}</span>
        </div>`).join("");
    return `<div class="event-card" data-id="${e.event_id}">
      <h3>${esc(e.title)}</h3>
      <div class="meta">
        <span class="status-badge status-${e.status}">${STATUS_ICON[e.status] || "·"}${STATUS_CN[e.status] || e.status}</span>
        <span>更新 ${fmtTime(e.updated_at)}</span>
      </div>
      <div class="props">${props}</div>
    </div>`;
  }).join("");
  document.querySelectorAll(".event-card").forEach((el) =>
    el.addEventListener("click", () => {
      document.querySelectorAll(".event-card").forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      selectEvent(el.dataset.id);
    }));
  if (selectId) { const el = document.querySelector(`.event-card[data-id="${selectId}"]`); if (el) el.click(); }
  else if (data.events.length) document.querySelector(".event-card").click();
}

function verdictCn(v) { return { confirmed: "已证实", denied: "已否认", corrected: "已更正", closed: "已完结", disputed: "证据冲突" }[v] || "待证实"; }
function fmtTime(t) { return t ? String(t).replace("T", " ").slice(5, 16) : "—"; }

/* ---------- 事件详情 ---------- */
async function selectEvent(id) {
  const d = await api(`/api/events/${id}`);
  const e = d.event, concl = e.conclusion;
  $("emptyState").classList.add("hidden");
  const detail = $("detail");
  detail.classList.remove("hidden");
  const flipDocs = new Set(d.versions.filter((v) => ["confirmed", "denied", "corrected", "closed", "disputed"].includes(v.change_type)).map((v) => v.trigger_doc_id));

  const propCards = (concl.propositions ? Object.values(concl.propositions) : []).map((p) => `
    <div class="panel prop-card">
      <div class="prop-head">
        <span class="verdict-badge verdict-${p.verdict}">${VERDICT_ICON[p.verdict] || "·"}${verdictCn(p.verdict)}</span>
        <span class="prop-name">${esc(p.proposition)}</span>
      </div>
      <div class="prop-meta">证据 ${p.claims} 条 · 权威权重 <span class="weight-num">${p.authoritative_weight || 0}</span></div>
      <div class="weight-bar"><i style="width:${Math.min(100, p.authoritative_weight || 0)}%"></i></div>
      <div class="prop-meta">${p.by_doc ? `判定依据：${esc(p.by_source)}（${fmtTime(p.by_time)}，证据权重 ${p.by_weight}）` : "尚无权威来源判定，处于待证实状态"}</div>
    </div>`).join("");

  const claimsHtml = d.claims.map((c) => {
    const doc = c.doc || {};
    return `
    <div class="tl-node type-${c.claim_type} ${flipDocs.has(c.doc_id) ? "flip" : ""}">
      <div class="tl-body">
        <div class="tl-head">
          <span class="type-badge type-${c.claim_type}">${TYPE_CN[c.claim_type] || c.claim_type}</span>
          <span class="summary">${esc(c.summary)}</span>
          <span class="weight-num">权重 ${c.evidence_weight}</span>
          ${doc.fetch_status === "degraded" ? `<span class="warn">⚠ 接口降级（规则抽取）</span>` : ""}
        </div>
        ${c.quote ? `<div class="tl-quote">${esc(c.quote)}</div>` : ""}
        <div class="tl-meta">
          <span>来源：${esc(doc.publisher || "未知")} · ${esc(doc.source || "")}</span>
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
      <summary>${esc(doc.title)}<span class="doc-meta">${esc(doc.publisher)} · 披露 ${fmtTime(doc.disclose_time)} · 抓取 ${fmtTime(doc.crawl_time)}${doc.fetch_status === "degraded" ? " · ⚠降级" : ""}</span></summary>
      <pre>${esc(doc.body)}</pre>
    </details>`).join("");

  detail.innerHTML = `
    <h2>${esc(e.title)} <span class="status-badge status-${e.status}">${STATUS_ICON[e.status] || "·"}${STATUS_CN[e.status] || e.status}</span></h2>
    <div class="entity-tags">涉事主体：${esc((JSON.parse(e.entity_tags || "[]") || []).join("、") || "—")}</div>
    <div class="ts-row">
      <span><span class="ts-label">事件发生</span><b>${fmtTime(e.event_time)}</b></span>
      <span><span class="ts-label">最新披露</span><b>${fmtTime(e.latest_disclose)}</b></span>
      <span><span class="ts-label">事件创建（首抓）</span><b>${fmtTime(e.created_at)}</b></span>
      <span><span class="ts-label">更新时间</span><b>${fmtTime(e.updated_at)}</b></span>
    </div>

    <div class="section">
      <h4>当前结论</h4>
      <div class="panel statement">${esc(concl.statement || "（暂无结论）")}</div>
    </div>

    <div class="section">
      <h4>命题与判定</h4>
      ${propCards || `<div class="panel">暂无命题</div>`}
    </div>

    <div class="section">
      <h4>证据时间线（${d.claims.length} 条主张）</h4>
      <div class="timeline">${claimsHtml}</div>
    </div>

    <div class="section">
      <h4>版本演化（${d.versions.length} 个版本）</h4>
      <div class="ver-list">${versionsHtml}</div>
    </div>

    <div class="section">
      <h4>原始文档（${d.documents.length} 篇）</h4>
      <div class="panel">${docsHtml}</div>
    </div>`;
}

/* ---------- 通知 ---------- */
async function loadNotifs() {
  const data = await api("/api/notifications");
  const unread = data.notifications.filter((n) => !n.read).length;
  const badge = $("unreadBadge");
  badge.textContent = unread;
  badge.classList.toggle("hidden", unread === 0);
  $("notifList").innerHTML = data.notifications.map((n) => `
    <div class="notif ${n.read ? "" : "unread"}" data-id="${n.notif_id}" data-event="${esc(n.event_id || "")}">
      <div class="n-title">${esc(n.title)}</div>
      <div class="n-msg">${esc(n.message)}</div>
      <div class="n-time">${fmtTime(n.created_at)}</div>
    </div>`).join("") || `<div class="empty">暂无通知</div>`;
  document.querySelectorAll(".notif").forEach((el) => el.addEventListener("click", async () => {
    await api(`/api/notifications/${el.dataset.id}/read`, { method: "POST" });
    closeDrawer();
    if (el.dataset.event) {
      loadEvents(el.dataset.event);
    }
  }));
}
function openDrawer() { $("notifDrawer").classList.remove("hidden"); loadNotifs(); }
function closeDrawer() { $("notifDrawer").classList.add("hidden"); }

/* ---------- 在线入库（主链路操作） ---------- */
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
      ${r.notifications.length ? `<div class="ir-item">🔔 触发 ${r.notifications.length} 条通知（${r.notifications.map((n) => esc(n.message)).join("；")}）</div>` : `<div class="ir-item">🔕 未改变任何事件结论，无新通知</div>`}
    `;
    loadEvents(); loadStats();
  } catch (e) {
    alert("提交失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "提交并分析";
  }
}

/* ---------- 启动 ---------- */
$("bellBtn").addEventListener("click", openDrawer);
$("closeNotif").addEventListener("click", closeDrawer);
$("addDocBtn").addEventListener("click", openIngest);
$("closeIngest").addEventListener("click", closeIngest);
$("cancelIngest").addEventListener("click", closeIngest);
$("submitIngest").addEventListener("click", submitIngest);
loadStats();
loadEvents();
