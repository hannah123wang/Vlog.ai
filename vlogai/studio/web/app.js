// vlogai studio —— 前端（无构建步骤，原生 JS）
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const d = await r.json().catch(() => ({ error: "服务器没有返回 JSON" }));
  if (d.error) throw new Error(d.error);
  return d;
};
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body || {}) });
const mins = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const esc = (s) => (s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));

let state = { pid: null, cur: null, shots: [], story: null, outline: null, lineCount: 0 };
const mock = () => $("#mock").checked;

// ----------------------------------------------------------- 文件夹浏览
async function ls(path) {
  const d = await api("/api/ls?path=" + encodeURIComponent(path || ""));
  $("#curPath").textContent = d.path;
  $("#curPath").dataset.path = d.path;
  $("#upBtn").disabled = !d.parent;
  $("#upBtn").dataset.path = d.parent || "";
  $("#dirList").innerHTML = d.dirs.map((x) =>
    `<div data-path="${esc(x.path)}"><span>${esc(x.name)}</span>` +
    `<span class="n">${x.videos ? x.videos + " 个视频" : ""}</span></div>`).join("")
    || `<div class="muted" style="padding:.6rem">这里没有子文件夹</div>`;
  pick(d.path, d.videos_here);
}
function pick(path, n) {
  $("#pickedPath").textContent = path;
  $("#pickedCount").textContent = n ? `这个文件夹里有 ${n} 个视频，可以直接扫描` : "这个文件夹里没有视频，进下一层看看";
  $("#createBtn").disabled = !n;
  $("#createBtn").dataset.path = path;
}
$("#dirList").addEventListener("click", (e) => {
  const d = e.target.closest("div[data-path]");
  if (d) ls(d.dataset.path);
});
$("#upBtn").onclick = () => ls($("#upBtn").dataset.path);
$("#createBtn").onclick = async () => {
  const footage = $("#createBtn").dataset.path;
  const d = await post("/api/projects", { footage, name: $("#projName").value });
  pollJob(() => openProject(d.id));
};

// ----------------------------------------------------------- 项目
async function loadProjects() {
  const d = await api("/api/projects");
  $("#projList").innerHTML = d.projects.map((p) =>
    `<a data-id="${esc(p.id)}"><div class="t">${esc(p.name)}</div>
     <div class="muted">${p.clips} 条素材 · ${p.duration_min} 分钟 · ${p.days} 天` +
    (p.has_story ? ` · 已有粗剪 ${mins(p.story_seconds)}` : p.logged ? ` · 已看 ${p.logged} 个镜头` : "") +
    `</div></a>`).join("") || `<div class="muted">还没有项目</div>`;
}
$("#projList").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]");
  if (a) openProject(a.dataset.id);
});
$("#backBtn").onclick = () => {
  state.pid = null;
  $("#home").hidden = false; $("#project").hidden = true; $("#backBtn").hidden = true;
  loadProjects();
};

async function openProject(pid) {
  state.pid = pid;
  $("#home").hidden = true; $("#project").hidden = false; $("#backBtn").hidden = false;
  await refresh();
  showStep(state.cur.has_story ? "timeline" : state.cur.logged ? "story" : "footage");
}

async function refresh() {
  const p = await api("/api/projects/" + encodeURIComponent(state.pid));
  state.cur = p;
  $("#pTitle").textContent = p.name;
  $("#pStats").textContent =
    `${p.clips} 条素材 · ${p.duration_min} 分钟 · ${p.days} 天 · ${p.shots} 个镜头` +
    (p.logged ? ` · 已看 ${p.logged}` : "") + (p.unusable ? ` · ${p.unusable} 个废片` : "");
  if (p.settings?.minutes) $("#minutes").value = p.settings.minutes;
  if (p.settings?.brief && !$("#idea").value) $("#idea").value = p.settings.brief;
  if (p.settings?.music) $("#music").value = p.settings.music;
  $("#previewCard").hidden = !p.has_preview;
  if (p.has_preview) $("#previewVideo").src = `/media/${encodeURIComponent(p.id)}/preview.mp4?t=` + Date.now();
  $("#outFiles").innerHTML =
    (p.has_xml ? `<code>projects/${esc(p.id)}/timeline.xml</code>` : "") +
    (p.has_preview ? `<code>projects/${esc(p.id)}/preview.mp4</code>` : "");
  return p;
}

function showStep(name) {
  $$(".steps button").forEach((b) => b.classList.toggle("on", b.dataset.step === name));
  $$(".step").forEach((s) => (s.hidden = s.id !== "step-" + name));
  if (name === "footage") loadShots();
  if (name === "story") loadOutline();
  if (name === "timeline") loadStory();
}
$$(".steps button").forEach((b) => (b.onclick = () => showStep(b.dataset.step)));

// ----------------------------------------------------------- 1 素材
$("#analyzeBtn").onclick = async () => {
  await post(`/api/projects/${encodeURIComponent(state.pid)}/analyze`,
             { mock: mock(), whisper: $("#whisper").checked });
  pollJob(async () => { await refresh(); loadShots(); });
};
async function loadShots() {
  const d = await api(`/api/projects/${encodeURIComponent(state.pid)}/shots`);
  state.shots = d.shots;
  $("#shotsCard").hidden = !d.shots.length;
  const days = [...new Set(d.shots.map((s) => s.day))].sort((a, b) => a - b);
  $("#dayFilter").innerHTML = `<option value="">全部日期</option>` +
    days.map((x) => `<option value="${x}">Day ${x}</option>`).join("");
  renderShots();
}
function renderShots() {
  const day = $("#dayFilter").value, hide = $("#hideBad").checked;
  const list = state.shots.filter((s) => (!day || String(s.day) === day) && (!hide || s.quality >= 0.25));
  $("#shotCount").textContent = `${list.length} 个镜头`;
  $("#shotGrid").innerHTML = list.map((s) => `
    <div class="shot ${s.quality < 0.25 ? "bad" : ""}">
      ${s.thumb ? `<img loading="lazy" src="${s.thumb}">` : ""}
      <div class="meta">
        <div class="sum">${esc(s.summary) || "（还没分析）"}</div>
        <div>Day ${s.day} ${s.time} · ${s.duration.toFixed(1)}s
          ${s.highlight ? `<span class="badge hl">★${s.highlight}</span>` : ""}
          ${s.shot_type ? `<span class="badge">${esc(s.shot_type)}</span>` : ""}
          ${s.quality < 0.25 ? `<span class="badge">废片</span>` : ""}</div>
      </div>
    </div>`).join("");
}
$("#dayFilter").onchange = renderShots;
$("#hideBad").onchange = renderShots;

// ----------------------------------------------------------- 2 故事线
$("#outlineBtn").onclick = async () => {
  await post(`/api/projects/${encodeURIComponent(state.pid)}/outline`,
             { idea: $("#idea").value, minutes: +$("#minutes").value, mock: mock() });
  pollJob(loadOutline);
};
async function loadOutline() {
  const d = await api(`/api/projects/${encodeURIComponent(state.pid)}/outline`);
  state.outline = d;
  $("#beatsCard").hidden = !(d.beats || []).length;
  if (!d.beats) return;
  $("#outTitle").textContent = d.title || "分幕结构";
  $("#outLogline").textContent = d.logline || "";
  $("#missingBox").hidden = !(d.missing || []).length;
  if ((d.missing || []).length)
    $("#missingBox").innerHTML = "⚠️ 你的想法里这些内容，素材中没有找到：<br>· " +
      d.missing.map(esc).join("<br>· ") + (d.notes ? `<br><br>${esc(d.notes)}` : "");
  renderBeats();
}
function renderBeats() {
  $("#beats").innerHTML = state.outline.beats.map((b, i) => `
    <div class="beat" data-i="${i}">
      <div class="top">
        <div class="mv"><button class="ghost up">▲</button><button class="ghost down">▼</button></div>
        <input class="t" value="${esc(b.title)}">
        <label class="muted"><input class="sec" type="number" value="${Math.round(b.seconds || 0)}"
          style="width:4.5rem"> 秒</label>
        <button class="ghost del">删</button>
      </div>
      <textarea class="intent" rows="2">${esc(b.intent || "")}</textarea>
      <div class="ev">素材依据：${esc(b.evidence || "")} ${b.shot_ids?.length ? `（${b.shot_ids.length} 个候选镜头）` : ""}</div>
    </div>`).join("");
}
$("#beats").addEventListener("click", (e) => {
  const card = e.target.closest(".beat"); if (!card) return;
  const i = +card.dataset.i, B = state.outline.beats;
  if (e.target.classList.contains("del")) B.splice(i, 1);
  else if (e.target.classList.contains("up") && i > 0) [B[i - 1], B[i]] = [B[i], B[i - 1]];
  else if (e.target.classList.contains("down") && i < B.length - 1) [B[i + 1], B[i]] = [B[i], B[i + 1]];
  else return;
  renderBeats();
});
$("#beats").addEventListener("input", (e) => {
  const card = e.target.closest(".beat"); if (!card) return;
  const b = state.outline.beats[+card.dataset.i];
  if (e.target.classList.contains("t")) b.title = e.target.value;
  if (e.target.classList.contains("sec")) b.seconds = +e.target.value;
  if (e.target.classList.contains("intent")) b.intent = e.target.value;
});
$("#addBeat").onclick = () => {
  state.outline.beats.push({ id: "b" + Date.now(), title: "新的一幕", intent: "", seconds: 30, shot_ids: [] });
  renderBeats();
};
$("#storyBtn").onclick = async () => {
  await post(`/api/projects/${encodeURIComponent(state.pid)}/story`,
             { beats: state.outline.beats, minutes: +$("#minutes").value, mock: mock() });
  pollJob(async () => { await refresh(); showStep("timeline"); });
};

// ----------------------------------------------------------- 3 时间线
async function loadStory() {
  const d = await api(`/api/projects/${encodeURIComponent(state.pid)}/story`);
  state.story = d;
  $("#tlTitle").textContent = d.title || "还没有粗剪";
  $("#tlStats").textContent = d.timeline.length
    ? `${d.timeline.length} 个片段 · ${mins(d.total_seconds)} · ${esc(d.logline)}` : "先在第 2 步生成";
  renderTl();
}
function renderTl() {
  let chap = null, html = "";
  state.story.timeline.forEach((t, i) => {
    if (t.chapter !== chap) { chap = t.chapter; html += `<div class="chapline">${esc(chap || "")}</div>`; }
    html += `
      <div class="seg ${t.cut ? "cut" : ""}" data-i="${i}">
        ${t.thumb ? `<img src="${t.thumb}">` : ""}
        <div class="info">
          <div>${esc(t.summary) || esc(t.shot_id)}</div>
          <div class="why">✂️ ${esc(t.why)} ${t.keep_audio ? "· 🔊原声" : ""}</div>
        </div>
        <div class="t">${mins(t.at)}<br>${t.dur.toFixed(1)}s</div>
        <div class="ops">
          <button class="ghost trim" data-d="-1">−1s</button>
          <button class="ghost trim" data-d="1">+1s</button>
          <button class="ghost up">▲</button><button class="ghost down">▼</button>
          <button class="ghost del">${t.cut ? "恢复" : "删"}</button>
        </div>
      </div>`;
  });
  $("#tlList").innerHTML = html;
}
$("#tlList").addEventListener("click", (e) => {
  const seg = e.target.closest(".seg"); if (!seg) return;
  const i = +seg.dataset.i, T = state.story.timeline, t = T[i];
  if (e.target.classList.contains("del")) t.cut = !t.cut;
  else if (e.target.classList.contains("trim")) {
    const d = +e.target.dataset.d;
    t.out = Math.max(t.in + 0.8, Math.min(t.shot_dur || t.out + d, t.out + d));
    t.dur = +(t.out - t.in).toFixed(2);
  } else if (e.target.classList.contains("up") && i > 0) [T[i - 1], T[i]] = [T[i], T[i - 1]];
  else if (e.target.classList.contains("down") && i < T.length - 1) [T[i + 1], T[i]] = [T[i], T[i + 1]];
  else return;
  let at = 0;
  T.forEach((x) => { x.at = +at.toFixed(1); if (!x.cut) at += x.out - x.in; });
  state.story.total_seconds = +at.toFixed(1);
  $("#saveTl").hidden = false;
  renderTl();
});
$("#saveTl").onclick = async () => {
  const keep = state.story.timeline.filter((t) => !t.cut)
    .map((t) => ({ shot_id: t.shot_id, in: t.in, out: t.out }));
  state.story = await post(`/api/projects/${encodeURIComponent(state.pid)}/timeline`, { timeline: keep });
  $("#saveTl").hidden = true;
  renderTl();
  $("#tlStats").textContent = `${state.story.timeline.length} 个片段 · ${mins(state.story.total_seconds)}`;
};
$("#reviseBtn").onclick = async () => {
  if (!$("#reviseBox").value.trim()) return;
  await post(`/api/projects/${encodeURIComponent(state.pid)}/revise`,
             { instruction: $("#reviseBox").value, minutes: +$("#minutes").value, mock: mock() });
  pollJob(async () => { $("#reviseBox").value = ""; await refresh(); loadStory(); });
};

// ----------------------------------------------------------- 4 导出
$("#renderBtn").onclick = async () => {
  await post(`/api/projects/${encodeURIComponent(state.pid)}/render`,
             { music: $("#music").value, vertical: $("#vertical").checked, hd: $("#hd").checked });
  pollJob(refresh);
};
$("#exportBtn").onclick = async () => {
  await post(`/api/projects/${encodeURIComponent(state.pid)}/export`, {});
  pollJob(refresh);
};

// ----------------------------------------------------------- 任务进度
let timer = null;
function pollJob(onDone) {
  state.lineCount = 0;
  $("#jobBar").hidden = false;
  $("#jobLog").textContent = "";
  clearInterval(timer);
  timer = setInterval(async () => {
    let j;
    try { j = await api("/api/job?since=" + state.lineCount); } catch { return; }
    state.lineCount = j.line_count || 0;
    $("#jobName").textContent = j.name || "";
    $("#jobPct").textContent = j.total ? `${j.done}/${j.total}` : "";
    const fill = $("#jobFill");
    if (j.pct != null) { fill.classList.remove("indet"); fill.style.width = j.pct + "%"; }
    else fill.classList.add("indet");
    if (j.lines?.length) {
      $("#jobLog").textContent += j.lines.join("\n") + "\n";
      $("#jobLog").scrollTop = $("#jobLog").scrollHeight;
    }
    if (j.status !== "running") {
      clearInterval(timer);
      fill.classList.remove("indet");
      fill.style.width = "100%";
      if (j.status === "error") { $("#jobLog").hidden = false; $("#jobToggle").textContent = "收起"; }
      else setTimeout(() => ($("#jobBar").hidden = true), 2500);
      onDone && onDone();
    }
  }, 900);
}
$("#jobToggle").onclick = () => {
  const log = $("#jobLog");
  log.hidden = !log.hidden;
  $("#jobToggle").textContent = log.hidden ? "展开" : "收起";
};

// ----------------------------------------------------------- 启动
(async () => {
  const env = await api("/api/env");
  $("#keyState").textContent = env.has_key ? "API Key 已就绪" : "未设置 API Key · 只能离线模拟";
  if (!env.has_key) $("#mock").checked = true;
  await ls(env.home);
  await loadProjects();
  const running = await api("/api/job");
  if (running.status === "running") pollJob(() => state.pid && refresh());
})();
