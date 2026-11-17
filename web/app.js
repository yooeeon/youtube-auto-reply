// 운명원 댓글 에이전트 — 대시보드 (빌드 없는 vanilla JS)
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  tab: "latest",
  videos: { latest: [], rising: [] },
  loaded: { latest: false, rising: false },
  selected: null,        // 선택한 영상 객체
  draft: null,           // 서버 초안 결과
  draftId: null,
  text: "",
  validation: null,
  drafting: false,
  draftSource: null,     // EventSource
  status: null,
  posting: null,         // {attempt_id, profile, events: []}
  cooldownUntil: 0,
};

const DRAFT_STEPS = ["영상 정보 불러오는 중", "자막 읽는 중", "인기 댓글 읽는 중", "연애 고민 정리 중", "댓글 후보 쓰는 중", "후보 심사 중"];
const POST_STEPS = [
  ["opening_profile", "프로필 여는 중"],
  ["opening_video", "영상 여는 중"],
  ["opening_comments", "댓글 창 여는 중"],
  ["focusing_input", "댓글 입력창 여는 중"],
  ["typing", "한 글자씩 입력 중"],
  ["submitting", "게시 중"],
  ["verifying", "내 댓글 확인 중"],
  ["posted", "게시 완료 · 댓글 보기"],
];
const TRANSCRIPT_LABEL = { manual_ko: "한국어 자막", auto_ko: "자동 자막", yt_dlp: "자막(yt-dlp)", none: "자막 없음 → 제목·설명으로 분석" };
const STATUS_LABEL = { posted: ["게시 완료", "ok"], failed: ["실패", "bad"], unverified: ["확인 실패", "warn"], posting: ["게시 중", "accent"] };

// ── 공통 ─────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = {};
  try { data = await res.json(); } catch {}
  if (!res.ok) throw Object.assign(new Error(data.error || `요청 실패 (${res.status})`), { code: data.code, status: res.status });
  return data;
}
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 4000);
}
function relTime(iso) {
  if (!iso) return "";
  const d = new Date(iso), m = (Date.now() - d) / 60000;
  if (m < 60) return `${Math.max(1, Math.round(m))}분 전`;
  if (m < 60 * 24) return `${Math.round(m / 60)}시간 전`;
  if (m < 60 * 24 * 30) return `${Math.round(m / 1440)}일 전`;
  return d.toLocaleDateString("ko-KR");
}
function absDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function shortDate(iso) { if (!iso) return ""; const d = new Date(iso); return `${d.getMonth() + 1}.${d.getDate()}`; }
const fmtNum = (n) => (n == null ? "-" : n >= 10000 ? `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)}만` : n.toLocaleString("ko-KR"));
const fmtDur = (s) => { s = s || 0; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; };
const tsToSec = (ts) => (ts || "").split(":").reverse().reduce((a, v, i) => a + (+v || 0) * 60 ** i, 0);
function setLoading(btn, on) { btn.classList.toggle("loading", on); btn.disabled = on; }
// '유튜브1로' / '유튜브3으로' — 마지막 글자 받침에 맞춘 조사
function ro(name) {
  const last = name.slice(-1);
  const digitBatchim = { "0": 1, "1": 2, "3": 1, "6": 1, "7": 2, "8": 2 }; // 1: 받침(으로), 2: ㄹ받침(로)
  let b = 0;
  if (last in digitBatchim) b = digitBatchim[last];
  else if (/[가-힣]/.test(last)) { const j = (last.charCodeAt(0) - 0xac00) % 28; b = j === 0 ? 0 : j === 8 ? 2 : 1; }
  return name + (b === 1 ? "으로" : "로");
}
const PROFILE_LABEL = { ok: "로그인 ✓", unchecked: "점검 전", missing: "로그인 필요", logged_out: "로그인 필요", no_channel: "채널 없음", in_use: "창 열려 있음", unknown: "확인 필요" };
const BAD_PROFILE = ["missing", "logged_out", "no_channel"];

// ── 상태 ─────────────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    S.status = await api(`/api/status${S.selected ? `?video_id=${S.selected.video_id}` : ""}`);
  } catch { return; }
  const st = S.status;
  if (st.cooldown_sec) S.cooldownUntil = Date.now() + st.cooldown_sec * 1000;
  $("#quota-used").textContent = st.today_used;
  $("#quota-max").textContent = st.daily_max;
  $("#quota").classList.toggle("full", st.today_used >= st.daily_max);
  $("#rules-note").textContent = `규칙: 영상당 댓글 1개 · 하루 ${st.daily_max}개까지 · 게시 사이 최소 간격`;
  const env = [];
  env.push(st.llm_backend === "cli" ? `<span class="chip ok" title="이 컴퓨터에 로그인된 Claude Code로 초안을 만들어요">AI: Claude 구독</span>`
    : st.llm_backend === "api" ? `<span class="chip ok">AI: Claude API</span>` : `<span class="chip bad" title="${esc(st.llm_error)}">AI 연결 안 됨</span>`);
  env.push(st.youtube_api ? `<span class="chip ok">YouTube API</span>` : `<span class="chip warn" title="YOUTUBE_API_KEY가 없어 최신순이 정확하지 않아요">YouTube API 키 없음</span>`);
  for (const p of st.profiles) {
    const cls = p.status === "ok" ? "ok" : BAD_PROFILE.includes(p.status) ? "bad" : "warn";
    const label = p.status === "ok" ? "✓" : PROFILE_LABEL[p.status] || "확인 필요";
    env.push(`<button class="chip ${cls} chip-btn" data-open-profiles title="${esc(p.message)} — 눌러서 프로필 관리">${esc(p.name)} ${label}</button>`);
  }
  $("#env").innerHTML = env.join("");
  $("#env").querySelectorAll("[data-open-profiles]").forEach((b) => (b.onclick = openProfiles));
  renderSetup();
  renderProfiles();
  renderPostPanel();
}

function openProfiles() {
  const box = $("#profiles-box");
  box.open = true;
  box.scrollIntoView({ behavior: "smooth", block: "center" });
}

// 처음 쓰는 사람을 위한 준비 안내 (준비가 끝나면 사라짐)
function renderSetup() {
  const st = S.status, el = $("#setup");
  if (!st) return;
  const items = [];
  if (!st.llm_backend) items.push(`AI가 연결되지 않았어요. 이 컴퓨터에서 Claude Code에 로그인하거나 <code>.env</code>에 ANTHROPIC_API_KEY를 넣어 주세요.`);
  const ready = st.profiles.filter((p) => !BAD_PROFILE.includes(p.status));
  if (!ready.length) {
    const first = st.profiles[0]?.name || "유튜브1";
    items.push(`댓글을 달 크롬 프로필이 아직 없어요. <button class="btn small" data-setup-login="${esc(first)}">${esc(first)} 로그인</button> → 열린 크롬에서 구글 로그인 → ⌘Q로 닫기 → <button class="btn small" data-setup-check="${esc(first)}">점검</button>`);
  }
  if (!items.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<b>시작하기 전에</b><ol>${items.map((i) => `<li>${i}</li>`).join("")}</ol>`;
  el.querySelectorAll("[data-setup-login]").forEach((b) => (b.onclick = () => profileLogin(b.dataset.setupLogin)));
  el.querySelectorAll("[data-setup-check]").forEach((b) => (b.onclick = () => profileCheck(b.dataset.setupCheck, b)));
}

// ── ① 영상 목록 ───────────────────────────────────────────────────────
function renderTabHint() {
  $("#btn-load").textContent = S.tab === "latest" ? "영상 불러오기" : "🔥 뜨기 직전 영상 찾기";
  $("#tab-hint").textContent = S.tab === "latest"
    ? "최근 7일 연애 영상을 최신순으로 불러와요. (쇼츠·라이브·노래 영상 제외)"
    : "‘연애’ 영상 중 최근 48시간 · 조회수 1,000~5,000 · 시간당 150회 이상. 초안까지만 만들어요.";
}

function renderList() {
  const list = $("#video-list");
  const vids = S.videos[S.tab];
  if (!vids.length) {
    list.innerHTML = S.loaded[S.tab]
      ? `<div class="empty">조건에 맞는 영상이 없어요.<br/>잠시 후 다시 불러오거나 링크로 직접 추가해 보세요.</div>`
      : `<div class="empty"><b>[${S.tab === "latest" ? "영상 불러오기" : "뜨기 직전 영상 찾기"}]</b>를 누르면<br/>썸네일 · 제목 · 채널 · 올린 날짜로 영상이 떠요.</div>`;
    return;
  }
  const guide = !S.selected ? `<p class="list-guide">👆 영상을 누르면 ②에 댓글 초안이 만들어져요.</p>` : "";
  list.innerHTML = guide + vids.map((v) => `
    <div class="vcard ${S.selected?.video_id === v.video_id ? "selected" : ""}" data-id="${v.video_id}" tabindex="0">
      <div class="thumb"><img loading="lazy" src="${esc(v.thumbnail_url)}" alt="" /><span class="dur">${fmtDur(v.duration_sec)}</span></div>
      <div>
        <div class="vtitle">${esc(v.title)}</div>
        <div class="vmeta one">${esc(v.channel_title)}</div>
        <div class="vmeta one" title="올린 날짜 ${absDate(v.published_at)}">${shortDate(v.published_at)} · ${relTime(v.published_at)}</div>
        <div class="vmeta one">조회 ${fmtNum(v.view_count)} · 댓글 ${v.comment_count != null ? fmtNum(v.comment_count) : "-"}</div>
        <div class="vbadges">
          ${v.commented ? `<span class="chip ok">✓ 댓글 완료</span>` : ""}
          ${v.tab === "rising" ? `<span class="chip fire">🔥 시간당 ${fmtNum(Math.round(v.views_per_hour))}회</span>` : ""}
          ${v._linked ? `<span class="chip accent">링크로 추가</span>` : ""}
          ${v.warning ? `<span class="chip warn">${esc(v.warning)}</span>` : ""}
        </div>
      </div>
    </div>`).join("");
}

async function loadVideos() {
  const btn = $("#btn-load");
  setLoading(btn, true);
  try {
    const r = await api(S.tab === "latest" ? "/api/videos/load" : "/api/videos/rising", { method: "POST" });
    S.videos[S.tab] = r.videos;
    S.loaded[S.tab] = true;
    renderList();
    S.loadError = null;
    toast(`${r.videos.length}개 영상을 불러왔어요. 영상을 눌러 골라 주세요.`);
  } catch (e) {
    S.loadError = e.message;
    $("#video-list").innerHTML = `<div class="notice bad">${esc(e.message)}<br/><button class="btn small" id="btn-retry-load" style="margin-top:6px">다시 시도</button></div>`;
    $("#btn-retry-load").onclick = loadVideos;
  }
  finally { setLoading(btn, false); renderTabHint(); highlightStep(); }
}

async function addLink(ev) {
  ev.preventDefault();
  const input = $("#link-input"), url = input.value.trim();
  if (!url) return;
  const btn = $("#link-form button");
  setLoading(btn, true);
  try {
    const r = await api("/api/videos/link", { method: "POST", body: { url } });
    S.tab = "latest"; syncTabs();
    S.videos.latest = r.videos; S.loaded.latest = true;
    input.value = "";
    renderList();
    selectVideo(r.video);
  } catch (e) { toast(e.message, true); }
  finally { setLoading(btn, false); }
}

function syncTabs() {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === S.tab));
  renderTabHint();
}

// ── ② 초안 ───────────────────────────────────────────────────────────
function selectVideo(v) {
  if (S.posting && !S.posting.done) { toast("게시가 끝난 뒤에 다른 영상을 골라 주세요.", true); return; }
  S.selected = v;
  S.draft = null; S.draftId = null; S.text = ""; S.validation = null; S.posting = null;
  renderList();
  startDraft(false);
  refreshStatus();
}

function startDraft(regenerate) {
  if (!S.selected) return;
  S.draftSource?.close();
  S.drafting = true;
  S.draftProgress = [];
  S.draftStarted = Date.now();
  clearInterval(S.draftTimer);
  S.draftTimer = setInterval(() => { const e = $("#draft-elapsed"); if (e && S.drafting) e.textContent = `${Math.round((Date.now() - S.draftStarted) / 1000)}초 지남`; }, 1000);
  renderDraft(); renderPostPanel(); highlightStep();
  const es = new EventSource(`/api/videos/${S.selected.video_id}/draft${regenerate ? "?regenerate=1" : ""}`);
  S.draftSource = es;
  const vid = S.selected.video_id;
  es.onmessage = (m) => {
    if (S.selected?.video_id !== vid) { es.close(); return; }
    const ev = JSON.parse(m.data);
    if (ev.type === "progress") { S.draftProgress.push(ev.message); renderDraft(); }
    else if (ev.type === "result") {
      es.close(); S.drafting = false;
      S.draft = ev.draft;
      const sel = ev.draft.selected;
      S.text = sel ? sel.text : ""; S.draftId = sel ? sel.id : null; S.originalText = S.text;
      S.validation = sel ? { ok: sel.ok, checks: sel.checks } : null;
      renderDraft(); validateNow(); highlightStep();
    } else if (ev.type === "error") {
      es.close(); S.drafting = false; S.draftError = ev.message; renderDraft(); renderPostPanel();
    }
  };
  es.onerror = () => {
    if (!S.drafting) return;
    es.close(); S.drafting = false; S.draftError = "서버와 연결이 끊겼어요. 다시 시도해 주세요."; renderDraft();
  };
  S.draftError = null;
}

function renderDraft() {
  const el = $("#draft-panel");
  const v = S.selected;
  if (!v) {
    el.innerHTML = `<div class="empty">왼쪽에서 영상을 고르면<br/>AI가 <b>자막과 인기 댓글</b>을 읽고<br/>영상 속 연애 고민과 <b>댓글 초안</b>을 만들어요.<br/><span class="small">(약 1분 걸려요)</span></div>`;
    return;
  }
  const head = `
    <div class="vhead">
      <a class="thumb" href="https://www.youtube.com/watch?v=${v.video_id}" target="_blank" title="유튜브에서 열기"><img src="${esc(v.thumbnail_url)}" alt="" /><span class="dur">${fmtDur(v.duration_sec)}</span></a>
      <div><h3>${esc(v.title)}</h3><div class="vmeta">${esc(v.channel_title)} · ${relTime(v.published_at)} · 조회 ${fmtNum(v.view_count)}${v.comment_count != null ? ` · 댓글 ${fmtNum(v.comment_count)}` : ""}</div>
      ${S.draft ? `<div class="vbadges"><span class="chip">${TRANSCRIPT_LABEL[S.draft.transcript_source] || ""}</span></div>` : ""}</div>
    </div>`;

  if (S.drafting) {
    const done = S.draftProgress || [];
    // 지금 단계보다 앞 단계는 모두 완료로 (분석이 캐시돼 건너뛴 단계 포함)
    const lastMain = done.filter((d) => DRAFT_STEPS.some((s) => d.startsWith(s.slice(0, 5)))).slice(-1)[0];
    const curIdx = lastMain ? DRAFT_STEPS.findIndex((s) => lastMain.startsWith(s.slice(0, 5))) : -1;
    const items = DRAFT_STEPS.map((s, i) => {
      const cls = i < curIdx ? "done" : i === curIdx ? "now" : "";
      return `<li class="${cls}"><span class="dot">${cls === "done" ? "✓" : ""}</span>${s}</li>`;
    });
    const extra = done.filter((d) => d.startsWith("다시 쓰는 중")).slice(-1).map((d) => `<li class="now"><span class="dot"></span>${esc(d)} — 규칙·점수를 못 넘은 후보를 고치는 중</li>`);
    el.innerHTML = head + `<ol class="progress">${items.join("")}${extra.join("")}</ol>
      <p class="hint" style="margin-top:16px"><b id="draft-elapsed">0초 지남</b> · 보통 30~50초 걸려요. AI가 영상 속 장면을 짚은 댓글 후보 여러 개를 쓰고, 규칙 검사와 심사를 거쳐 가장 좋은 것을 골라요.</p>`;
    return;
  }
  if (S.draftError) {
    el.innerHTML = head + `<div class="notice bad">${esc(S.draftError)}</div><div class="row"><span></span><button class="btn" id="btn-regen">다시 시도</button></div>`;
    $("#btn-regen").onclick = () => startDraft(false);
    return;
  }
  const d = S.draft;
  if (!d) { el.innerHTML = head; return; }
  const sel = d.selected;
  const scenes = d.scenes.map((s) => `<li title="${esc(s.why)}">${s.ts ? `<a class="ts" target="_blank" href="https://www.youtube.com/watch?v=${v.video_id}&t=${tsToSec(s.ts)}s">${esc(s.ts)}</a>` : ""}<span><q>${esc(s.quote)}</q><span class="why muted small"> — ${esc(s.why)}</span></span></li>`).join("");
  const j = sel?.judge;
  const alts = (d.alternatives || []).map((c, i) => `<button class="alt" data-alt="${i}">${esc(c.text)}<span class="muted">심사 ${c.judge.total}점 · ${esc(c.judge.reason)}</span></button>`).join("");
  el.innerHTML = head + `
    <div class="block-title">💬 영상 속 연애 고민</div>
    <div class="concern">${esc(d.concern_one_liner)}<span class="criterion">풀려면 필요한 기준: ${esc(d.criterion)}</span></div>
    <div class="block-title">📍 근거 장면 <span class="muted small">시간을 누르면 그 장면으로 · <a href="#" id="toggle-why">${S.showWhy ? "설명 접기" : "설명 보기"}</a></span></div>
    <ul class="scenes ${S.showWhy ? "show-why" : ""}">${scenes}</ul>
    <div class="block-title">✏️ 댓글 초안 <span class="muted small" id="edit-state">직접 고칠 수 있어요</span></div>
    ${sel ? "" : `<div class="notice warn">규칙을 통과한 후보가 없어요. [다시 만들기]를 누르거나 직접 써 주세요.</div>`}
    <textarea id="draft" spellcheck="false">${esc(S.text)}</textarea>
    <div class="counter" id="counter"></div>
    <div class="checks" id="checks"></div>
    ${j ? `<div class="judge" id="judge"><b>AI 심사 ${j.total}점</b> · 맥락 ${j.uniqueness} · 자연스러움 ${j.naturalness} · 유도력 ${j.persuasion}<br/>${esc(j.reason)}</div>` : ""}
    ${d.needs_review ? `<div class="notice warn">심사 기준(3.8점)에 못 미쳐요. 다시 만들거나 직접 다듬어 주세요.</div>` : ""}
    ${alts ? `<div class="block-title">다른 후보 <span class="muted small">누르면 초안으로 바꿔요</span></div><div class="alts">${alts}</div>` : ""}
    <div class="row"><span class="muted small">페르소나: 운명원 평판을 아는 조언자 · 이용 후기는 쓰지 않아요</span><button class="btn" id="btn-regen">↻ 다시 만들기</button></div>`;
  const ta = $("#draft");
  ta.oninput = () => { S.text = ta.value; S.draftId = null; $("#judge")?.remove(); renderEditState(); debounceValidate(); };
  renderEditState();
  $("#btn-regen").onclick = () => startDraft(true);
  $("#toggle-why").onclick = (e) => { e.preventDefault(); S.showWhy = !S.showWhy; $(".scenes").classList.toggle("show-why", S.showWhy); e.target.textContent = S.showWhy ? "설명 접기" : "설명 보기"; };
  el.querySelectorAll("[data-alt]").forEach((b) => (b.onclick = () => {
    const c = d.alternatives[+b.dataset.alt];
    const cur = d.selected;
    d.alternatives[+b.dataset.alt] = cur; d.selected = c;
    S.text = c.text; S.draftId = c.id; S.originalText = c.text; renderDraft(); validateNow();
  }));
  renderChecks();
}

function renderEditState() {
  const el = $("#edit-state");
  if (!el) return;
  if (S.originalText != null && S.text !== S.originalText) {
    el.innerHTML = `직접 수정함 · <a href="#" id="btn-revert">원래 초안으로</a>`;
    $("#btn-revert").onclick = (e) => { e.preventDefault(); S.text = S.originalText; const ta = $("#draft"); ta.value = S.text; renderEditState(); validateNow(); };
  } else el.textContent = "직접 고칠 수 있어요";
}

function renderChecks() {
  const cfg = S.status?.comment_cfg || { min: 40, max: 120 };
  const t = S.text.trim();
  const withSp = [...t].length, noSp = [...t.replace(/\s/g, "")].length;
  const c = $("#counter");
  if (c) c.innerHTML = `<span>공백 포함 <span class="${withSp > cfg.max ? "bad" : ""}">${withSp}/${cfg.max}자</span> · 공백 제외 <span class="${noSp < cfg.min ? "bad" : ""}">${noSp}자</span> (최소 ${cfg.min})</span>`;
  const box = $("#checks");
  if (!box || !S.validation) return;
  box.innerHTML = S.validation.checks.map((ch) => `<span class="chip ${ch.ok ? "ok" : "bad"}" title="${esc(ch.detail)}">${ch.ok ? "✓" : "✗"} ${esc(ch.label)}${!ch.ok && ch.detail ? ` — ${esc(ch.detail)}` : ""}</span>`).join("");
  $("#draft")?.classList.toggle("invalid", !S.validation.ok);
}

let vTimer;
function debounceValidate() { renderChecks(); clearTimeout(vTimer); vTimer = setTimeout(validateNow, 350); renderPostPanel(); }
async function validateNow() {
  if (!S.selected) return;
  try {
    S.validation = await api("/api/drafts/validate", { method: "POST", body: { video_id: S.selected.video_id, text: S.text } });
  } catch { return; }
  renderChecks(); renderPostPanel();
}

// ── ③ 댓글 달기 ───────────────────────────────────────────────────────
function readyProfiles() { return (S.status?.profiles || []).filter((p) => !BAD_PROFILE.includes(p.status)); }
function autoPick() {
  const ps = readyProfiles();
  if (!ps.length) return null;
  return ps.reduce((a, b) => (b.today < a.today ? b : a));
}

function renderProfileSelect() {
  const sel = $("#profile-select");
  const prev = sel.value || "auto";
  const pick = autoPick();
  const opts = [`<option value="auto">자동 배정${pick ? ` → ${esc(pick.name)}` : ""}</option>`];
  for (const p of S.status?.profiles || []) {
    const bad = BAD_PROFILE.includes(p.status);
    opts.push(`<option value="${esc(p.name)}" ${bad ? "disabled" : ""}>${esc(p.name)} · 오늘 ${p.today}개${bad ? ` (${PROFILE_LABEL[p.status]})` : ""}</option>`);
  }
  sel.innerHTML = opts.join("");
  sel.value = [...sel.options].some((o) => o.value === prev && !o.disabled) ? prev : "auto";
  $("#profile-hint").textContent = sel.value === "auto"
    ? "오늘 게시가 가장 적은 프로필이 자동으로 골라져요. 한 영상엔 한 계정만 달아요."
    : "선택한 프로필의 크롬 창이 열리고 직접 타이핑해요.";
}

// 반환: [문구, 'info'|'block'] — info는 다음에 할 일을 알려 주는 회색 안내, block은 규칙상 막힘(빨강)
function postBlockedReason() {
  const r = postBlockedText();
  if (!r) return ["", ""];
  const info = ["① 목록에서", "초안을 만드는 중", "초안이 있어야", "게시하는 중"].some((k) => r.startsWith(k));
  return [r, info ? "info" : "block"];
}
function postBlockedText() {
  const st = S.status;
  if (!S.selected) return "① 목록에서 영상을 고르면 초안이 만들어지고, 여기서 바로 달 수 있어요.";
  // 영상당 1개 규칙은 초안보다 먼저 알려 준다
  if (st?.blocked_reason?.startsWith("이미")) return st.blocked_reason;
  if (S.selected.commented) return "이미 댓글을 단 영상이에요. 영상당 댓글은 1개만 달 수 있어요.";
  if (S.drafting) return "초안을 만드는 중이에요. 잠시만 기다려 주세요.";
  if (S.posting && !S.posting.done) return "";
  if (S.selected.tab === "rising") return "뜨기 직전 영상은 초안까지만 만들어요. (요구사항)";
  if (!S.text.trim()) return "댓글 초안이 있어야 달 수 있어요.";
  if (S.validation && !S.validation.ok) return "초안이 댓글 규칙을 통과해야 달 수 있어요. (빨간 항목 확인)";
  if (!st) return "";
  if (st.blocked_reason) {
    if (S.cooldownUntil > Date.now() && st.blocked_reason.includes("초 후")) return `연속 게시를 막기 위해 ${Math.ceil((S.cooldownUntil - Date.now()) / 1000)}초 후에 달 수 있어요.`;
    return st.blocked_reason;
  }
  if (!readyProfiles().length) return "사용할 수 있는 프로필이 없어요. 위 '시작하기 전에' 안내대로 로그인해 주세요.";
  return "";
}

function renderPostPanel() {
  renderProfileSelect();
  const [reason, level] = postBlockedReason();
  const busy = S.posting && !S.posting.done;
  const btn = $("#btn-post");
  btn.disabled = !!reason || busy;
  btn.classList.toggle("loading", !!busy);
  const selVal = $("#profile-select").value;
  const who = selVal === "auto" ? autoPick()?.name : selVal;
  btn.textContent = who ? `${ro(who)} 댓글 달기` : "댓글 달기";
  const pb = $("#post-blocked");
  pb.textContent = reason;
  pb.className = "blocked " + level;
  renderStepper();
}

function renderStepper() {
  const ol = $("#stepper"), out = $("#post-result");
  const P = S.posting;
  if (!P) {
    ol.innerHTML = `<li class="preview-title">[댓글 달기]를 누르면 크롬 창이 화면 오른쪽에 열리고 이렇게 진행돼요</li>` +
      POST_STEPS.map(([, label]) => `<li><span class="dot"></span>${label}</li>`).join("");
    out.innerHTML = "";
    return;
  }
  const evs = P.events;
  const last = evs[evs.length - 1] || {};
  const reached = new Set(evs.map((e) => e.stage));
  const failed = ["failed", "unverified"].includes(last.stage);
  const failedAt = failed ? last.failed_stage : null;
  let hitCurrent = false;
  ol.innerHTML = POST_STEPS.map(([key, label], i) => {
    let cls = "", dot = "";
    if (failed && key === failedAt) { cls = "fail"; dot = "!"; hitCurrent = true; }
    else if (hitCurrent) cls = "";
    else if (reached.has(key)) {
      const nextReached = POST_STEPS.slice(i + 1).some(([k]) => reached.has(k)) || last.stage === "posted";
      cls = nextReached ? "done" : failed ? "done" : "now"; dot = cls === "done" ? "✓" : "";
      if (cls === "now") hitCurrent = true;
    }
    let extra = "";
    if (key === "typing" && reached.has("typing")) {
      const t = [...evs].reverse().find((e) => e.stage === "typing");
      if (t?.total) extra = ` <span class="muted">${t.typed}/${t.total}자</span><div class="typing-bar"><i style="width:${(100 * t.typed) / t.total}%"></i></div>`;
    }
    if (key === "opening_profile" && P.profile) extra = ` <span class="muted">(${esc(P.profile)})</span>`;
    return `<li class="${cls}"><span class="dot">${dot}</span><div>${label}${extra}</div></li>`;
  }).join("");

  if (last.stage === "posted") {
    out.innerHTML = `<div class="result-box ok"><h4>✓ 게시 완료 — 내 댓글이 보이는 것까지 확인했어요</h4>
      <a href="${esc(last.comment_url)}" target="_blank">댓글 보기 ↗</a>
      ${last.screenshot_path ? `<img src="/${esc(last.screenshot_path)}" alt="게시 확인 캡처" />` : ""}
      <p class="next">다음: ①에서 다른 영상을 고르세요. 연속 게시를 막기 위해 잠시 뒤에 달 수 있어요.</p></div>`;
  } else if (failed) {
    out.innerHTML = `<div class="result-box ${last.stage === "unverified" ? "warn" : "bad"}">
      <h4>${last.stage === "unverified" ? "⚠ 확인 실패" : "✗ 실패"}</h4><div>${esc(last.error_message || "")}</div>
      <p class="next">${last.stage === "unverified" ? "게시됐을 수 있어 이 영상엔 다시 달 수 없어요. 아래 기록에서 직접 확인 후 정리해 주세요." : "이유를 해결한 뒤 다시 [댓글 달기]를 누르면 돼요. 캡처를 누르면 크게 보여요."}</p>
      ${last.screenshot_path ? `<img src="/${esc(last.screenshot_path)}" alt="실패 화면 캡처" />` : ""}</div>`;
  } else out.innerHTML = "";
  out.querySelectorAll("img").forEach((img) => (img.onclick = () => openLightbox(img.src)));
}

async function post() {
  const sel = $("#profile-select").value;
  const btn = $("#btn-post");
  btn.disabled = true;
  try {
    const r = await api("/api/post", { method: "POST", body: {
      video_id: S.selected.video_id, text: S.text, profile: sel === "auto" ? null : sel,
      draft_id: S.draftId, video_title: S.selected.title, tab: S.selected.tab || "latest" } });
    S.posting = { attempt_id: r.attempt_id, profile: r.profile, events: [], done: false };
    renderPostPanel(); highlightStep();
    const es = new EventSource(`/api/post/${r.attempt_id}/events`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.stage === "typing") {
        const lastEv = S.posting.events[S.posting.events.length - 1];
        if (lastEv?.stage === "typing") S.posting.events[S.posting.events.length - 1] = ev; else S.posting.events.push(ev);
      } else S.posting.events.push(ev);
      if (["posted", "failed", "unverified"].includes(ev.stage)) {
        S.posting.done = true; es.close();
        loadHistory(r.attempt_id); refreshStatus(); markCommented();
        toast(ev.stage === "posted" ? "게시 완료! 내 댓글이 보이는 것까지 확인했어요." : ev.error_message || "실패했어요.", ev.stage !== "posted");
      }
      renderPostPanel();
    };
    es.onerror = () => { if (S.posting && !S.posting.done) { /* 브라우저가 자동 재연결 */ } };
    loadHistory(r.attempt_id);
  } catch (e) {
    toast(e.message, true);
    refreshStatus();
  }
}

function markCommented() {
  for (const tab of ["latest", "rising"]) S.videos[tab].forEach((v) => { if (v.video_id === S.selected?.video_id && S.posting?.events.some((e) => e.stage === "posted")) v.commented = true; });
  renderList();
}

// 프로필 관리
function renderProfiles() {
  const box = $("#profiles");
  box.innerHTML = (S.status?.profiles || []).map((p) => `
    <div class="prow"><div><span class="name">${esc(p.name)}</span>
      <span class="chip ${p.status === "ok" ? "ok" : BAD_PROFILE.includes(p.status) ? "bad" : "warn"}">${PROFILE_LABEL[p.status] || "확인 필요"}</span>
      <div class="msg">${esc(p.message)} · 오늘 ${p.today}개</div></div>
      <div style="display:flex;gap:4px"><button class="btn small" data-login="${esc(p.name)}">로그인</button><button class="btn small" data-check="${esc(p.name)}" ${p.status === "missing" ? "disabled" : ""}>점검</button></div>
    </div>`).join("");
  box.querySelectorAll("[data-login]").forEach((b) => (b.onclick = () => profileLogin(b.dataset.login)));
  box.querySelectorAll("[data-check]").forEach((b) => (b.onclick = () => profileCheck(b.dataset.check, b)));
}
async function profileLogin(name) {
  try { await api(`/api/profiles/${encodeURIComponent(name)}/login`, { method: "POST" }); toast("로그인 창을 열었어요. 구글 로그인 후 ⌘Q로 닫고 [점검]을 눌러 주세요."); refreshStatus(); }
  catch (e) { toast(e.message, true); }
}
async function profileCheck(name, btn) {
  if (btn) setLoading(btn, true);
  toast(`${name} 크롬을 잠깐 열어 로그인 상태를 확인하고 있어요…`);
  try { const r = await api(`/api/profiles/${encodeURIComponent(name)}/check`, { method: "POST" }); toast(`${name}: ${r.message}`, r.status !== "ok"); }
  catch (e) { toast(e.message, true); }
  finally { refreshStatus(); }
}

// ── 기록 ─────────────────────────────────────────────────────────────
async function loadHistory(highlightId) {
  let r;
  try { r = await api("/api/attempts"); } catch { return; }
  const rows = r.attempts;
  $("#history-empty").hidden = rows.length > 0;
  $("#history").innerHTML = rows.map((a) => {
    const [label, cls] = STATUS_LABEL[a.status] || [a.status, ""];
    const t = new Date(a.started_at);
    const time = `${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}:${String(t.getSeconds()).padStart(2, "0")}`;
    const links = [
      a.comment_url ? `<a href="${esc(a.comment_url)}" target="_blank">댓글 보기</a>` : "",
      a.screenshot_path ? `<a href="#" data-shot="/${esc(a.screenshot_path)}">캡처</a>` : "",
      a.status === "unverified" ? `<button class="btn small" data-resolve="${a.id}" data-to="posted">게시됨</button> <button class="btn small" data-resolve="${a.id}" data-to="failed">실패</button>` : "",
    ].filter(Boolean).join(" · ");
    return `<tr class="${a.id === highlightId ? "new" : ""}">
      <td class="time">${time}</td><td>${esc(a.profile)}</td>
      <td class="video"><a href="https://www.youtube.com/watch?v=${esc(a.video_id)}" target="_blank">${esc(a.video_title || a.video_id)}</a></td>
      <td class="comment">${esc(a.comment_text)}${a.error_message && a.status !== "posted" ? `<div class="why">${esc(a.error_message)}</div>` : ""}</td>
      <td><span class="chip ${cls}">${label}</span></td><td>${links}</td></tr>`;
  }).join("");
  document.querySelectorAll("[data-shot]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openLightbox(a.dataset.shot); }));
  document.querySelectorAll("[data-resolve]").forEach((b) => (b.onclick = async () => {
    try { await api(`/api/attempts/${b.dataset.resolve}/resolve`, { method: "POST", body: { status: b.dataset.to } }); loadHistory(); refreshStatus(); }
    catch (e) { toast(e.message, true); }
  }));
}

function openLightbox(src) { const lb = $("#lightbox"); $("img", lb).src = src; lb.hidden = false; }

// 지금 해야 할 칸 강조 (사용성)
function highlightStep() {
  let cur = 1;
  if (S.selected) cur = S.drafting || !S.text ? 2 : 3;
  if (!S.loaded.latest && !S.loaded.rising && !S.selected) cur = 1;
  ["#col1", "#col2", "#col3"].forEach((id, i) => {
    $(id).classList.toggle("current", i + 1 === cur);
    $(id).classList.toggle("dim", i + 1 > cur && !(S.posting));
  });
}

// ── 시작 ─────────────────────────────────────────────────────────────
function init() {
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => { S.tab = t.dataset.tab; syncTabs(); renderList(); }));
  $("#btn-load").onclick = loadVideos;
  $("#link-form").onsubmit = addLink;
  $("#video-list").onclick = (e) => {
    const card = e.target.closest(".vcard");
    if (!card) return;
    const v = S.videos[S.tab].find((x) => x.video_id === card.dataset.id);
    if (v && v.video_id !== S.selected?.video_id) selectVideo(v);
  };
  $("#video-list").onkeydown = (e) => { if (e.key === "Enter") e.target.closest(".vcard")?.click(); };
  $("#btn-post").onclick = post;
  $("#profile-select").onchange = () => renderPostPanel();
  $("#lightbox").onclick = () => ($("#lightbox").hidden = true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });
  window.addEventListener("beforeunload", (e) => {
    if ((S.posting && !S.posting.done) || S.drafting) { e.preventDefault(); e.returnValue = ""; }
  });
  syncTabs(); renderList(); renderDraft(); renderStepper(); highlightStep();
  refreshStatus(); loadHistory();
  // 새로고침해도 서버가 기억하는 목록을 다시 보여 준다
  for (const tab of ["latest", "rising"]) {
    api(`/api/videos?tab=${tab}`).then((r) => {
      if (r.videos.length) { S.videos[tab] = r.videos; S.loaded[tab] = true; if (tab === S.tab) renderList(); }
    }).catch(() => {});
  }
  setInterval(() => { if (S.cooldownUntil > Date.now() || S.status?.cooldown_sec) { renderPostPanel(); if (S.cooldownUntil <= Date.now()) refreshStatus(); } }, 1000);
}
init();
