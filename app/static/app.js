"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
let sessionId = store("session_id") || Math.random().toString(36).slice(2);
store("session_id", sessionId);
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
  $("#forms-count").textContent = state.forms.drafts;
  const tts = state.timetable;
  $("#tt-count").textContent = tts.classes ? tts.classes : "미등록";
  $("#tt-today").textContent = !tts.classes ? "" : tts.today.length ? "오늘 수업: " + tts.today.map((c) => `${c.start} ${c.title}`).join(" · ") : "오늘은 수업이 없어요";
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
  const typing = addMsg("bot", '<div class="typing"><span></span><span></span><span></span></div><ul class="progress"></ul>');
  const progressUl = typing.querySelector(".progress");
  const bubble = typing.querySelector(".bubble");
  const markTool = (name, ok) => {
    const li = [...progressUl.querySelectorAll("li")].reverse().find((x) => x.dataset.name === name && !x.classList.contains("done"));
    if (li) { li.classList.add("done"); if (!ok) li.classList.add("fail"); }
  };
  let acc = "", doneEv = null;
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    let data = {};
    if (!res.ok) {
      try { data = await res.json(); } catch (_) {}
      throw new Error(data.detail || `요청 실패 (${res.status})`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const block of parts) {
        const line = block.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev; try { ev = JSON.parse(line.slice(6)); } catch (_) { continue; }
        if (ev.type === "tool_start") {
          const li = document.createElement("li");
          li.dataset.name = ev.name; li.textContent = ev.text || ev.label;
          progressUl.appendChild(li);
        } else if (ev.type === "tool_end") {
          markTool(ev.name, ev.ok !== false);
        } else if (ev.type === "status") {
          const li = document.createElement("li"); li.className = "st"; li.textContent = ev.text;
          progressUl.appendChild(li);
        } else if (ev.type === "delta" && ev.text) {
          acc += ev.text;
          bubble.querySelector(".typing")?.remove();
          let live = bubble.querySelector(".live");
          if (!live) { live = document.createElement("div"); live.className = "live"; bubble.insertBefore(live, progressUl); }
          live.innerHTML = md(acc);
        } else if (ev.type === "done") {
          doneEv = ev; if (ev.reply) acc = ev.reply;
        } else if (ev.type === "error") {
          throw new Error(ev.message || "오류가 발생했어요.");
        }
        $("#messages").scrollTop = $("#messages").scrollHeight;
      }
    }
    typing.remove();
    const tools = doneEv?.tools || [];
    const botEl = addMsg("bot", md(acc || "응답을 만들지 못했어요."), tools);
    addFeedback(botEl, tools);
    speak(acc);
    const used = (n) => tools.some((t) => t.name === n && t.ok);
    if (used("find_events_in_emails") || used("find_events_in_notices")) openSched(false);
    if (used("draft_application")) openForms(true);
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
  await api("/api/chat/reset", { method: "POST", body: { session_id: sessionId } }).catch(() => {});
  sessionId = Math.random().toString(36).slice(2);
  store("session_id", sessionId);
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
const KIND = { briefing: "브리핑", weekly: "주간 회고", reminder: "마감", schedule: "일정 후보", notice: "관심 공지", system: "안내" };
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
  renderKeywords(state.notice_keywords || []);
}
function renderKeywords(list) {
  const ul = $("#kw-list"); if (!ul) return;
  ul.innerHTML = "";
  if (!list.length) ul.innerHTML = '<li class="muted">아직 없어요. 예: 장학, AI, 공모전</li>';
  list.forEach((k) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(k)}</span><button class="x" title="지우기">×</button>`;
    li.querySelector(".x").addEventListener("click", () => saveKeywords(list.filter((x) => x !== k)));
    ul.appendChild(li);
  });
}
async function saveKeywords(list) {
  await api("/api/settings", { method: "POST", body: { notice_keywords: list } });
  state.notice_keywords = list; renderKeywords(list); toast("키워드를 저장했어요");
}
$("#kw-add")?.addEventListener("click", () => {
  const v = ($("#kw-input").value || "").trim(); if (!v) return;
  const list = [...(state.notice_keywords || [])];
  v.split(/[,，]/).map((s) => s.trim()).filter(Boolean).forEach((k) => { if (!list.includes(k)) list.push(k); });
  $("#kw-input").value = ""; saveKeywords(list.slice(0, 20));
});
$("#kw-input")?.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) $("#kw-add").click(); });
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
  fillTeam(d);
}
async function fillTeam(d) {
  const box = $("#dc-team"); if (!box) return;
  box.classList.toggle("hidden", !d.linked);
  if (!d.linked) return;
  $("#dc-team-now").textContent = d.team_channel_name ? `현재: ${d.team_channel_name}` : "아직 팀 채널이 없어요. 마감 알림·일정 후보를 공유할 채널을 고르세요 (봇은 그 채널을 읽지 않고 보내기만 합니다).";
  const sel = $("#dc-team-sel");
  try {
    const r = await api("/api/discord/channels");
    sel.innerHTML = '<option value="">(공유 안 함)</option>' + (r.channels || []).map((c) =>
      `<option value="${esc(c.id)}" ${c.id === d.team_channel_id ? "selected" : ""}>${esc(c.guild)} ${esc(c.name)}</option>`).join("");
  } catch (_) { sel.innerHTML = '<option value="">채널 목록을 불러오지 못했어요</option>'; }
}
$("#dc-team-save")?.addEventListener("click", async () => {
  try {
    await api("/api/discord/team", { method: "POST", body: { channel_id: $("#dc-team-sel").value } });
    await refreshStatus(); fillDiscord(); toast("팀 채널을 저장했어요");
  } catch (e) { toast(e.message); }
});
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

/* ---------- 시간표 (주간 격자) ---------- */
const DAYNAMES = "월화수목금토일";
const TT = { rows: [], preview: null, slot: 24, editing: null, startH: 9, endH: 18 };
const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
const toHM = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
function ttHues(rows) {  // 과목마다 색 하나: 이름으로 자리를 정하고, 겹치면 다음 빈 색으로
  const map = {}, used = new Set();
  [...new Set(rows.map((c) => c.title))].sort().forEach((title) => {
    let h = 0; for (const ch of title) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    let slot = h % 8;
    for (let i = 0; i < 8 && used.has(slot); i++) slot = (slot + 1) % 8;
    used.add(slot); map[title] = slot + 1;
  });
  return map;
}
function renderTT() {
  const rows = TT.preview || TT.rows;
  const startH = Math.min(9, ...rows.map((c) => Math.floor(toMin(c.start) / 60)));
  const endH = Math.max(18, ...rows.map((c) => Math.ceil(toMin(c.end) / 60)));
  const days = rows.some((c) => c.day === 6) ? 7 : rows.some((c) => c.day === 5) ? 6 : 5;
  const H = (endH - startH) * 2 * TT.slot, today = (new Date().getDay() + 6) % 7;
  Object.assign(TT, { startH, endH });
  const hue = ttHues(rows);
  let head = "<span></span>", cols = "", times = "";
  for (let h = startH; h <= endH; h++) times += `<span style="top:${(h - startH) * 2 * TT.slot}px">${String(h).padStart(2, "0")}:00</span>`;
  for (let d = 0; d < days; d++) {
    head += `<span class="${d === today ? "today" : ""}">${DAYNAMES[d]}${d === today ? " · 오늘" : ""}</span>`;
    const blocks = rows.filter((c) => c.day === d).map((c) => {
      const top = (toMin(c.start) - startH * 60) / 30 * TT.slot;
      const h = Math.max(22, (toMin(c.end) - toMin(c.start)) / 30 * TT.slot - 2);
      return `<button type="button" class="ttb h${hue[c.title]}${TT.preview ? " pv" : ""}" data-id="${esc(c.id || "")}" style="top:${top}px;height:${h}px" title="${esc(c.title)} ${c.start}~${c.end}">
        <b>${esc(c.title)}</b><small>${c.start}~${c.end}${c.place ? " · " + esc(c.place) : ""}</small></button>`;
    }).join("");
    cols += `<div class="ttg-col${d === today ? " today" : ""}" data-day="${d}" style="height:${H}px">${blocks}</div>`;
  }
  $("#tt-grid").innerHTML = `<div class="ttg-head" style="--cols:${days}">${head}</div>
    <div class="ttg-body" style="--cols:${days};--slot:${TT.slot}px"><div class="ttg-times" style="height:${H}px">${times}</div>${cols}</div>`;
  $("#tt-empty").classList.toggle("hidden", rows.length > 0);
  $("#tt-clear").classList.toggle("hidden", !TT.rows.length || !!TT.preview);
  $("#tt-preview").classList.toggle("hidden", !TT.preview);
  $$("#tt-grid .ttg-col").forEach((col) => col.addEventListener("click", (e) => {
    if (TT.preview) return;
    const blk = e.target.closest(".ttb");
    if (blk) { const c = TT.rows.find((x) => x.id === blk.dataset.id); if (c) openClassEdit(c, false); return; }
    const y = e.clientY - col.getBoundingClientRect().top;
    const start = TT.startH * 60 + Math.max(0, Math.floor(y / TT.slot)) * 30;
    openClassEdit({ day: +col.dataset.day, start: toHM(start), end: toHM(Math.min(start + 110, 23 * 60 + 50)), title: "", place: "" }, true);
  }));
}
async function ttSave(rows, msg) {
  const r = await api("/api/timetable", { method: "PUT", body: { classes: rows } });
  TT.rows = r.classes; TT.preview = null; renderTT();
  if (msg) setResult($("#tt-result"), "ok", msg);
  refreshStatus().catch(() => {});
  return r.classes;
}
async function openTT() {
  $("#tt").classList.remove("hidden");
  setResult($("#tt-result"), "", "");
  TT.preview = null;
  TT.rows = (await api("/api/timetable")).classes;
  renderTT();
}
$("#btn-tt").addEventListener("click", () => openTT().catch((e) => toast(e.message)));
$("#tt-close").addEventListener("click", () => $("#tt").classList.add("hidden"));
$("#wiz-tt").addEventListener("click", async () => {
  await api("/api/settings", { method: "POST", body: { setup_done: true } }).catch(() => {});
  closeWizard(); openTT();
});

/* 수업 추가·수정 창 */
function openClassEdit(c, isNew) {
  TT.editing = isNew ? null : c.id;
  $("#tt-edit-title").textContent = isNew ? "수업 추가" : "수업 고치기";
  $("#te-title").value = c.title || ""; $("#te-day").value = String(c.day);
  $("#te-start").value = c.start; $("#te-end").value = c.end; $("#te-place").value = c.place || "";
  $("#te-del").classList.toggle("hidden", isNew);
  setResult($("#te-result"), "", "");
  $("#tt-edit").classList.remove("hidden");
  setTimeout(() => $("#te-title").focus(), 30);
}
const closeClassEdit = () => $("#tt-edit").classList.add("hidden");
$("#tt-edit-close").addEventListener("click", closeClassEdit);
$("#tt-add").addEventListener("click", () => openClassEdit({ day: Math.min((new Date().getDay() + 6) % 7, 4), start: "09:00", end: "10:50", title: "", place: "" }, true));
$("#tt-add2").addEventListener("click", () => $("#tt-add").click());
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#tt-edit").classList.contains("hidden")) closeClassEdit(); });
$("#tt-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const c = { day: +$("#te-day").value, start: $("#te-start").value, end: $("#te-end").value, title: $("#te-title").value.trim(), place: $("#te-place").value.trim() };
  const out = $("#te-result");
  if (!c.title) return setResult(out, "bad", "과목명을 입력해주세요.");
  if (!c.start || !c.end || toMin(c.end) <= toMin(c.start)) return setResult(out, "bad", "끝 시간이 시작 시간보다 늦어야 해요.");
  const others = TT.rows.filter((x) => x.id !== TT.editing);
  const clash = others.find((x) => x.day === c.day && toMin(x.start) < toMin(c.end) && toMin(c.start) < toMin(x.end));
  if (TT.editing) c.id = TT.editing;
  try {
    await ttSave([...others, c], TT.editing ? "수업을 고쳤어요." : `「${c.title}」 수업을 추가했어요.`);
    closeClassEdit();
    if (clash) toast(`같은 시간에 「${clash.title}」 수업이 있어요. 확인해보세요.`);
  } catch (err) { setResult(out, "bad", err.message); }
});
$("#te-del").addEventListener("click", async () => {
  const c = TT.rows.find((x) => x.id === TT.editing);
  await ttSave(TT.rows.filter((x) => x.id !== TT.editing), c ? `「${c.title}」 수업을 지웠어요.` : "");
  closeClassEdit();
});
let ttClearArmed = null;
$("#tt-clear").addEventListener("click", async (e) => {
  const b = e.target;
  if (!ttClearArmed) {  // 실수 방지: 두 번 눌러야 지워짐
    b.textContent = "한 번 더 누르면 모두 지워요";
    ttClearArmed = setTimeout(() => { ttClearArmed = null; b.textContent = "전체 지우기"; }, 4000);
    return;
  }
  clearTimeout(ttClearArmed); ttClearArmed = null; b.textContent = "전체 지우기";
  await ttSave([], "시간표를 모두 지웠어요.");
});

/* 사진으로 등록 */
async function ttUpload(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) return setResult($("#tt-result"), "bad", "이미지 파일(캡처 화면)을 올려주세요.");
  const fd = new FormData(); fd.append("file", file);
  setResult($("#tt-result"), "wait", "AI가 시간표를 읽는 중… (10~20초)");
  $("#tt-photo-btn").classList.add("busy");
  try {
    const r = await api("/api/timetable/extract", { method: "POST", body: fd });
    if (!r.classes.length) return setResult($("#tt-result"), "bad", "수업을 찾지 못했어요. 요일과 시간이 잘 보이는 캡처로 다시 올려주세요.");
    if (!TT.rows.length) { await ttSave(r.classes, `수업 ${r.classes.length}개를 등록했어요. 틀린 곳은 눌러서 고치세요.`); return; }
    TT.preview = r.classes;
    $("#tt-preview-text").innerHTML = `<b>AI가 수업 ${r.classes.length}개를 읽었어요.</b> 아래는 미리보기예요. 지금 시간표(${TT.rows.length}개)를 어떻게 할까요?`;
    setResult($("#tt-result"), "", ""); renderTT();
  } catch (e) { setResult($("#tt-result"), "bad", e.message); }
  finally { $("#tt-photo-btn").classList.remove("busy"); $("#tt-file").value = ""; }
}
$("#tt-file").addEventListener("change", (e) => ttUpload(e.target.files[0]));
$("#tt-pv-replace").addEventListener("click", () => ttSave(TT.preview, `수업 ${TT.preview.length}개로 바꿨어요. 틀린 곳은 눌러서 고치세요.`));
$("#tt-pv-merge").addEventListener("click", () => {
  const key = (c) => `${c.day}|${c.start}|${c.title}`, have = new Set(TT.rows.map(key));
  const add = TT.preview.filter((c) => !have.has(key(c)));
  ttSave([...TT.rows, ...add], `수업 ${add.length}개를 추가했어요.`);
});
$("#tt-pv-cancel").addEventListener("click", () => { TT.preview = null; renderTT(); });
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

/* ---------- 답변 평가 ---------- */
const ICON = (n) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-${n}"/></svg>`;
function addFeedback(wrap, tools) {
  const names = [...new Set((tools || []).map((t) => t.name))];
  const box = document.createElement("div"); box.className = "fb";
  box.innerHTML = `<button title="도움이 됐어요" data-r="up">${ICON("up")}</button><button title="아쉬워요" data-r="down">${ICON("down")}</button>`;
  const sendFb = async (rating, reason = "") => {
    try { await api("/api/feedback", { method: "POST", body: { rating, tools: names, reason } }); } catch (_) {}
  };
  box.querySelector('[data-r="up"]').addEventListener("click", async () => {
    await sendFb("up"); box.innerHTML = "<small>고마워요! 평가가 리포트에 반영돼요.</small>";
  });
  box.querySelector('[data-r="down"]').addEventListener("click", () => {
    box.innerHTML = `<input placeholder="뭐가 아쉬웠나요? (선택)" maxlength="200"><button class="sm" type="button">보내기</button>`;
    const inp = box.querySelector("input"); inp.focus();
    const go = async () => { await sendFb("down", inp.value); box.innerHTML = "<small>의견 고마워요. 더 나아지게 할게요.</small>"; };
    box.querySelector("button").addEventListener("click", go);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) go(); });
  });
  wrap.querySelector(".bubble").appendChild(box);
}

/* ---------- 신청서 도우미 ---------- */
let fmSrc = "file", fmFile = null, fmCurrent = null;
async function loadFormList(selectId) {
  const r = await api("/api/forms");
  const ul = $("#fm-list"); ul.innerHTML = "";
  if (!r.drafts.length) ul.innerHTML = '<li class="muted">아직 초안이 없어요</li>';
  r.drafts.forEach((d) => {
    const li = document.createElement("li");
    const t = new Date(d.created * 1000);
    li.innerHTML = `${esc(d.title)}<small>${t.getMonth() + 1}/${t.getDate()} · 항목 ${d.fields}개${d.missing ? ` · 빈칸 ${d.missing}` : ""}</small>`;
    li.dataset.id = d.id;
    li.addEventListener("click", () => showDraft(d.id));
    ul.appendChild(li);
  });
  return r.drafts;
}
function fmShowCreate() {
  fmCurrent = null;
  $("#fm-create").classList.remove("hidden"); $("#fm-view").classList.add("hidden");
  $$("#fm-list li").forEach((li) => li.classList.remove("on"));
}
async function openForms(latest = false) {
  $("#forms").classList.remove("hidden");
  const drafts = await loadFormList();
  if (latest && drafts.length) showDraft(drafts[0].id); else if (!fmCurrent) fmShowCreate();
}
$("#btn-forms").addEventListener("click", () => openForms(false));
$("#forms-close").addEventListener("click", () => $("#forms").classList.add("hidden"));
$("#fm-new").addEventListener("click", fmShowCreate);
$$("#fm-src button").forEach((b) => b.addEventListener("click", () => {
  fmSrc = b.dataset.src;
  $$("#fm-src button").forEach((x) => x.classList.toggle("on", x === b));
  $$("#fm-create [data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== fmSrc));
}));
function fmSetFile(f) {
  if (!f) return; fmFile = f;
  $("#fm-drop").classList.add("done"); $("#fm-drop-text").innerHTML = `<b>${esc(f.name)}</b> · 바꾸려면 다시 올리세요`;
}
$("#fm-file").addEventListener("change", (e) => fmSetFile(e.target.files[0]));
const fmDrop = $("#fm-drop");
["dragenter", "dragover"].forEach((ev) => fmDrop.addEventListener(ev, (e) => { e.preventDefault(); fmDrop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => fmDrop.addEventListener(ev, (e) => { e.preventDefault(); fmDrop.classList.remove("over"); }));
fmDrop.addEventListener("drop", (e) => fmSetFile(e.dataTransfer.files[0]));
$("#fm-notice-load").addEventListener("click", async () => {
  const url = $("#fm-notice-url").value.trim(); const box = $("#fm-att-list");
  if (!url) return; box.innerHTML = '<p class="muted">불러오는 중…</p>';
  try {
    const r = await api(`/api/forms/notice_attachments?url=${encodeURIComponent(url)}`);
    if (!r.attachments.length) { box.innerHTML = '<p class="muted">이 공지에는 첨부파일이 없어요.</p>'; return; }
    box.innerHTML = `<p class="muted">${esc(r.title)} — 양식 파일을 골라주세요</p>` + r.attachments.map((a, i) =>
      `<label><input type="radio" name="fm-att" value="${esc(a.url)}" ${i === 0 ? "checked" : ""}>${esc(a.name)}</label>`).join("");
  } catch (e) { box.innerHTML = `<p class="result bad">${esc(e.message)}</p>`; }
});
$("#fm-make").addEventListener("click", async () => {
  const out = $("#fm-result"), btn = $("#fm-make");
  const notes = $("#fm-notes").value, use_profile = $("#fm-use-profile").checked, style = $("#fm-style").value;
  let req;
  if (fmSrc === "file") {
    if (!fmFile) return setResult(out, "bad", "양식 파일을 올려주세요.");
    const fd = new FormData(); fd.append("file", fmFile); fd.append("notes", notes); fd.append("use_profile", use_profile); fd.append("style", style);
    req = api("/api/forms/upload", { method: "POST", body: fd });
  } else if (fmSrc === "notice") {
    const sel = document.querySelector('input[name="fm-att"]:checked');
    if (!sel) return setResult(out, "bad", "공지 주소를 넣고 「첨부 불러오기」로 양식을 골라주세요.");
    req = api("/api/forms/from_notice", { method: "POST", body: { attachment_url: sel.value, notice_url: $("#fm-notice-url").value.trim(), notes, use_profile, style } });
  } else {
    const text = $("#fm-text").value.trim();
    if (!text) return setResult(out, "bad", "양식 내용을 붙여넣어 주세요.");
    req = api("/api/forms/from_text", { method: "POST", body: { text, notes, use_profile, style } });
  }
  btn.disabled = true; setResult(out, "wait", "양식을 읽고 항목별 초안을 쓰는 중… (20~60초)");
  try { const d = await req; setResult(out, "", ""); await loadFormList(); renderDraft(d); refreshStatus(); }
  catch (e) { setResult(out, "bad", e.message); }
  finally { btn.disabled = false; }
});
async function showDraft(id) {
  try { renderDraft(await api(`/api/forms/${id}`)); } catch (e) { toast(e.message); }
}
function draftText(d) {
  let out = `${d.title}\n\n`, sec = null;
  d.fields.forEach((f) => {
    if (f.section && f.section !== sec) { sec = f.section; out += `■ ${sec}\n\n`; }
    out += `[${f.label}]\n${f.value || ""}\n\n`;
  });
  return out.trim() + "\n";
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast("복사했어요"); }
  catch (_) { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("복사했어요"); }
}
function updateMissing(d) {
  const all = [...new Set(d.fields.flatMap((f) => f.missing))];
  const box = $("#fm-missing");
  box.classList.toggle("hidden", !all.length);
  box.innerHTML = all.length ? `<b>직접 채워야 할 것</b> · ${all.map(esc).join(", ")} <span class="muted">(초안에 [○○ 입력]으로 표시돼 있어요)</span>` : "";
}
function renderDraft(d) {
  fmCurrent = d;
  $("#fm-create").classList.add("hidden"); $("#fm-view").classList.remove("hidden");
  $$("#fm-list li").forEach((li) => li.classList.toggle("on", li.dataset.id === d.id));
  $("#fm-title").textContent = d.title;
  const t = new Date(d.created * 1000);
  $("#fm-meta").textContent = `${d.source?.name || ""} · ${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")} · 항목 ${d.fields.length}개 · 고친 내용은 자동 저장돼요`;
  $("#fm-docx").href = `/api/forms/${d.id}/docx`;
  updateMissing(d);
  const box = $("#fm-fields"); box.innerHTML = "";
  d.fields.forEach((f) => {
    const el = document.createElement("div"); el.className = "field";
    el.innerHTML = `<div class="field-top">${f.section ? `<span class="sec">${esc(f.section)}</span>` : ""}<b>${esc(f.label)}</b></div>
      ${f.guidance ? `<div class="guide">${esc(f.guidance)}</div>` : ""}
      <textarea class="${f.kind === "short" ? "short" : ""}" rows="${f.kind === "short" ? 1 : 5}">${esc(f.value)}</textarea>
      ${f.note ? `<div class="field-note">메모: ${esc(f.note)}</div>` : ""}
      <div class="field-foot"><span class="cnt"></span><button class="ghost sm" data-a="copy" type="button">복사</button>
        <div class="rw"><input placeholder="다시 쓰기 요청 (예: 더 짧게, 기대효과 강조)"><button class="ghost sm" data-a="rw" type="button">다시 쓰기</button></div></div>`;
    const ta = el.querySelector("textarea"), cnt = el.querySelector(".cnt");
    const count = () => { const n = ta.value.length; cnt.textContent = f.max_chars ? `${n} / ${f.max_chars}자` : `${n}자`; cnt.classList.toggle("over", !!f.max_chars && n > f.max_chars); };
    const fit = () => { if (f.kind !== "short") { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 520) + "px"; } };
    count(); setTimeout(fit, 0);
    ta.addEventListener("input", () => { count(); fit(); });
    ta.addEventListener("change", async () => {
      try { const nf = await api(`/api/forms/${d.id}/fields/${f.id}`, { method: "PUT", body: { value: ta.value } }); Object.assign(f, nf); updateMissing(d); }
      catch (e) { toast(e.message); }
    });
    el.querySelector('[data-a="copy"]').addEventListener("click", () => copyText(ta.value));
    el.querySelector('[data-a="rw"]').addEventListener("click", async (e) => {
      const b = e.target, inp = el.querySelector(".rw input");
      b.disabled = true; b.textContent = "쓰는 중…";
      try {
        const nf = await api(`/api/forms/${d.id}/fields/${f.id}/rewrite`, { method: "POST", body: { instruction: inp.value } });
        Object.assign(f, nf); ta.value = nf.value; count(); fit(); inp.value = ""; updateMissing(d); toast("다시 썼어요");
      } catch (err) { toast(err.message); }
      finally { b.disabled = false; b.textContent = "다시 쓰기"; }
    });
    box.appendChild(el);
  });
}
$("#fm-copy-all").addEventListener("click", () => fmCurrent && copyText(draftText(fmCurrent)));
$("#fm-del").addEventListener("click", async () => {
  if (!fmCurrent) return;
  await api(`/api/forms/${fmCurrent.id}`, { method: "DELETE" });
  toast("초안을 지웠어요"); await loadFormList(); fmShowCreate(); refreshStatus();
});

/* ---------- 사용 리포트 ---------- */
function kpiTile(v, l, s = "") { return `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`; }
function fmtMinutes(m) { if (m < 60) return `${m}분`; const h = Math.floor(m / 60), r = m % 60; return r ? `${h}시간 ${r}분` : `${h}시간`; }
function renderChart(rows) {
  const W = 640, H = 180, P = { l: 28, r: 8, t: 10, b: 24 };
  const vals = rows.map((r) => r.chats + r.auto);
  const max = Math.max(4, ...vals), step = Math.ceil(max / 4);
  const top = step * 4, iw = W - P.l - P.r, ih = H - P.t - P.b, bw = iw / rows.length, barW = Math.min(26, bw - 6);
  const y = (v) => P.t + ih - (v / top) * ih;
  let g = "";
  for (let i = 0; i <= 4; i++) { const v = step * i, yy = y(v); g += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}"/><text class="axis" x="${P.l - 6}" y="${yy + 4}" text-anchor="end">${v}</text>`; }
  rows.forEach((r, i) => {
    const v = vals[i], x = P.l + i * bw + (bw - barW) / 2, yy = y(v), h = P.t + ih - yy;
    const [, m, d] = r.day.split("-").map(Number);
    let bar = "";
    if (v > 0) { const rr = Math.min(4, h, barW / 2); bar = `<path class="bar" d="M${x},${P.t + ih} V${yy + rr} Q${x},${yy} ${x + rr},${yy} H${x + barW - rr} Q${x + barW},${yy} ${x + barW},${yy + rr} V${P.t + ih} Z"/>`; }
    g += `<g data-i="${i}"><rect class="hit" x="${P.l + i * bw}" y="${P.t}" width="${bw}" height="${ih}"/>${bar}</g>`;
    if ((rows.length - 1 - i) % 3 === 0) g += `<text class="axis" x="${x + barW / 2}" y="${H - 6}" text-anchor="middle">${m}/${d}</text>`;
  });
  $("#st-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="최근 14일 하루 활동 수">${g}</svg>`;
  const tip = $("#chart-tip");
  $$("#st-chart g[data-i]").forEach((el) => {
    el.addEventListener("mousemove", (e) => {
      const r = rows[+el.dataset.i], [, m, d] = r.day.split("-").map(Number);
      tip.innerHTML = `<b>${m}월 ${d}일 · ${r.chats + r.auto}건</b>대화 ${r.chats} · 비서가 먼저 한 일 ${r.auto}`;
      tip.style.left = Math.min(e.clientX + 14, innerWidth - 220) + "px"; tip.style.top = e.clientY + 14 + "px"; tip.classList.remove("hidden");
    });
    el.addEventListener("mouseleave", () => tip.classList.add("hidden"));
  });
}
async function loadStats() {
  const r = await api(`/api/stats?days=${$("#st-period").value}`), k = r.kpi;
  const sat = k.satisfaction === null ? "–" : `${k.satisfaction}%`;
  $("#st-kpi").innerHTML =
    kpiTile(k.chats, "비서와 대화", Object.entries(k.chats_by_channel).map(([c, n]) => `${({ web: "화면", discord: "디스코드", telegram: "텔레그램", task: "자동" })[c] || c} ${n}`).join(" · ")) +
    kpiTile(k.reminders + k.briefings, "먼저 챙겨준 알림", `마감 알림 ${k.reminders} · 브리핑·회고 ${k.briefings}`) +
    kpiTile(k.candidates_found, "찾아준 일정 후보", `캘린더 추가 ${k.candidates_added}`) +
    kpiTile(k.email_drafts + k.form_drafts, "써준 초안", `메일 ${k.email_drafts} · 신청서 ${k.form_drafts}`) +
    kpiTile(sat, "답변 만족도", k.feedback_total ? `평가 ${k.feedback_total}회 중 좋아요 ${k.feedback_up}` : "답변 아래 버튼으로 평가해요") +
    kpiTile(fmtMinutes(k.minutes_saved), "아낀 시간 (추정)", "계산 기준은 아래에");
  renderChart(r.chart);
  $("#st-tools").innerHTML = r.top_tools.length ? r.top_tools.map((t) => `<li>${esc(t.name)} <span>${t.count}회</span></li>`).join("") : '<li class="muted">아직 기록이 없어요</li>';
  $("#st-assume").innerHTML = r.assumptions.map((a) => `<li>${esc(a.label)}: ${a.minutes}분</li>`).join("");
  $("#st-feedback").innerHTML = r.recent_feedback.length ? `<h4>최근 남긴 의견</h4><ul class="plain-list">${r.recent_feedback.map((f) => `<li><span>${f.rating === "up" ? "좋아요" : "아쉬워요"} · ${esc(f.reason)}</span><small class="muted">${f.day.slice(5)}</small></li>`).join("")}</ul>` : "";
}
$("#btn-stats").addEventListener("click", () => { $("#stats").classList.remove("hidden"); loadStats().catch((e) => toast(e.message)); });
$("#stats-close").addEventListener("click", () => { $("#stats").classList.add("hidden"); $("#chart-tip").classList.add("hidden"); });
$("#st-period").addEventListener("change", () => loadStats());

/* ---------- 시작 ---------- */
(async function init() {
  try { await refreshStatus(); } catch (e) { toast("서버에 연결할 수 없어요. 실행 창이 켜져 있는지 확인해주세요."); return; }
  fillProfile("p");
  if (!state.setup_done) openWizard(0);
  else if (!state.gemini.set) openWizard(2);
  try {
    const r = await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}`);
    (r.messages || []).forEach((m) => {
      if (m.role === "user") addMsg("user", m.text);
      else addMsg("bot", md(m.text), m.tools);
    });
  } catch (_) {}
  $("#input").focus();
})();
