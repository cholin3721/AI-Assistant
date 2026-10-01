"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let sessionId = Math.random().toString(36).slice(2);
let state = null;
let busy = false;

/* ---------- 공통 ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
    body: opts.body instanceof FormData ? opts.body : opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.detail || `요청 실패 (${res.status})`);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}
function store(key, val) {
  try { if (val === undefined) return JSON.parse(localStorage.getItem(key) || "null"); localStorage.setItem(key, JSON.stringify(val)); }
  catch (_) { return null; }
}
function setResult(el, cls, text) { el.className = "result " + cls; el.textContent = text; }

/* ---------- 마크다운(간단) ---------- */
function esc(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  return s;
}
function md(text) {
  const lines = text.replace(/\r/g, "").split("\n");
  let html = "", list = null, para = [], code = null;
  const flushPara = () => { if (para.length) { html += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
  const flushList = () => { if (list) { html += `<${list.type}>` + list.items.map((i) => `<li>${inline(i)}</li>`).join("") + `</${list.type}>`; list = null; } };
  for (const line of lines) {
    if (code !== null) {
      if (/^```/.test(line)) { html += "<pre><code>" + esc(code.join("\n")) + "</code></pre>"; code = null; } else code.push(line);
      continue;
    }
    if (/^```/.test(line)) { flushPara(); flushList(); code = []; continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)/))) { flushPara(); flushList(); html += `<h3>${inline(m[2])}</h3>`; continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); continue; }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    flushList(); para.push(line);
  }
  if (code !== null) html += "<pre><code>" + esc(code.join("\n")) + "</code></pre>";
  flushPara(); flushList();
  return html;
}

/* ---------- 상태 ---------- */
async function refreshStatus() {
  state = await api("/api/status");
  const g = $("#conn-gemini"), go = $("#conn-google");
  g.className = "conn " + (state.gemini.set ? "on" : "off");
  g.querySelector("small").textContent = state.gemini.set ? `연결됨 · ${state.gemini.model}` : "키 입력 필요 (눌러서 설정)";
  const gs = state.google;
  go.className = "conn " + (gs.connected ? "on" : "off");
  go.querySelector("small").textContent = gs.connected ? (gs.email || "연결됨") : gs.error ? "다시 로그인 필요" : "미연동 (눌러서 연결)";
  const tg = state.telegram, dc = state.discord, ct = $("#conn-tg");
  const linked = [dc.linked && "디스코드", tg.linked && "텔레그램"].filter(Boolean);
  const pending = (dc.token_set && !dc.linked) || (tg.token_set && !tg.linked);
  ct.className = "conn " + (linked.length ? "on" : "off");
  ct.querySelector("small").textContent = linked.length ? `${linked.join(" · ")} 연결됨` : pending ? "연결 마무리 필요" : "미연결 (눌러서 설정)";
  updateSchedBadge();
  updateBell();
  $("#todo-count").textContent = state.todos.open;
  return state;
}

/* ---------- 채팅 ---------- */
function addMsg(role, html, tools) {
  $("#empty")?.remove();
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const b = document.createElement("div");
  b.className = "bubble";
  if (role === "user") b.textContent = html; else b.innerHTML = html;
  if (tools && tools.length) {
    const t = document.createElement("div"); t.className = "tools";
    const seen = new Map();
    tools.forEach((x) => seen.set(x.label, (seen.get(x.label) || x.ok) && x.ok));
    seen.forEach((ok, label) => { const c = document.createElement("span"); c.className = "chip" + (ok ? "" : " fail"); c.textContent = label; t.appendChild(c); });
    b.appendChild(t);
  }
  wrap.appendChild(b);
  $("#messages").appendChild(wrap);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return wrap;
}
async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  if (!state?.gemini.set) { openWizard(2); return; }
  busy = true; $("#send").disabled = true;
  addMsg("user", text);
  const typing = addMsg("bot", '<div class="typing"><span></span><span></span><span></span></div>');
  try {
    const r = await api("/api/chat", { method: "POST", body: { session_id: sessionId, message: text } });
    typing.remove();
    addMsg("bot", md(r.reply), r.tools);
    speak(r.reply);
    const used = (n) => (r.tools || []).some((t) => t.name === n && t.ok);
    if (used("find_events_in_emails") || used("find_events_in_notices")) openSched(false);
  } catch (e) {
    typing.remove();
    addMsg("error", esc(e.message));
  } finally {
    busy = false; $("#send").disabled = false; $("#input").focus();
    refreshStatus().catch(() => {});
  }
}
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#input").value; $("#input").value = ""; autosize(); send(v); });
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } });
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }
$("#input").addEventListener("input", autosize);
$$(".quick, .ex").forEach((b) => b.addEventListener("click", () => send(b.dataset.q)));
$("#btn-new").addEventListener("click", async () => {
  await api("/api/chat/reset", { method: "POST", body: { session_id: sessionId, message: "-" } }).catch(() => {});
  sessionId = Math.random().toString(36).slice(2);
  location.reload();
});
$("#conn-gemini").addEventListener("click", () => (state?.gemini.set ? openSettings("ai") : openWizard(2)));
$("#conn-google").addEventListener("click", () => (state?.google.connected ? openSettings("google") : openWizard(3)));
$("#conn-tg").addEventListener("click", () => openSettings("tg"));

/* ---------- 설정 마법사 ---------- */
let page = 0;
const LAST = 4;
function openWizard(p = 0) { $("#wizard").classList.remove("hidden"); $("#settings").classList.add("hidden"); showPage(p); }
function closeWizard() { $("#wizard").classList.add("hidden"); }
function showPage(p) {
  page = p;
  $$(".page").forEach((el) => el.classList.toggle("hidden", +el.dataset.page !== p));
  $$("#steps li").forEach((li, i) => { li.className = i === p ? "active" : i < p ? "done" : ""; });
  $("#wiz-prev").style.visibility = p === 0 ? "hidden" : "visible";
  $("#wiz-skip").classList.toggle("hidden", !(p === 1 || p === 3));
  $("#wiz-next").textContent = p === 0 ? "시작하기" : p === LAST ? "AI 비서 시작" : "다음";
  if (p === 2) updateKeyPage();
  if (p === 3) updateGooglePage();
  if (p === LAST) {
    const parts = ["Gemini 연결됨"];
    parts.push(state.google.connected ? `구글 연동됨 (${state.google.email || "계정"})` : "구글 연동은 나중에 설정에서 할 수 있어요");
    parts.push("학교 공지 사용 가능");
    $("#done-summary").textContent = parts.join(" · ");
  }
}
function updateKeyPage() {
  if (state.gemini.set) setResult($("#key-result"), "ok", `연결됨 (${state.gemini.masked}) · 모델: ${state.gemini.model}. 바꾸려면 새 키를 넣고 확인을 누르세요.`);
}
async function saveProfile(prefix) {
  const profile = {};
  ["name", "department", "grade", "interests"].forEach((k) => (profile[k] = $(`#${prefix}-${k}`).value.trim()));
  await api("/api/settings", { method: "POST", body: { profile } });
}
function fillProfile(prefix) { ["name", "department", "grade", "interests"].forEach((k) => ($(`#${prefix}-${k}`).value = state.profile[k] || "")); }

$("#wiz-prev").addEventListener("click", () => showPage(Math.max(0, page - 1)));
$("#wiz-skip").addEventListener("click", () => showPage(page + 1));
$("#wiz-next").addEventListener("click", async () => {
  if (page === 1) await saveProfile("p").catch(() => {});
  if (page === 2 && !state.gemini.set) {
    if ($("#gemini-key").value.trim()) { await checkKey(); if (!state.gemini.set) return; }
    else { setResult($("#key-result"), "bad", "API 키를 넣고 「확인」을 눌러주세요. (이 단계는 꼭 필요해요)"); return; }
  }
  if (page === LAST) {
    await api("/api/settings", { method: "POST", body: { setup_done: true } });
    closeWizard(); $("#input").focus(); return;
  }
  await refreshStatus();
  showPage(page + 1);
});

/* Gemini 키 */
$("#key-eye").addEventListener("click", () => { const i = $("#gemini-key"); i.type = i.type === "password" ? "text" : "password"; $("#key-eye").textContent = i.type === "password" ? "보기" : "숨기기"; });
async function checkKey(inputSel = "#gemini-key", resultSel = "#key-result") {
  const key = $(inputSel).value.trim();
  const out = $(resultSel);
  if (!key) { setResult(out, "bad", "키를 붙여넣어 주세요."); return false; }
  if (/\s/.test(key)) { setResult(out, "bad", "키 중간에 공백이 있어요. 다시 복사해주세요."); return false; }
  setResult(out, "wait", "확인하는 중…");
  try {
    const r = await api("/api/gemini/key", { method: "POST", body: { key } });
    setResult(out, "ok", `연결 성공! 사용할 모델: ${r.model}`);
    $(inputSel).value = "";
    await refreshStatus();
    return true;
  } catch (e) { setResult(out, "bad", e.message); return false; }
}
$("#key-check").addEventListener("click", () => checkKey());
$("#gemini-key").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); checkKey(); } });

/* 구글 연동 체크리스트 */
const G_KEY = "g-steps-done";
function updateGooglePage() {
  const saved = store(G_KEY) || [];
  const items = $$("#g-steps > li");
  items.forEach((li, i) => {
    const cb = li.querySelector("input[type=checkbox]");
    if (i < 5) cb.checked = !!saved[i];
    if (i === 5) cb.checked = state.google.client_uploaded;
    if (i === 6) cb.checked = state.google.connected;
    li.classList.toggle("ok", cb.checked);
  });
  const drop = $("#drop");
  drop.classList.toggle("done", state.google.client_uploaded);
  if (state.google.client_uploaded) $("#drop-text").innerHTML = "<b>열쇠 파일 등록됨</b> · 바꾸려면 다시 올리세요";
  const r = $("#g-result");
  if (state.google.connected) setResult(r, "ok", `연동 완료! ${state.google.email || ""}`);
  else if (state.google.error) setResult(r, "bad", state.google.error);
  else setResult(r, "", "");
  $("#wiz-next").textContent = state.google.connected ? "다음" : "다음 (구글 없이 진행)";
}
$$("#g-steps > li").forEach((li, i) => {
  const cb = li.querySelector("input[type=checkbox]");
  cb.addEventListener("change", () => {
    if (i >= 5) { cb.checked = i === 5 ? state.google.client_uploaded : state.google.connected; return; }
    const saved = store(G_KEY) || []; saved[i] = cb.checked; store(G_KEY, saved);
    li.classList.toggle("ok", cb.checked);
  });
});
async function uploadClient(file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  const r = $("#g-result");
  setResult(r, "wait", "파일 확인 중…");
  try {
    await api("/api/google/client", { method: "POST", body: fd });
    await refreshStatus(); updateGooglePage();
    setResult(r, "ok", "열쇠 파일 등록 완료! 이제 「구글 로그인」을 눌러주세요.");
  } catch (e) { setResult(r, "bad", e.message); }
}
$("#client-file").addEventListener("change", (e) => uploadClient(e.target.files[0]));
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => uploadClient(e.dataTransfer.files[0]));

async function googleLogin(resultEl) {
  try {
    await api("/api/google/login", { method: "POST" });
  } catch (e) { if (resultEl) setResult(resultEl, "bad", e.message); else toast(e.message); return; }
  if (resultEl) setResult(resultEl, "wait", "새 브라우저 창에서 로그인을 진행해주세요… (창이 안 보이면 작업표시줄을 확인)");
  else toast("새 브라우저 창에서 로그인을 진행해주세요");
  const started = Date.now();
  while (Date.now() - started < 310000) {
    await new Promise((r) => setTimeout(r, 2000));
    const s = await refreshStatus();
    if (!s.google.login_in_progress) break;
  }
  if (!$("#wizard").classList.contains("hidden") && page === 3) updateGooglePage();
  if (!$("#settings").classList.contains("hidden")) fillSettings();
  if (state.google.connected) toast("구글 연동 완료!");
}
$("#g-login").addEventListener("click", () => googleLogin($("#g-result")));

/* ---------- 설정 ---------- */
async function fillSettings() {
  await refreshStatus();
  fillProfile("s");
  $("#s-key-mask").textContent = state.gemini.masked || "없음";
  const sel = $("#s-model"); sel.innerHTML = "";
  try {
    const r = await api("/api/gemini/models");
    (r.models.length ? r.models : [state.gemini.model]).forEach((m) => { const o = document.createElement("option"); o.value = o.textContent = m; sel.appendChild(o); });
    sel.value = r.current || state.gemini.model;
  } catch (e) { const o = document.createElement("option"); o.textContent = state.gemini.model || "-"; sel.appendChild(o); }
  const g = state.google;
  $("#s-auto-scan").checked = !!state.schedule?.enabled;
  $("#s-google-state").textContent = g.connected ? `연결됨: ${g.email || "구글 계정"}` : g.client_uploaded ? "열쇠 파일은 등록됨 · 로그인 필요" : "아직 연동 안 됨 · 「연동 가이드 열기」로 시작하세요";
}
function openSettings(tab = "profile") { $("#settings").classList.remove("hidden"); showTab(tab); fillSettings(); }
function showTab(tab) {
  $$("#set-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$("#settings .tab").forEach((t) => t.classList.toggle("hidden", t.dataset.tab !== tab));
  if (tab === "ai") loadMemory();
  if (tab === "auto") fillAuto();
  if (tab === "tg") { fillDiscord(); fillTelegram(); }
  if (tab === "tt") loadTimetable();
}
$$("#set-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#btn-settings").addEventListener("click", () => openSettings());
$("#set-close").addEventListener("click", () => $("#settings").classList.add("hidden"));
$("#s-save-profile").addEventListener("click", async () => { await saveProfile("s"); toast("내 정보를 저장했어요"); });
$("#s-key-save").addEventListener("click", async () => {
  const key = $("#s-key").value.trim(); if (!key) return toast("새 키를 붙여넣어 주세요");
  try { const r = await api("/api/gemini/key", { method: "POST", body: { key } }); $("#s-key").value = ""; toast(`키 변경 완료 · ${r.model}`); fillSettings(); }
  catch (e) { toast(e.message); }
});
$("#s-model").addEventListener("change", async (e) => { await api("/api/settings", { method: "POST", body: { model: e.target.value } }); toast(`모델 변경: ${e.target.value}`); refreshStatus(); });
$("#s-google-login").addEventListener("click", () => (state.google.client_uploaded ? googleLogin(null) : openWizard(3)));
$("#s-google-guide").addEventListener("click", () => openWizard(3));
$("#s-google-off").addEventListener("click", async () => { await api("/api/google/disconnect", { method: "POST", body: { remove_client: false } }); toast("구글 연결을 해제했어요"); fillSettings(); });

$("#s-auto-scan").addEventListener("change", async (e) => {
  await api("/api/settings", { method: "POST", body: { auto_scan: e.target.checked } });
  toast(e.target.checked ? "새 메일에서 일정을 자동으로 찾을게요" : "자동 찾기를 껐어요");
});

/* ---------- 메일 속 일정 ---------- */
let lastPending = null;
function updateSchedBadge() {
  const sc = state?.schedule; if (!sc) return;
  const badge = $("#sched-badge");
  badge.textContent = sc.pending; badge.classList.toggle("hidden", !sc.pending);
  let meta = sc.running ? "메일·공지 확인 중…" : !state.google.connected ? "메일 일정은 구글 연동 후 사용할 수 있어요" :
    sc.enabled ? "새 메일·공지에서 일정을 자동으로 찾아요" : "자동 찾기 꺼짐 · 눌러서 확인";
  $("#sched-meta").textContent = meta;
  if (lastPending !== null && sc.pending > lastPending) toast(`메일에서 새 일정 후보 ${sc.pending - lastPending}개를 찾았어요`);
  lastPending = sc.pending;
}
const WD = "일월화수목금토";
function parseLocal(v) {
  const m = (v || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  if (!m) return null;
  return { d: new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)), time: !!m[4] };
}
function fmtDay(d) { return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WD[d.getDay()]})`; }
function fmtTime(d) { return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }
function fmtWhen(c) {
  const s = parseLocal(c.start), e = parseLocal(c.end);
  if (!s) return c.start;
  if (!s.time) {
    if (e) { const last = new Date(e.d); last.setDate(last.getDate() - 1); if (last > s.d) return `${fmtDay(s.d)} ~ ${fmtDay(last)} · 하루 종일`; }
    return `${fmtDay(s.d)} · 하루 종일`;
  }
  if (!e) return `${fmtDay(s.d)} ${fmtTime(s.d)}`;
  const sameDay = e.d.toDateString() === s.d.toDateString();
  return `${fmtDay(s.d)} ${fmtTime(s.d)} ~ ${sameDay ? "" : fmtDay(e.d) + " "}${fmtTime(e.d)}`;
}
function cardHTML(c) {
  const conf = c.confidence >= 0.7 ? '<span class="conf high">확실</span>' : '<span class="conf low">확인 필요</span>';
  const allDay = c.start.length === 10;
  return `
  <div class="card-top"><b class="t">${esc(c.title)}</b>${conf}</div>
  <div class="when">${esc(fmtWhen(c))}${c.location ? " · " + esc(c.location) : ""}</div>
  ${c.evidence ? `<blockquote>“${esc(c.evidence)}”</blockquote>` : ""}
  <div class="src">${esc(c.mail_from.replace(/<.*>/, "").trim())} · ${esc(c.mail_subject)} · <a href="${esc(c.mail_link)}" target="_blank" rel="noopener">${esc(c.link_text || "메일 보기")}</a></div>
  <div class="edit hidden">
    <label class="full">제목<input data-f="title" value="${esc(c.title)}"></label>
    <label>시작<input data-f="start" type="${allDay ? "date" : "datetime-local"}" value="${esc(c.start)}"></label>
    <label>종료<input data-f="end" type="${allDay ? "date" : "datetime-local"}" value="${esc(c.end)}"></label>
    <label class="full">장소<input data-f="location" value="${esc(c.location || "")}"></label>
    <label class="check full"><input type="checkbox" data-f="allday" ${allDay ? "checked" : ""}> 하루 종일 (마감일 등)</label>
  </div>
  <div class="actions">
    <button class="sm" data-a="add">캘린더에 추가</button>
    <button class="ghost sm" data-a="edit">수정</button>
    <button class="ghost sm" data-a="ignore">무시</button>
  </div>`;
}
function renderCandidates(list) {
  const box = $("#sched-list"); box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = `<div class="empty-list">${state.google.connected ? "확인할 일정 후보가 없어요.<br>「새 메일 확인」을 눌러보세요." : "구글 연동을 먼저 해주세요."}</div>`;
    return;
  }
  list.forEach((c) => {
    const el = document.createElement("div"); el.className = "card"; el.innerHTML = cardHTML(c);
    const edit = el.querySelector(".edit");
    el.querySelector('[data-f="allday"]').addEventListener("change", (e) => {
      ["start", "end"].forEach((f) => {
        const i = edit.querySelector(`[data-f="${f}"]`); const v = i.value;
        i.type = e.target.checked ? "date" : "datetime-local";
        i.value = e.target.checked ? v.slice(0, 10) : (v.length === 10 ? v + (f === "start" ? "T09:00" : "T10:00") : v);
      });
    });
    el.querySelector('[data-a="edit"]').addEventListener("click", (e) => {
      edit.classList.toggle("hidden"); e.target.textContent = edit.classList.contains("hidden") ? "수정" : "수정 닫기";
    });
    el.querySelector('[data-a="ignore"]').addEventListener("click", async () => {
      await api(`/api/schedule/${c.id}/ignore`, { method: "POST" }).catch((e) => toast(e.message));
      el.classList.add("fade"); setTimeout(() => { el.remove(); if (!$("#sched-list .card")) renderCandidates([]); }, 400);
      refreshStatus();
    });
    el.querySelector('[data-a="add"]').addEventListener("click", async (e) => {
      const btn = e.target; btn.disabled = true; btn.textContent = "추가하는 중…";
      const body = {};
      if (!edit.classList.contains("hidden")) ["title", "start", "end", "location"].forEach((f) => (body[f] = edit.querySelector(`[data-f="${f}"]`).value));
      try {
        const r = await api(`/api/schedule/${c.id}/add`, { method: "POST", body });
        el.classList.add("done");
        el.innerHTML = `<div class="card-top"><b>${esc(r.title)}</b><span class="conf high">추가됨</span></div>
          <div class="when">${esc(fmtWhen(r))}</div>
          ${r.calendar_link ? `<a href="${esc(r.calendar_link)}" target="_blank" rel="noopener">캘린더에서 보기</a>` : ""}`;
        toast("캘린더에 추가했어요");
        refreshStatus();
      } catch (err) { btn.disabled = false; btn.textContent = "캘린더에 추가"; toast(err.message); }
    });
    box.appendChild(el);
  });
}
async function loadCandidates() {
  const r = await api("/api/schedule/candidates");
  renderCandidates(r.candidates);
  return r.candidates;
}
async function scanMail(force = false) {
  const out = $("#sched-result"), btn = $("#sched-scan");
  btn.disabled = true; setResult(out, "wait", "메일을 읽고 일정을 찾는 중… (메일 양에 따라 10~30초)");
  try {
    const r = await api("/api/schedule/scan", { method: "POST", body: { days: +$("#sched-days").value, force } });
    setResult(out, r.found ? "ok" : "", r.message);
    renderCandidates(r.candidates);
  } catch (e) { setResult(out, "bad", e.message); }
  finally { btn.disabled = false; refreshStatus(); }
}
async function openSched(autoScan = true) {
  $("#sched").classList.remove("hidden");
  setResult($("#sched-result"), "", "");
  const list = await loadCandidates().catch(() => []);
  if (autoScan && !list.length && state.google.connected && state.gemini.set) scanMail();
}
$("#btn-sched").addEventListener("click", () => openSched(state.google.connected));
$("#sched-close").addEventListener("click", () => $("#sched").classList.add("hidden"));
$("#sched-scan").addEventListener("click", () => scanMail(false));
$("#sched-force").addEventListener("click", (e) => { e.preventDefault(); scanMail(true); });
$("#sched-scan-notice").addEventListener("click", () => scanNotices());
async function scanNotices() {
  const out = $("#sched-result"), btn = $("#sched-scan-notice");
  btn.disabled = true; setResult(out, "wait", "학교 공지와 첨부파일을 읽는 중… (20~60초)");
  try {
    const r = await api("/api/schedule/scan_notices", { method: "POST", body: { days: +$("#sched-days").value } });
    setResult(out, r.found ? "ok" : "", r.message); renderCandidates(r.candidates);
  } catch (e) { setResult(out, "bad", e.message); }
  finally { btn.disabled = false; refreshStatus(); }
}
setInterval(() => refreshStatus().catch(() => {}), 30000);


/* ---------- 알림함 ---------- */
let lastUnread = null;
function updateBell() {
  const n = state.notifications.unread, b = $("#bell-badge");
  b.textContent = n; b.classList.toggle("hidden", !n);
  if (lastUnread !== null && n > lastUnread) {
    api("/api/notifications").then((r) => {
      const latest = r.items[0]; if (!latest) return;
      toast(`새 알림: ${latest.title}`);
      try { if ("Notification" in window && Notification.permission === "granted") new Notification(latest.title, { body: latest.body.replace(/[*#\[\]]/g, "").slice(0, 120) }); } catch (_) {}
    }).catch(() => {});
  }
  lastUnread = n;
}
const KIND = { briefing: "브리핑", weekly: "주간 회고", reminder: "마감", schedule: "일정 후보", system: "안내" };
function timeAgo(ts) {
  const d = (Date.now() / 1000 - ts) | 0;
  if (d < 60) return "방금"; if (d < 3600) return `${(d / 60) | 0}분 전`; if (d < 86400) return `${(d / 3600) | 0}시간 전`;
  const t = new Date(ts * 1000); return `${t.getMonth() + 1}/${t.getDate()}`;
}
async function openNotif() {
  $("#notif").classList.remove("hidden");
  const r = await api("/api/notifications");
  const box = $("#notif-list"); box.innerHTML = "";
  if (!r.items.length) box.innerHTML = '<div class="empty-list">아직 알림이 없어요.<br>설정 &gt; 자동 알림에서 「지금 받아보기」로 브리핑을 받아보세요.</div>';
  r.items.forEach((n) => {
    const el = document.createElement("div"); el.className = "notif-item" + (n.read ? "" : " unread");
    el.innerHTML = `<div class="notif-head"><b><span class="kind">${esc(KIND[n.kind] || "알림")}</span>${esc(n.title)}</b><small>${timeAgo(n.created)}</small></div><div class="notif-body">${md(n.body)}</div>`;
    box.appendChild(el);
  });
  if (r.unread) { await api("/api/notifications/read", { method: "POST" }); lastUnread = 0; refreshStatus(); }
}
$("#btn-bell").addEventListener("click", openNotif);
$("#notif-close").addEventListener("click", () => $("#notif").classList.add("hidden"));

/* ---------- 할 일 ---------- */
function dueBadge(due) {
  if (!due) return "";
  const p = parseLocal(due); if (!p) return "";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const left = Math.round((p.d - today) / 86400000);
  const cls = left < 0 ? "over" : left <= 2 ? "soon" : "";
  const label = left < 0 ? `${-left}일 지남` : left === 0 ? "오늘" : `D-${left}`;
  return `<span class="due ${cls}" title="${esc(due)}">${label} · ${p.d.getMonth() + 1}/${p.d.getDate()}</span>`;
}
async function loadTodos() {
  const r = await api("/api/todos?include_done=true");
  const showDone = $("#todo-show-done").checked;
  const list = $("#todo-list"); list.innerHTML = "";
  const items = r.items.filter((x) => showDone || !x.done);
  if (!items.length) list.innerHTML = '<li class="empty-list">할 일이 없어요.</li>';
  items.forEach((x) => {
    const li = document.createElement("li"); li.className = x.done ? "done" : "";
    li.innerHTML = `<input type="checkbox" ${x.done ? "checked" : ""}><span class="t">${esc(x.title)}${x.link ? ` · <a href="${esc(x.link)}" target="_blank" rel="noopener">링크</a>` : ""}</span>${x.done ? "" : dueBadge(x.due)}<button class="x" title="삭제">×</button>`;
    li.querySelector("input").addEventListener("change", async () => { await api(`/api/todos/${x.id}/toggle`, { method: "POST" }); loadTodos(); refreshStatus(); });
    li.querySelector(".x").addEventListener("click", async () => { await api(`/api/todos/${x.id}`, { method: "DELETE" }); loadTodos(); refreshStatus(); });
    list.appendChild(li);
  });
}
async function addTodo() {
  const title = $("#todo-title").value.trim(); if (!title) return;
  try { await api("/api/todos", { method: "POST", body: { title, due: $("#todo-due").value } }); $("#todo-title").value = ""; $("#todo-due").value = ""; loadTodos(); refreshStatus(); }
  catch (e) { toast(e.message); }
}
$("#btn-todo").addEventListener("click", () => { $("#todo").classList.remove("hidden"); loadTodos(); $("#todo-title").focus(); });
$("#todo-close").addEventListener("click", () => $("#todo").classList.add("hidden"));
$("#todo-add").addEventListener("click", addTodo);
$("#todo-title").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) addTodo(); });
$("#todo-show-done").addEventListener("change", loadTodos);

/* ---------- 기억 ---------- */
async function loadMemory() {
  const r = await api("/api/memory");
  const ul = $("#mem-list"); ul.innerHTML = "";
  if (!r.facts.length) ul.innerHTML = '<li class="muted">아직 기억하는 게 없어요.</li>';
  r.facts.forEach((f) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(f.text)}</span><button class="x" title="지우기">×</button>`;
    li.querySelector(".x").addEventListener("click", async () => { await api(`/api/memory/${f.id}`, { method: "DELETE" }); loadMemory(); });
    ul.appendChild(li);
  });
}
$("#mem-add").addEventListener("click", async () => {
  const text = $("#mem-input").value.trim(); if (!text) return;
  try { await api("/api/memory", { method: "POST", body: { text } }); $("#mem-input").value = ""; loadMemory(); } catch (e) { toast(e.message); }
});

/* ---------- 자동 알림 ---------- */
function fillAuto() {
  const a = state.automation;
  $$("[data-auto]").forEach((el) => {
    const v = a[el.dataset.auto];
    if (el.type === "checkbox") el.checked = !!v; else el.value = String(v);
  });
  $("#s-auto-scan").checked = !!state.schedule.enabled;
  updateNotifBtn();
}
$$("[data-auto]").forEach((el) => el.addEventListener("change", async () => {
  const k = el.dataset.auto;
  const v = el.type === "checkbox" ? el.checked : k === "weekly_day" ? +el.value : el.value;
  await api("/api/settings", { method: "POST", body: { automation: { [k]: v } } });
  toast("저장했어요"); refreshStatus();
}));
$$("[data-run]").forEach((b) => b.addEventListener("click", async () => {
  const out = $("#auto-result"); b.disabled = true;
  setResult(out, "wait", b.dataset.run === "briefing" || b.dataset.run === "weekly" ? "만드는 중… (메일·일정·공지를 확인해서 20~40초)" : "확인하는 중…");
  try { const r = await api("/api/automation/run", { method: "POST", body: { job: b.dataset.run } }); setResult(out, "ok", r.message); refreshStatus(); }
  catch (e) { setResult(out, "bad", e.message); }
  finally { b.disabled = false; }
}));
function updateNotifBtn() {
  const b = $("#btn-browser-notif");
  if (!("Notification" in window)) { b.disabled = true; b.textContent = "이 브라우저는 지원하지 않아요"; return; }
  b.textContent = Notification.permission === "granted" ? "브라우저 알림 켜짐" : Notification.permission === "denied" ? "차단됨 (주소창 자물쇠에서 허용)" : "브라우저 알림 켜기";
  b.disabled = Notification.permission !== "default";
}
$("#btn-browser-notif").addEventListener("click", async () => { try { await Notification.requestPermission(); } catch (_) {} updateNotifBtn(); });

/* ---------- 디스코드 ---------- */
let dcPoll = null;
function fillDiscord() {
  const d = state.discord;
  $("#dc-setup").classList.toggle("hidden", d.token_set);
  $("#dc-link").classList.toggle("hidden", !(d.token_set && !d.linked));
  $("#dc-done").classList.toggle("hidden", !d.linked);
  setResult($("#dc-result"), d.error ? "bad" : "", d.error || "");
  if (d.token_set && !d.linked) {
    $("#dc-bot").textContent = d.bot_username; $("#dc-invite").href = d.invite_url; $("#dc-code").textContent = d.link_code;
    $("#dc-online").textContent = d.online ? "(봇 접속됨)" : "(봇 접속 중…)";
    clearInterval(dcPoll);
    dcPoll = setInterval(async () => {
      if ($("#settings").classList.contains("hidden")) return clearInterval(dcPoll);
      await refreshStatus();
      $("#dc-online").textContent = state.discord.online ? "(봇 접속됨)" : "(봇 접속 중…)";
      if (state.discord.error) setResult($("#dc-result"), "bad", state.discord.error);
      if (state.discord.linked) { clearInterval(dcPoll); fillDiscord(); toast("디스코드 연결 완료!"); }
    }, 3000);
  }
  if (d.linked) $("#dc-done-text").textContent = `${d.bot_username} 연결됨${d.owner_name ? ` · 주인: ${d.owner_name}` : ""}${d.online ? "" : " (봇 접속 중…)"}`;
}
$("#dc-save").addEventListener("click", async () => {
  const text = $("#dc-token").value.trim(); if (!text) return setResult($("#dc-result"), "bad", "토큰을 붙여넣어 주세요.");
  setResult($("#dc-result"), "wait", "확인하는 중…");
  try { await api("/api/discord/token", { method: "POST", body: { text } }); $("#dc-token").value = ""; await refreshStatus(); fillDiscord(); }
  catch (e) { setResult($("#dc-result"), "bad", e.message); }
});
$("#dc-token").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#dc-save").click(); });
$("#dc-test").addEventListener("click", async () => { try { await api("/api/discord/test", { method: "POST" }); toast("보냈어요. 디스코드 DM을 확인해보세요"); } catch (e) { toast(e.message); } });
async function dcReset() { await api("/api/discord/disconnect", { method: "POST" }); await refreshStatus(); fillDiscord(); }
$("#dc-off").addEventListener("click", dcReset);
$("#dc-cancel").addEventListener("click", dcReset);

/* ---------- 텔레그램 ---------- */
let tgPoll = null;
function fillTelegram() {
  const t = state.telegram;
  if (t.token_set) $("#tg-details").open = true;
  $("#tg-setup").classList.toggle("hidden", t.token_set);
  $("#tg-link").classList.toggle("hidden", !(t.token_set && !t.linked));
  $("#tg-done").classList.toggle("hidden", !t.linked);
  setResult($("#tg-result"), t.error ? "bad" : "", t.error || "");
  if (t.token_set && !t.linked) {
    $("#tg-bot").textContent = "@" + t.bot_username; $("#tg-link-btn").href = t.link_url;
    clearInterval(tgPoll);
    tgPoll = setInterval(async () => {
      if ($("#settings").classList.contains("hidden")) return clearInterval(tgPoll);
      await refreshStatus();
      if (state.telegram.linked) { clearInterval(tgPoll); fillTelegram(); toast("텔레그램 연결 완료!"); }
    }, 3000);
  }
  if (t.linked) $("#tg-done-text").textContent = `@${t.bot_username} 연결됨${t.owner_name ? ` · ${t.owner_name}님의 대화방` : ""}`;
}
$("#tg-save").addEventListener("click", async () => {
  const text = $("#tg-token").value.trim(); if (!text) return setResult($("#tg-result"), "bad", "토큰을 붙여넣어 주세요.");
  setResult($("#tg-result"), "wait", "확인하는 중…");
  try { await api("/api/telegram/token", { method: "POST", body: { text } }); $("#tg-token").value = ""; await refreshStatus(); fillTelegram(); }
  catch (e) { setResult($("#tg-result"), "bad", e.message); }
});
$("#tg-test").addEventListener("click", async () => { try { await api("/api/telegram/test", { method: "POST" }); toast("보냈어요. 폰을 확인해보세요"); } catch (e) { toast(e.message); } });
$("#tg-off").addEventListener("click", async () => { await api("/api/telegram/disconnect", { method: "POST" }); await refreshStatus(); fillTelegram(); });

/* ---------- 시간표 ---------- */
const DAYNAMES = "월화수목금토일";
function ttRow(c = { day: 0, start: "09:00", end: "10:00", title: "", place: "" }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><select>${[...DAYNAMES].map((d, i) => `<option value="${i}" ${i === +c.day ? "selected" : ""}>${d}</option>`).join("")}</select></td>
    <td><input type="time" value="${esc(c.start)}"></td><td><input type="time" value="${esc(c.end)}"></td>
    <td><input value="${esc(c.title)}" placeholder="과목명"></td><td><input value="${esc(c.place || "")}" placeholder="강의실"></td>
    <td><button class="x" title="삭제">×</button></td>`;
  tr.querySelector(".x").addEventListener("click", () => tr.remove());
  $("#tt-body").appendChild(tr);
}
async function loadTimetable(rows) {
  if (!rows) rows = (await api("/api/timetable")).classes;
  $("#tt-body").innerHTML = "";
  rows.forEach(ttRow);
  if (!rows.length) setResult($("#tt-result"), "", "등록된 수업이 없어요. 이미지를 올리거나 직접 추가하세요.");
}
function ttCollect() {
  return $$("#tt-body tr").map((tr) => {
    const [day, start, end, title, place] = tr.querySelectorAll("select, input");
    return { day: +day.value, start: start.value, end: end.value, title: title.value.trim(), place: place.value.trim() };
  });
}
$("#tt-add").addEventListener("click", () => ttRow());
$("#tt-save").addEventListener("click", async () => {
  const rows = ttCollect();
  const r = await api("/api/timetable", { method: "PUT", body: { classes: rows } });
  const dropped = rows.filter((x) => x.title).length - r.classes.length;
  setResult($("#tt-result"), "ok", `수업 ${r.classes.length}개 저장했어요.` + (dropped > 0 ? ` (시간이 잘못된 ${dropped}개는 뺐어요)` : ""));
  loadTimetable(r.classes);
});
async function ttUpload(file) {
  if (!file) return;
  const fd = new FormData(); fd.append("file", file);
  setResult($("#tt-result"), "wait", "AI가 시간표를 읽는 중… (10~20초)");
  try {
    const r = await api("/api/timetable/extract", { method: "POST", body: fd });
    loadTimetable(r.classes);
    setResult($("#tt-result"), "ok", `수업 ${r.classes.length}개를 읽었어요. 틀린 곳을 고친 뒤 「시간표 저장」을 눌러주세요.`);
  } catch (e) { setResult($("#tt-result"), "bad", e.message); }
}
$("#tt-file").addEventListener("change", (e) => ttUpload(e.target.files[0]));
const ttDrop = $("#tt-drop");
["dragenter", "dragover"].forEach((ev) => ttDrop.addEventListener(ev, (e) => { e.preventDefault(); ttDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => ttDrop.addEventListener(ev, (e) => { e.preventDefault(); ttDrop.classList.remove("over"); }));
ttDrop.addEventListener("drop", (e) => ttUpload(e.dataTransfer.files[0]));

/* ---------- 음성 ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recOn = false, ttsOn = !!store("tts");
$("#tts").classList.toggle("on", ttsOn);
$("#tts").addEventListener("click", () => {
  ttsOn = !ttsOn; store("tts", ttsOn); $("#tts").classList.toggle("on", ttsOn);
  if (!ttsOn) try { speechSynthesis.cancel(); } catch (_) {}
  toast(ttsOn ? "답변을 소리로 읽어드릴게요" : "읽어주기를 껐어요");
});
function speak(text) {
  if (!ttsOn || !("speechSynthesis" in window)) return;
  const plain = text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/https?:\/\/\S+/g, "").replace(/[*#`>|_]/g, "").replace(/\n+/g, ". ").slice(0, 500);
  try {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(plain); u.lang = "ko-KR"; u.rate = 1.05;
    speechSynthesis.speak(u);
  } catch (_) {}
}
if (!SR) { $("#mic").disabled = true; $("#mic").title = "이 브라우저는 음성 입력을 지원하지 않아요 (크롬·엣지 권장)"; }
$("#mic").addEventListener("click", () => {
  if (!SR) return;
  if (recOn) { rec.stop(); return; }
  rec = new SR(); rec.lang = "ko-KR"; rec.interimResults = true; rec.continuous = false;
  let finalText = "";
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
    }
    $("#input").value = finalText + interim; autosize();
  };
  rec.onerror = (e) => { if (e.error === "not-allowed") toast("마이크 권한을 허용해주세요 (주소창 왼쪽 자물쇠)"); else if (e.error !== "no-speech" && e.error !== "aborted") toast("음성 인식 오류: " + e.error); };
  rec.onend = () => {
    recOn = false; $("#mic").classList.remove("rec");
    if (finalText.trim()) { $("#input").value = ""; autosize(); send(finalText); }
  };
  try { speechSynthesis.cancel(); } catch (_) {}
  rec.start(); recOn = true; $("#mic").classList.add("rec");
});

/* ---------- 시작 ---------- */
(async function init() {
  try { await refreshStatus(); } catch (e) { toast("서버에 연결할 수 없어요. 실행 창이 켜져 있는지 확인해주세요."); return; }
  fillProfile("p");
  if (!state.setup_done) openWizard(0);
  else if (!state.gemini.set) openWizard(2);
  $("#input").focus();
})();
