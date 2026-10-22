/* vlogai studio —— 前端（无构建步骤，原生 JS）。接口见 API.md。 */
'use strict';

const S = {
  env: { has_key: false, home: '', drives: [] },
  projects: [], pid: null, p: null,          // p = GET /api/projects/{id}
  shots: [], byId: {}, outline: null, story: null, versions: [],
  page: 'footage',
  fsel: null, fframe: null, day: 'all',
  filters: { q: '', star: false, talk: false, vert: false, hideBlur: false },
  outlineDirty: false,
  tl: null, tlDirty: false, tsel: 0,
  job: null, jobLines: [], jobSince: 0, jobOpen: false, jobHidden: false,
  hd: false, whisper: false,
  logs: null, logLevel: 'INFO', logQ: '', logAuto: true, logCounts: null,
  cfg: null, cfgError: null, draft: null, cfgDirty: false, newKey: '', showKey: false, keyTest: null,
};

/* ---------------------------------------------------------------- helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function mmss(sec) { sec = Math.max(0, Math.round(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
const SHOT_TYPES = { wide: '远景', medium: '中景', 'close-up': '近景', detail: '特写', selfie: '自拍', pov: '第一人称', drone: '航拍', timelapse: '延时', screen: '屏幕' };
const shotType = (t) => SHOT_TYPES[t] || t || '';
const enc = encodeURIComponent;
const P = () => `/api/projects/${enc(S.pid)}`;
const jobRunning = () => !!(S.job && S.job.status === 'running');
const dis = () => (jobRunning() ? 'disabled' : '');
const media = (name, mt) => `/media/${enc(S.pid)}/${name}?t=${mt || Date.now()}`;
const thumbOf = (id) => (S.byId[id] || {}).thumb || '';
function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem('vlogai.' + k)); localStorage.setItem('vlogai.' + k, JSON.stringify(v)); } catch (e) { return null; } return null; }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({ error: '服务器没有返回 JSON' }));
  if (!r.ok || d.error) throw new Error(d.error || `请求失败 (${r.status})`);
  return d;
}
function toast(msg, bad) {
  const p = $('#jobPanel');
  p.hidden = false; p.className = 'job ' + (bad ? 'fail' : 'done');
  $('#jobName').textContent = bad ? '出错了' : '好了';
  $('#jobLast').textContent = msg;
  $('#jobLog').hidden = true; $('#jobBar').hidden = true;
  clearTimeout(toast.t);
  if (!bad) toast.t = setTimeout(() => { if (!jobRunning()) p.hidden = true; }, 3500);
}
async function act(fn) { try { return await fn(); } catch (e) { toast(e.message, true); return null; } }
const mockOn = () => { const m = store('mock'); return m === null ? !S.env.has_key : !!m; };

/* ---------------------------------------------------------------- modal */
function modal(html, onReady) {
  const d = $('#modal'), body = $('#modalBody');
  body.innerHTML = html;
  return new Promise((resolve) => {
    d.onclose = () => resolve(d.returnValue || '');
    d.returnValue = '';
    if (onReady) onReady(body, (v) => d.close(v));
    d.showModal();
  });
}
async function confirmBox(title, text, ok = '确定') {
  const r = await modal(`<h2>${esc(title)}</h2><p class="muted" style="margin:0;line-height:1.6;white-space:pre-line;word-break:break-all">${esc(text)}</p>
    <div class="row-end"><button class="ghost" value="">取消</button><button class="btn" value="ok">${esc(ok)}</button></div>`);
  return r === 'ok';
}

/** 服务器端的文件夹浏览器（浏览器拿不到真实路径）。kind: 'video' 选文件夹，'audio' 选音乐（输入完整路径或在文件夹里找）。 */
async function browse(kind, start) {
  let chosen = '', name = '';
  const isNew = kind === 'new';
  const html = `<h2>${kind === 'audio' ? '选择背景音乐' : isNew ? '新建项目：选择素材文件夹' : '选择素材文件夹'}</h2>
    ${S.env.drives.length ? `<div class="drives">${S.env.drives.map((d) => `<button type="button" class="chip" style="min-height:32px" data-go="${esc(d)}">${esc(d)}</button>`).join('')}</div>` : ''}
    <div class="toolbar"><button type="button" class="ghost sm" id="bUp">↑ 上一级</button>
      <input class="field mono" id="bPath" style="font-size:12px;min-height:36px" aria-label="路径"><button type="button" class="ghost sm" id="bGo">前往</button></div>
    <div class="browse-list" id="bList"></div>
    ${kind === 'audio' ? `<label class="section"><span class="label">音乐文件的完整路径（mp3 / m4a / wav）</span>
      <input class="field mono" id="bFile" style="font-size:12px" placeholder="比如 D:\\Music\\bgm.mp3"></label>` : ''}
    ${isNew ? `<label class="section"><span class="label">项目名称（可留空，默认用文件夹名）</span><input class="field" id="bName" autocomplete="off"></label>` : ''}
    <div class="row-end"><span class="muted" id="bInfo" style="flex:1"></span><button class="ghost" value="">取消</button>
      <button class="btn" type="button" id="bPick">${kind === 'audio' ? '使用这个文件' : isNew ? '开始扫描' : '选择这个文件夹'}</button></div>`;
  const r = await modal(html, (b, close) => {
    let cur = null;
    const load = async (path) => {
      const d = await act(() => api(`/api/ls?path=${enc(path || '')}`));
      if (!d) return;
      cur = d;
      $('#bPath', b).value = d.path;
      $('#bUp', b).disabled = !d.parent;
      if (kind !== 'audio') {
        $('#bInfo', b).textContent = d.videos_here ? `这个文件夹里有 ${d.videos_here} 个视频` : '这里没有视频，进下一层看看';
        $('#bPick', b).disabled = !d.videos_here;
      }
      $('#bList', b).innerHTML = d.dirs.map((x) => `<button type="button" data-dir="${esc(x.path)}"><span class="ico">▸</span><span style="flex:1">${esc(x.name)}</span><span class="muted mono" style="font-size:12px">${x.videos ? x.videos + ' 个视频' : ''}</span></button>`).join('')
        || '<div class="muted" style="padding:14px">这里没有子文件夹</div>';
    };
    b.addEventListener('click', (e) => {
      const t = e.target.closest('button'); if (!t) return;
      if (t.dataset.dir) load(t.dataset.dir);
      else if (t.dataset.go) load(t.dataset.go);
      else if (t.id === 'bUp' && cur) load(cur.parent);
      else if (t.id === 'bGo') load($('#bPath', b).value);
      else if (t.id === 'bPick') {
        if (kind === 'audio') {
          chosen = $('#bFile', b).value.trim().replace(/^"|"$/g, '');
          if (!chosen) { $('#bInfo', b).textContent = '先填音乐文件路径'; return; }
        } else { if (!cur) return; chosen = cur.path; }
        if (isNew) name = $('#bName', b).value.trim();
        close('ok');
      }
    });
    $('#bPath', b).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); load(e.target.value); } });
    if (kind === 'audio') {
      $('#bInfo', b).textContent = '浏览器拿不到文件路径：可以在资源管理器里按住 Shift 右键音乐文件，选“复制文件地址”，粘贴到下面。';
      if (start) $('#bFile', b).value = start;
    }
    load(kind === 'audio' ? (start ? start.replace(/[\\/][^\\/]*$/, '') : S.env.home) : (start || S.env.home));
  });
  return r === 'ok' ? { path: chosen, name } : null;
}

/* ---------------------------------------------------------------- data */
async function loadProjects() { S.projects = (await api('/api/projects')).projects; }
async function loadProject() {
  if (!S.pid) { S.p = null; S.shots = []; S.byId = {}; S.outline = null; S.story = null; S.versions = []; return; }
  const [p, sh, ol, st, vs] = await Promise.all([
    api(P()), api(`${P()}/shots`), api(`${P()}/outline`), api(`${P()}/story`), api(`${P()}/versions`)]);
  S.p = p; S.shots = sh.shots; S.byId = Object.fromEntries(S.shots.map((s) => [s.id, s]));
  S.story = st.timeline && st.timeline.length ? st : null;
  S.versions = vs.versions;
  S.outlineServer = JSON.stringify(ol.beats || null);
  if (!S.outlineDirty) {
    // 没按“按这个结构剪”之前，分幕的修改只存在这台电脑的浏览器里（草稿）
    const draft = store('outline.' + S.pid);
    const useDraft = !!(draft && draft.ol && draft.base === S.outlineServer);
    S.outline = useDraft ? draft.ol : (ol.beats ? ol : null);
    S.outlineDirty = useDraft;
  }
  if (!S.tlDirty) resetTimeline();
}
function resetTimeline() {
  S.tl = S.story ? S.story.timeline.map((t) => ({ i: t.i, shot_id: t.shot_id, in: t.in, out: t.out, chapter: t.chapter, role: t.role, why: t.why, keep_audio: t.keep_audio, text: t.text })) : null;
  S.tlDirty = false;
  if (S.tl) S.tsel = clamp(S.tsel, 0, Math.max(0, S.tl.length - 1));
}
async function switchProject(pid) {
  S.pid = pid; store('project', pid);
  S.fsel = null; S.day = 'all'; S.tsel = 0; S.outlineDirty = false; S.tlDirty = false;
  await loadProject();
  render();
}
async function saveSettings(patch) {
  const d = await api(`${P()}/settings`, patch);
  S.p.settings = d.settings;
}

/* ---------------------------------------------------------------- jobs */
async function startJob(path, body, then) {
  const r = await act(() => api(path, body));
  if (!r) return false;
  S.job = { status: 'running', name: '', lines: [] }; S.jobLines = []; S.jobSince = 0; S.jobOpen = false; S.jobHidden = false;
  S.after = then || null;
  render(); pollJob();
  return true;
}
let pollTimer = null, tick = 0;
async function pollJob() {
  clearTimeout(pollTimer);
  let j;
  try { j = await api(`/api/job?since=${S.jobSince}`); } catch (e) { pollTimer = setTimeout(pollJob, 2000); return; }
  S.jobSince = j.line_count || 0;
  S.jobLines = S.jobLines.concat(j.lines || []).slice(-400);
  const was = S.job && S.job.status;
  S.job = j;
  renderJob();
  if (j.status === 'running') {
    tick += 1;
    if (S.page === 'footage' && j.project === S.pid && tick % 3 === 0) {
      S.p = await api(P()).catch(() => S.p);
      if (tick % 6 === 0) { const sh = await api(`${P()}/shots`).catch(() => null); if (sh) { S.shots = sh.shots; S.byId = Object.fromEntries(S.shots.map((s) => [s.id, s])); } }
      renderFootageLive();
    }
    pollTimer = setTimeout(pollJob, 900);
    return;
  }
  if (was === 'running') {
    const then = S.after; S.after = null;
    await loadProjects();
    if (j.project && j.project !== S.pid && S.projects.some((x) => x.id === j.project)) { S.pid = j.project; store('project', j.project); S.outlineDirty = false; S.tlDirty = false; }
    await loadProject();
    render();
    if (j.status === 'done' && then) then();
  }
}

/* ---------------------------------------------------------------- shell */
function renderHeader() {
  $('#projectSelect').innerHTML = (S.projects.length ? S.projects.map((p) => `<option value="${esc(p.id)}" ${p.id === S.pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('') : '<option value="">还没有项目</option>')
    + '<option value="__new">＋ 新建项目…</option>';
  const p = S.p || {};
  const steps = [['footage', '素材', p.logged > 0], ['story', '故事线', !!p.has_outline], ['timeline', '时间线', !!p.has_story], ['export', '导出', !!p.has_xml]];
  $('#steps').innerHTML = steps.map(([id, name, done], i) => `<a href="#${id}" ${S.page === id ? 'aria-current="page"' : ''}>
    ${done && S.page !== id ? '<span class="done">✓</span>' : `<span class="n">${i + 1}</span>`}${name}</a>`).join('');
  $('#mockToggle').checked = mockOn();
  if (S.page === 'settings') $('#gearBtn').setAttribute('aria-current', 'page'); else $('#gearBtn').removeAttribute('aria-current');
  if (S.page === 'logs') $('#logsBtn').setAttribute('aria-current', 'page'); else $('#logsBtn').removeAttribute('aria-current');
  renderLogBadge();
  const k = $('#keyState');
  k.textContent = S.env.has_key ? 'API Key 已就绪' : '未设置 API Key';
  k.className = 'pill' + (S.env.has_key ? ' ok' : '');
  const kh = S.env.key || {};
  k.title = S.env.has_key ? `studio 正在用的 Key：${kh.hint}（${kh.length} 位，来自 ${kh.source}）。点这里到设置页修改。`
    : '还没有 API Key，只能用离线模拟。点这里到设置页填写。';
  if (S.env.has_key) k.textContent = `Key ${kh.hint}`;
}

function renderJob() {
  const j = S.job, p = $('#jobPanel');
  if (!j || j.status === 'idle' || !j.name || S.jobHidden) { p.hidden = true; return; }
  p.hidden = false;
  p.className = 'job' + (j.status === 'error' ? ' fail' : j.status === 'done' ? ' done' : '');
  $('#jobName').textContent = j.status === 'running' ? j.name + '…' : j.status === 'error' ? `${j.name}失败` : `${j.name}完成`;
  const last = S.jobLines.filter((l) => !l.startsWith('Traceback')).slice(-1)[0] || '';
  $('#jobLast').textContent = j.status === 'error' ? j.error : (j.total ? `${j.done}/${j.total} · ` : '') + last.replace('[vlogai] ', '');
  const bar = $('#jobBar'), fill = $('#jobFill');
  bar.hidden = j.status !== 'running';
  bar.classList.toggle('indet', j.pct == null);
  fill.style.width = (j.pct || 0) + '%';
  const log = $('#jobLog');
  log.hidden = !(S.jobOpen || j.status === 'error');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  log.textContent = S.jobLines.join('\n');
  if (atBottom) log.scrollTop = log.scrollHeight;
  $('#jobToggle').textContent = log.hidden ? '日志' : '收起';
  if (j.status !== 'running') refreshLogSummary();
  if (j.status === 'done' && !S.jobOpen) { clearTimeout(renderJob.t); renderJob.t = setTimeout(() => { if (S.job && S.job.status === 'done' && !S.jobOpen) p.hidden = true; }, 4000); }
}

function render() {
  const page = (location.hash || '#footage').slice(1);
  S.page = ['footage', 'story', 'timeline', 'export', 'settings', 'logs'].includes(page) ? page : 'footage';
  document.body.classList.toggle('on-settings', S.page === 'settings');
  renderHeader();
  const active = document.activeElement;
  const keep = active && active.id ? { id: active.id, a: active.selectionStart, b: active.selectionEnd } : null;
  $('#app').innerHTML = S.page === 'settings' ? viewSettings() : S.page === 'logs' ? viewLogs() : !S.pid ? viewWelcome() : { footage: viewFootage, story: viewStory, timeline: viewTimeline, export: viewExport }[S.page]();
  if (S.pid && S.page === 'timeline') { renderTracks(); renderEditor(); renderSavebar(); }
  if (S.page === 'logs') { renderLogRows(true); if (!S.logs) refreshLogs(); }
  if (keep) {
    const el = document.getElementById(keep.id);
    if (el) { el.focus(); try { if (keep.a != null) el.setSelectionRange(keep.a, keep.b); } catch (e) { /* not text */ } }
  }
}

function viewWelcome() {
  return `<div class="page"><div class="content"><div class="empty"><h2>把一堆素材，变成一个有故事的片子</h2>
    <p style="margin:0;max-width:480px;line-height:1.7">选一个放着这趟旅行视频的文件夹（子文件夹也行）。AI 会逐个镜头看一遍，你写下想讲的故事，它用真实拍到的素材搭出结构，再剪成一条可以继续编辑的时间线。</p>
    <button type="button" class="btn" data-act="new-project" ${dis()}>选择素材文件夹</button></div></div></div>`;
}

/* ---------------------------------------------------------------- 1 素材 */
function shotDays() {
  const days = {};
  for (const s of S.shots) (days[s.day] = days[s.day] || []).push(s);
  return Object.keys(days).map(Number).sort((a, b) => a - b).map((d) => ({ day: d, shots: days[d], date: (days[d][0].created || '').slice(5, 10).replace('-', '/') }));
}
const badQ = () => +((S.cfg && S.cfg.settings.bad_quality) ?? 0.25);
function shotMatches(s) {
  const f = S.filters;
  if (f.star && !((s.highlight || 0) >= 7)) return false;
  if (f.talk && !s.speech) return false;
  if (f.vert && !s.vertical) return false;
  if (f.hideBlur && s.quality != null && s.quality < badQ()) return false;
  if (f.q) {
    const hay = [s.summary, s.place, s.activity, s.mood, s.speech, (s.tags || []).join(' '), s.file, s.id].join(' ').toLowerCase();
    if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

function progressHtml() {
  const p = S.p, j = S.job || {};
  const mine = jobRunning() && j.project === S.pid;
  const scanning = mine && j.name === '扫描素材', analyzing = mine && j.name === '看素材';
  const pct = p.shots ? Math.round((100 * p.logged) / p.shots) : 0;
  const describing = analyzing && S.jobLines.some((l) => l.includes('AI 看素材'));
  const stage = scanning ? 0 : analyzing ? (describing ? 3 : 1) : null;
  const row = (i, name, ok, val) => {
    const on = stage === i;
    return `<div class="prog ${on ? 'on' : ''}"><span class="dot ${ok && !on ? 'ok' : on ? 'on' : ''}">${ok && !on ? '✓' : ''}</span>
      <span class="name">${name}</span><span class="v">${val}</span></div>`;
  };
  const left = p.shots - p.logged;
  let btn;
  if (scanning || analyzing) btn = `<button type="button" class="btn" disabled>${scanning ? '扫描中…' : '看片中…'}</button>`;
  else if (!p.shots || left > 0) btn = `<button type="button" class="btn" data-act="analyze" ${dis()}>${!p.shots ? '开始分析' : `继续看片（还剩 ${left} 个镜头）`}</button>`;
  else btn = `<button type="button" class="ghost" data-act="analyze" ${dis()}>重新分析（只补新加的镜头）</button>`;
  return `<div class="label">处理进度</div>
    ${row(0, '扫描元数据', p.clips > 0, p.clips || '–')}
    ${row(1, '切镜头 · 抽帧 · 打分', p.shots > 0, p.shots ? `${p.shots}${p.unusable ? ` · ${p.unusable} 废片` : ''}` : '–')}
    <div class="section" style="gap:8px">${row(3, 'AI 看片', p.shots > 0 && p.logged >= p.shots, p.shots ? `${p.logged} / ${p.shots}` : '–')}
      <div class="bar" style="margin-left:32px"><i style="width:${pct}%"></i></div></div>
    <label class="check" style="min-height:32px;font-size:13px"><input type="checkbox" id="fWhisper" ${S.whisper ? 'checked' : ''}>同时转写人声（需要 faster-whisper）</label>
    ${btn}
    ${mockOn() ? '<p class="note" style="color:var(--accent)">现在是离线模拟：AI 日志是假的，只用来测试流程。</p>' : ''}
    <p class="note">原片始终留在本机，只上传 512px 静帧和文字。中途停了也没关系，已完成的部分不会重复计费。</p>`;
}

function cardHtml(s) {
  return `<button type="button" class="card ${s.id === S.fsel ? 'sel' : ''} ${s.excluded ? 'excluded' : ''}" data-shot="${esc(s.id)}" aria-label="${esc(s.summary || s.file)}">
    <div class="thumb ${s.vertical ? 'contain' : ''}">${s.thumb ? `<img loading="lazy" src="${s.thumb}" alt="">` : ''}
      ${(s.highlight || 0) >= 8 ? '<span class="tag star">★ 精彩</span>' : ''}
      ${s.must ? '<span class="tag must">必用</span>' : s.excluded ? '<span class="tag must" style="color:var(--danger)">已排除</span>' : ''}
      <span class="tag dur">${mmss(s.duration)}</span></div>
    <div class="body"><div class="t">${esc(s.summary || 'AI 还没看过这个镜头')}</div>
      <div class="m">${esc([shotType(s.shot_type), s.mood, s.file].filter(Boolean).join(' · '))}</div></div></button>`;
}

function gridHtml() {
  if (!S.shots.length) {
    return `<div class="empty"><h2>${S.p.clips ? `找到 ${S.p.clips} 条素材` : '还没有镜头'}</h2>
      <p style="margin:0;max-width:440px;line-height:1.7">点左边的“开始分析”：切镜头、抽关键帧、打质量分，然后 AI 逐个镜头写日志——内容、情绪、景别和最佳片段。</p></div>`;
  }
  const days = shotDays().filter((d) => S.day === 'all' || String(d.day) === String(S.day));
  let shown = 0;
  const html = days.map((d) => {
    const list = d.shots.filter(shotMatches);
    shown += list.length;
    if (!list.length) return '';
    return `<section class="section"><div class="dayhead"><h2>第 ${d.day} 天</h2><span class="muted">${d.date} · ${list.length} 个镜头</span></div>
      <div class="grid">${list.map(cardHtml).join('')}</div></section>`;
  }).join('');
  return shown ? html : '<div class="empty">没有符合筛选条件的镜头</div>';
}

function detailHtml() {
  const s = S.byId[S.fsel];
  if (!s) return '<div class="label">镜头日志</div><p class="muted" style="margin:0">点一个镜头，看 AI 写的日志。</p>';
  const frames = s.frames || [];
  const fi = S.fframe ?? Math.floor(frames.length / 2);
  const bm = s.best_moment;
  const best = bm ? `${(+bm.start).toFixed(1)}–${(+bm.end).toFixed(1)} 秒` : '–';
  const gps = s.gps ? `${(+s.gps[0]).toFixed(3)}, ${(+s.gps[1]).toFixed(3)}` : '–';
  return `<div class="label">镜头日志</div>
    <div class="thumb big ${s.vertical ? 'contain' : ''}">${frames.length ? `<img src="${frames[fi]}" alt="${esc(s.summary)}">` : ''}
      ${bm ? `<span class="tag dur" style="left:10px;right:auto">最佳片段 ${best}</span>` : ''}</div>
    ${frames.length > 1 ? `<div class="strip">${frames.map((u, i) => `<button type="button" data-frame="${i}" aria-pressed="${i === fi}" aria-label="第 ${i + 1} 帧"><img src="${u}" alt=""></button>`).join('')}</div>` : ''}
    <div><div class="mono muted" style="font-size:12px">${esc(s.id)} · ${esc(s.file)}</div>
      <h3 style="font-size:19px;margin-top:4px;line-height:1.45">${esc(s.summary || 'AI 还没看过这个镜头')}</h3></div>
    <dl class="kv">
      <dt>景别</dt><dd>${esc(shotType(s.shot_type) || '–')}</dd>
      <dt>情绪</dt><dd>${esc(s.mood || '–')}</dd>
      <dt>精彩度</dt><dd>${s.highlight != null ? esc(s.highlight) + ' / 10' : '–'}</dd>
      <dt>地点</dt><dd>${esc(s.place || '–')}</dd>
      <dt>画质</dt><dd>${s.quality != null ? `${(+s.quality).toFixed(2)}${s.quality < badQ() ? '（偏模糊或过暗）' : ''}` : '–'}</dd>
      <dt>拍摄</dt><dd>第 ${s.day} 天 ${esc(s.time)} · ${mmss(s.duration)}</dd>
      <dt>GPS</dt><dd>${gps}</dd>
      <dt>对白</dt><dd>${esc(s.speech || '无')}</dd>
      <dt>标签</dt><dd>${esc((s.tags || []).join('、') || '–')}</dd>
    </dl>
    <div class="toolbar" style="margin-top:auto">
      <button type="button" class="ghost ${s.must ? 'outline' : ''}" style="flex:1" data-flag="must" aria-pressed="${s.must}">${s.must ? '✓ 必用' : '标为必用'}</button>
      <button type="button" class="ghost ${s.excluded ? 'danger' : ''}" style="flex:1" data-flag="exclude" aria-pressed="${s.excluded}">${s.excluded ? '已排除（点击恢复）' : '排除这个镜头'}</button>
    </div>`;
}

function dayNavHtml() {
  const items = [{ k: 'all', n: '全部', c: S.shots.length }].concat(shotDays().map((d) => ({ k: String(d.day), n: `第 ${d.day} 天 · ${d.date}`, c: d.shots.length })));
  return items.map((i) => `<button type="button" data-day="${i.k}" aria-pressed="${String(S.day) === i.k}"><span>${esc(i.n)}</span><span class="c">${i.c}</span></button>`).join('');
}

function viewFootage() {
  const p = S.p, f = S.filters;
  const chip = (k, label) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${f[k]}">${label}</button>`;
  return `<div class="page">
    <aside class="side">
      <section class="section">
        <div class="label">素材文件夹</div>
        <div class="path">${esc(p.footage || '–')}</div>
        <button type="button" class="ghost" data-act="rescan" ${dis()}>更换文件夹 / 重新扫描</button>
        <div class="stats" style="margin-top:4px"><div><b>${p.clips}</b><span>文件</span></div><div><b>${p.shots}</b><span>镜头</span></div><div><b>${mmss(p.duration_min * 60)}</b><span>总时长</span></div></div>
      </section>
      <section class="section" id="fProgress">${progressHtml()}</section>
      ${S.shots.length ? `<section class="section"><div class="label">按天浏览</div><div class="daynav" id="fDays">${dayNavHtml()}</div></section>` : ''}
    </aside>
    <div class="content">
      ${S.shots.length ? `<div class="toolbar">
        <label for="fSearch" class="sr">搜索镜头日志</label>
        <input id="fSearch" class="field" type="search" placeholder="搜索镜头日志：灯塔、退潮、车里聊天…" value="${esc(f.q)}">
        ${chip('star', '★ 精彩')}${chip('talk', '有对白')}${chip('vert', '竖屏')}${chip('hideBlur', '隐藏废片')}</div>` : ''}
      <div id="fGrid" class="section" style="gap:22px">${gridHtml()}</div>
    </div>
    ${S.shots.length ? `<aside class="detail" id="fDetail">${detailHtml()}</aside>` : ''}
  </div>`;
}

function renderFootageLive() {
  if (S.page !== 'footage' || !S.pid) return;
  const pr = $('#fProgress');
  if (!pr || (!S.shots.length) !== !$('#fDetail')) { render(); return; }
  pr.innerHTML = progressHtml();
  if (!(document.activeElement && document.activeElement.id === 'fSearch')) {
    $('#fGrid').innerHTML = gridHtml();
    const dn = $('#fDays'); if (dn) dn.innerHTML = dayNavHtml();
  }
  renderHeader();
}

function selectShot(id) {
  S.fsel = id; S.fframe = null;
  document.querySelectorAll('.card.sel').forEach((el) => el.classList.remove('sel'));
  const el = document.querySelector(`.card[data-shot="${CSS.escape(id)}"]`);
  if (el) el.classList.add('sel');
  const d = $('#fDetail'); if (d) d.innerHTML = detailHtml();
}

/* ---------------------------------------------------------------- 2 故事线 */
function outlineChanged() {
  S.outlineDirty = true;
  store('outline.' + S.pid, { base: S.outlineServer, ol: S.outline });
  const sum = $('#oSummary'); if (sum) sum.textContent = outlineSummary();
}
function outlineSummary() {
  const b = S.outline ? S.outline.beats : [];
  return `${b.length} 幕 · 约 ${mmss(b.reduce((a, x) => a + (+x.seconds || 0), 0))} · 可以拖动排序，直接改文字`;
}

function beatHtml(b, i) {
  const ids = (b.shot_ids || []).filter((id) => S.byId[id]).slice(0, 8);
  return `<li class="beat" draggable="true" data-beat="${i}">
    <div class="lead"><span class="handle" aria-hidden="true" title="拖动排序"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg></span>
      <span class="num">${String(i + 1).padStart(2, '0')}</span></div>
    <div class="main">
      <div class="row"><input class="title" data-bf="title" value="${esc(b.title)}" aria-label="这一幕的标题">
        <label class="secs">约<input type="number" min="1" step="1" data-bf="seconds" value="${esc(Math.round(b.seconds || 0))}" aria-label="秒数">秒 · ${(b.shot_ids || []).length} 个候选镜头</label></div>
      <textarea data-bf="intent" rows="2" aria-label="这一幕要做什么">${esc(b.intent || '')}</textarea>
      ${b.evidence ? `<div class="ev">素材依据：${esc(b.evidence)}</div>` : ''}
      ${ids.length ? `<div class="minis">${ids.map((id) => `<img loading="lazy" src="${thumbOf(id)}" alt="" title="${esc(S.byId[id].summary || id)}">`).join('')}</div>` : ''}
    </div>
    <div class="ops"><button type="button" class="ghost sm" data-bmove="-1" aria-label="上移" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button type="button" class="ghost sm" data-bmove="1" aria-label="下移" ${i === S.outline.beats.length - 1 ? 'disabled' : ''}>↓</button>
      <button type="button" class="ghost sm danger" data-bdel aria-label="删除这一幕">删除</button></div>
  </li>`;
}

function viewStory() {
  const s = S.p.settings || {};
  const minutes = +(s.minutes || 3);
  const lens = [1, 2, 4, 8];
  const custom = !lens.includes(minutes);
  const ready = S.p.logged > 0;
  const ol = S.outline;
  const missing = (ol && ol.missing) || [];
  return `<div class="page">
    <aside class="side wide">
      <div><h1 style="font-size:24px;margin-bottom:6px">你想讲一个什么故事？</h1>
        <p class="muted" style="margin:0;line-height:1.6">跟谁去的、这趟旅行意味着什么、想要什么感觉、结尾落在哪。用自己的话写就行。</p></div>
      <label for="sBrief" class="sr">故事描述</label>
      <textarea id="sBrief" class="field" rows="8" placeholder="比如：和 James 从多伦多一路开到大西洋边，前半段热闹，越往东越安静，结尾停在傍晚的灯塔。">${esc(s.brief || '')}</textarea>
      <div class="section"><div class="label">成片长度</div>
        <div class="seg" style="grid-template-columns:repeat(4,minmax(0,1fr)) minmax(0,1.4fr)" role="group" aria-label="成片长度">
          ${lens.map((m) => `<button type="button" data-min="${m}" aria-pressed="${minutes === m}">${m} 分</button>`).join('')}
          <input id="sMinCustom" class="field mono" style="min-height:44px;padding:0 8px;${custom ? 'border-color:var(--accent)' : ''}" type="number" min="0.5" max="120" step="0.5" value="${custom ? esc(minutes) : ''}" placeholder="自定" aria-label="自定义分钟数">
        </div></div>
      <div class="section"><div class="label">画幅</div>
        <div class="seg" style="grid-template-columns:repeat(2,minmax(0,1fr))">
          <button type="button" data-vert="0" aria-pressed="${!s.vertical}">横屏 16:9</button>
          <button type="button" data-vert="1" aria-pressed="${!!s.vertical}">竖屏 9:16</button></div></div>
      <div class="section"><div class="label">背景音乐（渲染时自动卡点，对白时自动压低）</div>
        <div class="path" style="display:flex;align-items:center;gap:8px">${s.music ? `<span style="flex:1">${esc(s.music.split(/[\\/]/).pop())}</span>` : '<span style="flex:1" class="muted">不加音乐</span>'}
          ${s.music ? '<button type="button" class="ghost sm" data-act="clear-music">移除</button>' : ''}
          <button type="button" class="ghost sm" data-act="pick-music">${s.music ? '更换' : '选择'}</button></div></div>
      <button type="button" class="btn" style="min-height:48px;font-size:15px" data-act="outline" ${!ready || jobRunning() ? 'disabled' : ''}>${ol ? '重新生成故事线' : '让 AI 提出分幕'}</button>
      ${!ready ? '<p class="note">需要先在“素材”里完成 AI 看片。</p>' : ''}
    </aside>
    <div class="content">
      ${ol ? `<div class="toolbar" style="gap:12px">
          <div style="flex:1 1 240px"><h2 style="font-size:22px">${esc(ol.title || '故事线')}</h2>
            <div class="muted" style="margin-top:2px">${esc(ol.logline || '')}</div>
            <div class="muted mono" style="font-size:12px;margin-top:4px" id="oSummary">${outlineSummary()}</div></div>
          <button type="button" class="ghost" data-act="add-beat">+ 加一幕</button>
          <button type="button" class="btn" data-act="make-story" ${!ol.beats.length || jobRunning() ? 'disabled' : ''}>按这个结构剪 →</button></div>
        <ol class="beats" id="beats">${ol.beats.map(beatHtml).join('')}</ol>`
      : `<div class="empty"><h2>故事线会出现在这里</h2><p style="margin:0;max-width:440px;line-height:1.7">在左边写下你想要的故事，AI 会对照你真实拍到的素材，给出一组可以编辑、排序、删除的分幕。</p></div>`}
    </div>
    <aside class="detail">
      <div><h2 style="font-size:18px">素材撑不起的部分</h2>
        <p class="muted" style="margin:6px 0 0;line-height:1.6">AI 对照了你写的内容和实际拍到的东西，这些地方需要你决定。</p></div>
      ${missing.length ? missing.map((m) => `<div class="gap">${esc(m)}</div>`).join('') : `<p class="muted" style="margin:0">${ol ? '没有发现明显的缺口。' : '生成故事线后显示。'}</p>`}
      ${ol && ol.notes ? `<div class="gap" style="background:var(--card);border-color:var(--line)"><b style="color:var(--text)">剪辑建议</b>${esc(ol.notes)}</div>` : ''}
      ${S.p.flags && (S.p.flags.must || S.p.flags.exclude) ? `<p class="note">已标记：${S.p.flags.must} 个必用镜头，${S.p.flags.exclude} 个排除镜头（排除的 AI 不会用）。</p>` : ''}
      <p class="note" style="margin-top:auto;border-top:1px solid var(--line);padding-top:14px;font-size:13px">提示：录一段 5 分钟的语音回忆放进素材文件夹，开启转写后，AI 能找到更清楚的结构。</p>
    </aside>
  </div>`;
}

/* ---------------------------------------------------------------- 3 时间线 */
function tlStarts() {
  let acc = 0;
  return (S.tl || []).map((t) => { const s = acc; acc += t.out - t.in; return s; });
}
function previewStale() {
  const m = S.p.mtimes || {};
  return S.p.has_preview && (S.tlDirty || m['story.json'] > m['preview.mp4'] + 1);
}

function chatHtml() {
  const vs = [...S.versions].reverse();
  const msgs = vs.map((v) => {
    const res = `v${v.n}：${v.shots} 个镜头，${mmss(v.seconds)}`;
    if (v.note.startsWith('revise: ')) return `<div class="msg me">${esc(v.note.slice(8))}</div><div class="msg ai">改好了 → ${res}</div>`;
    if (v.note.startsWith('initial') || v.note.startsWith('from approved')) return `<div class="msg ai">按故事线剪了一版 → ${res}</div>`;
    return `<div class="msg ai">${esc(v.note)} → ${res}</div>`;
  }).join('');
  const cur = S.versions[0];
  return `<div class="toolbar" style="justify-content:space-between">
      <h2 style="font-size:18px">用一句话改</h2>
      ${S.versions.length ? `<label class="muted" style="display:flex;align-items:center;gap:6px;font-size:13px">版本
        <select id="tVersion" style="min-height:34px;background:var(--raise);border:1px solid var(--line2);border-radius:6px;padding:0 6px">
          ${S.versions.map((v) => `<option value="${v.n}">v${v.n}${v === cur ? ' · 当前' : ''}</option>`).join('')}</select></label>` : ''}</div>
    <div class="msgs" id="tMsgs">${msgs || '<div class="muted">还没有修改记录。</div>'}</div>
    ${mockOn() ? '<p class="note" style="color:var(--accent)">离线模拟听不懂修改要求，只会重新校验。</p>' : ''}
    <div class="toolbar" style="gap:6px">
      ${['开头再有力一点', '少一些空镜', '第二段节奏再快点', '去掉重复的镜头'].map((x) => `<button type="button" class="chip" style="min-height:34px;font-size:13px" data-suggest="${esc(x)}">${esc(x)}</button>`).join('')}</div>
    <form id="tAsk"><label for="tAskInput" class="sr">修改要求</label>
      <input id="tAskInput" class="field" placeholder="比如：结尾停在灯塔，不要回程" autocomplete="off">
      <button class="btn" ${dis()}>发送</button></form>`;
}

function viewTimeline() {
  if (!S.tl) {
    return `<div class="page"><div class="content"><div class="empty"><h2>还没有时间线</h2>
      <p style="margin:0">先在“故事线”里确认分幕，再按结构剪。</p><a class="btn" href="#story">去写故事线</a></div></div></div>`;
  }
  const total = S.tl.reduce((a, t) => a + (t.out - t.in), 0);
  const m = S.p.mtimes || {};
  return `<div class="tl-top">
    <section class="player">
      <div class="screen" id="tScreen">${S.p.has_preview ? `<video id="tVideo" controls preload="metadata" src="${media('preview.mp4', m['preview.mp4'])}"></video>`
        : `<div class="ph"><p style="margin:0 0 12px">还没有渲染预览</p><button type="button" class="btn" data-act="render" ${dis()}>渲染预览</button></div>`}</div>
      <div class="toolbar">
        <span class="mono">${S.tl.length} 个镜头 · ${mmss(total)}</span>
        <span class="muted" style="flex:1">${previewStale() ? '预览是上一次渲染的，和当前时间线不一致' : S.p.has_preview ? '点时间线上的镜头会跳到对应位置' : ''}</span>
        ${S.p.has_preview ? `<button type="button" class="ghost sm" data-act="render" ${dis()}>重新渲染预览</button>` : ''}
      </div>
    </section>
    <aside class="chat" id="tChat">${chatHtml()}</aside>
  </div>
  <div id="tSave"></div>
  <section class="tracks" aria-label="时间线"><div class="tracks-in" id="tTracks"></div></section>
  <section class="editor" id="tEditor"></section>`;
}

function chapters() {
  const out = [];
  S.tl.forEach((t, i) => {
    const last = out[out.length - 1];
    if (last && last.name === (t.chapter || '')) last.items.push(i);
    else out.push({ name: t.chapter || '', items: [i] });
  });
  return out;
}
function renderTracks() {
  const el = $('#tTracks'); if (!el) return;
  const ch = chapters();
  const dur = (i) => Math.max(0.3, S.tl[i].out - S.tl[i].in);
  const chDur = (c) => c.items.reduce((a, i) => a + dur(i), 0);
  const music = (S.p.settings || {}).music;
  el.innerHTML = `<div></div>
    <div class="lane">${ch.map((c, k) => `<div class="chap" style="flex:${chDur(c)} 1 0" title="${esc(c.name)}"><b>${String(k + 1).padStart(2, '0')}</b>${esc(c.name || '（无章节）')}</div>`).join('')}</div>
    <div class="lab">V1</div>
    <div class="lane">${ch.map((c) => `<div class="chapgroup" style="flex:${chDur(c)} 1 0">${c.items.map((i) => {
      const t = S.tl[i], s = S.byId[t.shot_id] || {};
      const bg = s.thumb ? `background-image:url('${s.thumb}')` : '';
      return `<button type="button" class="clip ${i === S.tsel ? 'sel' : ''}" draggable="true" data-clip="${i}" style="flex:${dur(i)} 1 0;${bg}"
        aria-label="${esc((s.summary || t.shot_id) + ' ' + dur(i).toFixed(1) + ' 秒')}">${t.text ? `<span class="cap">${esc(t.text)}</span>` : ''}</button>`;
    }).join('')}</div>`).join('')}</div>
    <div class="lab">A1</div>
    <div class="lane">${ch.map((c) => `<div class="chapgroup" style="flex:${chDur(c)} 1 0">${c.items.map((i) => `<span class="aud ${S.tl[i].keep_audio ? 'on' : ''}" style="flex:${dur(i)} 1 0" title="${S.tl[i].keep_audio ? '保留原声' : '原声静音'}"></span>`).join('')}</div>`).join('')}</div>
    <div class="lab">A2</div>
    <div class="music ${music ? '' : 'none'}">${music ? '♪ ' + esc(music.split(/[\\/]/).pop()) : '没有背景音乐（在“故事线”里选择）'}</div>`;
}

function renderEditor() {
  const el = $('#tEditor'); if (!el) return;
  const t = S.tl[S.tsel];
  if (!t) { el.innerHTML = '<span class="muted">时间线是空的</span>'; return; }
  const s = S.byId[t.shot_id] || { duration: t.out, has_audio: false };
  el.innerHTML = `<div class="who"><div class="mono muted" style="font-size:12px">#${S.tsel + 1} · ${esc(t.shot_id)} · ${esc(t.role || '')} · 镜头全长 ${(+s.duration).toFixed(1)} 秒</div>
      <div style="font:600 16px var(--serif);margin-top:4px">${esc(s.summary || t.shot_id)}</div>
      ${t.why ? `<div class="muted" style="font-size:13px;margin-top:4px">剪辑理由：${esc(t.why)}</div>` : ''}</div>
    <label class="f num">入点（秒）<input id="eIn" type="number" step="0.1" min="0" max="${s.duration}" value="${t.in}"></label>
    <label class="f num">出点（秒）<input id="eOut" type="number" step="0.1" min="0" max="${s.duration}" value="${t.out}"></label>
    <label class="f grow">字幕<input id="eText" value="${esc(t.text)}" placeholder="可留空"></label>
    <label class="f">章节<input id="eChap" value="${esc(t.chapter)}" style="width:150px"></label>
    <label class="check"><input id="eAudio" type="checkbox" ${t.keep_audio ? 'checked' : ''} ${s.has_audio ? '' : 'disabled'}>保留原声</label>
    <div class="toolbar" style="gap:6px">
      <button type="button" class="ghost sm" data-move="-1" ${S.tsel === 0 ? 'disabled' : ''}>← 前移</button>
      <button type="button" class="ghost sm" data-move="1" ${S.tsel === S.tl.length - 1 ? 'disabled' : ''}>后移 →</button>
      <button type="button" class="ghost sm danger" data-act="del-clip">删除镜头</button></div>`;
}

function renderSavebar() {
  const el = $('#tSave'); if (!el) return;
  el.innerHTML = S.tlDirty ? `<div class="savebar" style="margin-top:16px"><span>有未保存的修改</span>
    <button type="button" class="ghost sm" data-act="discard">放弃</button>
    <button type="button" class="ghost sm outline" data-act="save-tl" ${dis()}>保存</button>
    <button type="button" class="btn sm" data-act="save-render" ${dis()}>保存并重新渲染</button></div>` : '';
}
function tlChanged(full) { S.tlDirty = true; renderTracks(); renderSavebar(); if (full) renderEditor(); }
function selectClip(i, seek) {
  S.tsel = i; renderTracks(); renderEditor();
  const v = $('#tVideo');
  if (seek && v && !previewStale()) v.currentTime = tlStarts()[i] + 0.05;
}
async function saveTimeline(thenRender) {
  const r = await act(() => api(`${P()}/timeline`, { timeline: S.tl }));
  if (!r) return;
  S.tlDirty = false;
  await loadProject();
  if (thenRender) await startRender();
  else { render(); toast('已保存为新版本'); }
}
function startRender(extra = {}) {
  const s = S.p.settings || {};
  return startJob(`${P()}/render`, { music: s.music || '', vertical: !!s.vertical, hd: S.hd, ...extra });
}

/* ---------------------------------------------------------------- 4 导出 */
function viewExport() {
  const p = S.p, st = S.story;
  if (!st) return `<div class="page"><div class="content"><div class="empty"><h2>还没有可以导出的时间线</h2><a class="btn" href="#story">去写故事线</a></div></div></div>`;
  const m = p.mtimes || {}, s = p.settings || {};
  const cur = S.versions[0];
  const when = (k) => (m[k] ? `<span class="muted mono" style="font-size:12px">上次生成 ${new Date(m[k] * 1000).toLocaleString()}</span>` : '');
  const icon = (d) => `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">${d}</svg>`;
  return `<div class="ex-wrap"><main class="ex-main">
    <div><h1 style="font-size:26px">把这一版带走</h1>
      <p class="muted" style="margin:6px 0 0">${cur ? `v${cur.n} · ` : ''}${esc(st.title || '')} · ${new Set(st.timeline.map((t) => t.chapter)).size} 幕 · ${st.timeline.length} 个镜头 · ${mmss(st.total_seconds)}</p></div>
    <div class="ex-grid">
      <section class="ex-card"><div class="hd">${icon('<polygon points="6 4 20 12 6 20 6 4"/>')}<h2>MP4 预览</h2></div>
        <p>带字幕、卡节拍、对白时音乐自动压低。硬切，不调色，适合快速看效果。长片建议跳过，直接导时间线去 Resolve 精修。</p>
        <div class="seg" style="grid-template-columns:repeat(2,minmax(0,1fr))" role="group" aria-label="清晰度">
          <button type="button" data-hd="0" aria-pressed="${!S.hd}">${s.vertical ? '竖屏 1080×1920' : '720p'}</button>
          <button type="button" data-hd="1" aria-pressed="${S.hd}" ${s.vertical ? 'disabled' : ''}>1080p</button></div>
        <p style="font-size:13px">音乐：${s.music ? esc(s.music.split(/[\\/]/).pop()) : '无（在“故事线”里选择）'}</p>
        ${when('preview.mp4')}
        <div class="foot">${p.has_preview ? `<a class="ghost" href="${media('preview.mp4', m['preview.mp4'])}" target="_blank" rel="noopener">打开 preview.mp4</a>` : ''}
          <button type="button" class="btn" data-act="render" ${dis()}>渲染 preview.mp4</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>')}<h2>Resolve / Premiere 时间线</h2></div>
        <p>导出 timeline.xml，直接关联你的原片，接着在专业软件里精剪、调色、加转场。</p>
        <ul class="tracklist"><li><b>V1</b>画面</li><li><b>A1</b>原声（没保留的部分是静音，可以随时打开）</li><li><b>A2</b>音乐</li><li><b>◆</b>每幕的名字和 AI 的选片理由作为标记</li></ul>
        <div class="howto">Resolve：File → Import → Timeline<br>Premiere：File → Import<br>Final Cut Pro 暂不支持</div>
        ${when('timeline.xml')}
        <div class="foot">${p.has_xml ? `<a class="ghost" href="${media('timeline.xml', m['timeline.xml'])}" download="timeline.xml">下载</a>` : ''}
          <button type="button" class="ghost outline" data-act="export-xml" ${dis()}>导出 timeline.xml</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<path d="M8 4H6a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h2M16 4h2a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-2"/>')}<h2>剪辑清单 story.json</h2></div>
        <p>纯文本 JSON，可以直接手改入点、出点、字幕。改完点“重新校验”，故事板和 XML 会一起更新。</p>
        <p class="mono" style="font-size:12px;word-break:break-all">${esc(p.path || '')}</p>
        <div class="foot"><button type="button" class="ghost" data-act="open-folder">打开项目文件夹</button>
          <button type="button" class="ghost" data-act="recheck" ${dis()}>重新校验</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v5"/>')}<h2>分镜表</h2></div>
        <p>每个镜头的缩略图、选它的理由、它在成片里的位置。可以打印，也方便发给同行的人看。</p>
        <div class="foot">${p.has_storyboard ? `<a class="ghost" href="${media('storyboard.html')}" target="_blank" rel="noopener">打开 storyboard.html</a>` : '<span class="muted">生成时间线后会自动出现</span>'}</div></section>
    </div></main>
    <aside class="versions"><h2 style="font-size:18px">版本历史</h2><p class="muted" style="margin:0;font-size:13px">每一版都留着，随时可以回去。</p>
      <ol>${S.versions.map((v, k) => `<li class="ver ${k === 0 ? 'cur' : ''}"><div class="r"><b class="mono">v${v.n}</b><span>${mmss(v.seconds)} · ${esc((v.created || '').slice(5, 16).replace('T', ' '))}</span></div>
        <p>${esc(versionNote(v.note))}</p>
        ${k ? `<button type="button" class="ghost sm" data-restore="${v.n}" ${dis()}>恢复这一版</button>` : ''}</li>`).join('')}</ol></aside></div>`;
}
function versionNote(n) {
  if (n.startsWith('revise: ')) return `“${n.slice(8)}”`;
  if (n.startsWith('initial') || n.startsWith('from approved')) return '按故事线剪的版本';
  return n;
}

/* ---------------------------------------------------------------- 设置 */
const MODEL_CHOICES = ['claude-haiku-4-5-20251001', 'claude-haiku-5-5', 'claude-sonnet-5', 'claude-sonnet-5-5', 'claude-opus-5-5'];
const WHISPER = [['tiny', '最快'], ['base', '较快'], ['small', '推荐'], ['medium', '最准，较慢']];

async function loadSettings() {
  try {
    S.cfg = await api('/api/settings');
    S.cfgError = null;
    if (!S.cfgDirty) S.draft = { ...S.cfg.settings };
  } catch (e) {
    S.cfgError = /not found|404/i.test(e.message)
      ? '后端还是更新前的版本，没有设置接口。在运行 studio 的窗口按 Ctrl+C 停掉，重新运行 python -m vlogai.studio，再刷新这个页面。'
      : e.message;
  }
}
function cfgChanged() {
  const a = S.cfg.settings, d = S.draft;
  return Object.keys(d).filter((k) => String(d[k]) !== String(a[k])).length + (S.newKey ? 1 : 0);
}
function estimate() {
  const d = S.draft;
  const every = +d.frame_every_sec || 4, max = +d.max_frames_per_shot || 4, w = +d.frame_width || 512;
  const perImage = Math.round((w * w * 9 / 16) / 750);
  const f12 = Math.max(1, Math.min(max, Math.ceil(12 / every)));
  const avg = Math.max(1, Math.min(max, Math.ceil(8 / every)));
  const perShot = avg * perImage + 600;
  const fmt = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(n < 1e4 ? 1 : 0) + 'k' : String(n));
  return { perImage, f12, perShot: fmt(perShot), p500: fmt(perShot * 500) };
}
function seg(key, opts, cols) {
  return `<div class="seg" role="group" style="grid-template-columns:repeat(${cols || opts.length},minmax(0,1fr))">${opts.map(([v, label, sub]) =>
    `<button type="button" data-set="${key}" data-val="${esc(v)}" aria-pressed="${String(S.draft[key]) === String(v)}">${esc(label)}${sub ? `<span style="display:block;font-size:11px;color:var(--muted)">${esc(sub)}</span>` : ''}</button>`).join('')}</div>`;
}
function navSubs() {
  const d = S.draft;
  return { api: '', frames: `每 ${d.frame_every_sec} 秒`, whisper: d.whisper ? d.whisper_model : '关', render: d.render_hd ? '1080p' : '720p', advanced: '' };
}

function viewSettings() {
  if (!S.cfg) {
    if (!S.cfgError) { setTimeout(async () => { await loadSettings(); if (S.page === 'settings') render(); }, 0); }
    return `<div class="page"><div class="content">${S.cfgError
      ? `<div class="empty"><h2>读取设置失败</h2><p class="err" style="max-width:560px;line-height:1.7;margin:0">${esc(S.cfgError)}</p>
         <button type="button" class="btn" data-act="cfg-retry">重试</button></div>`
      : '<p class="muted">正在读取设置…</p>'}</div></div>`;
  }
  const d = S.draft, c = S.cfg, k = c.key || {}, e = estimate();
  const subs = navSubs();
  const nav = [['api', 'API Key 与模型'], ['frames', '抽帧与切镜头'], ['whisper', '语音转写'], ['render', '预览渲染'], ['advanced', '性能与高级']];
  const keyStatus = S.keyTest
    ? (S.keyTest === 'testing' ? '<span class="muted">正在测试…</span>'
      : S.keyTest.ok ? `<span class="ok-text">✓ 连接成功${S.keyTest.models && S.keyTest.models.length ? ` · 可用模型 ${S.keyTest.models.length} 个` : ''}</span>${S.keyTest.missing && S.keyTest.missing.length ? `<br><span class="bad-text">你选的模型不在可用列表里：${esc(S.keyTest.missing.join('、'))}</span>` : ''}`
        : `<span class="bad-text">✗ ${esc(S.keyTest.reason)}</span>`)
    : (k.has_key ? `<span class="muted">当前：${esc(k.hint)}（${k.length} 位，来自 ${esc(k.source)}）</span>` : '<span class="bad-text">还没有 API Key，只能用离线模拟</span>');
  const models = [...new Set(MODEL_CHOICES.concat((S.keyTest && S.keyTest.models) || []).concat([d.describe_model, d.story_model]))];
  const restart = (+d.port !== +c.settings.port || d.projects_dir !== c.settings.projects_dir);
  const n = cfgChanged();
  return `<div class="set-wrap">
    <aside class="set-nav">
      <h1 style="font-size:26px;margin:0 0 14px">设置</h1>
      ${nav.map(([id, label]) => `<button type="button" data-scroll="${id}"><span>${label}</span><span class="sub" id="nav-${id}">${esc(subs[id])}</span></button>`).join('')}
      <p class="help" style="margin-top:16px">这些设置对所有项目生效，保存在 <span class="mono">${esc(c.settings_file.split(/[\\/]/).pop())}</span>。API Key 存在 .env，不会被提交到 git。</p>
    </aside>
    <main class="set-main">

    <section class="set-card" id="sec-api">
      <div><h2>API Key 与模型</h2><p class="lead">AI 看片和编故事用的都是 Anthropic 的模型。</p></div>
      <div class="set-row">
        <label class="lbl" for="cfgKey">Anthropic API Key</label>
        <div class="toolbar">
          <input id="cfgKey" class="field mono" style="flex:1 1 320px;font-size:13px" type="${S.showKey ? 'text' : 'password'}" autocomplete="off" spellcheck="false"
            value="${esc(S.newKey || '')}" placeholder="${k.has_key ? esc(k.hint) + '（想换就粘贴新的）' : 'sk-ant-api03-…'}">
          <button type="button" class="ghost" data-act="toggle-key">${S.showKey ? '隐藏' : '显示'}</button>
          <button type="button" class="ghost outline" data-act="test-key">测试连接</button>
        </div>
        <div style="font-size:13px;line-height:1.6" id="keyStatus">${keyStatus}</div>
        <div class="help">保存到 <span class="mono">${esc(c.env_file)}</span>。在 <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a> 创建。保存后下一个任务就会用新的 Key，不用重启。</div>
      </div>
      <datalist id="modelList">${models.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgM1">看片模型</label>
          <input id="cfgM1" class="field mono" style="font-size:13px" list="modelList" data-cfg="describe_model" value="${esc(d.describe_model)}">
          <div class="help">每个镜头调用一次，镜头多时调用次数也多。选快一点、便宜一点的。</div></div>
        <div class="set-row"><label class="lbl" for="cfgM2">编故事模型</label>
          <input id="cfgM2" class="field mono" style="font-size:13px" list="modelList" data-cfg="story_model" value="${esc(d.story_model)}">
          <div class="help">分幕、剪时间线、一句话修改都用它。每次只调用一次，故事好不好主要看它。</div></div>
      </div>
      <div class="set-row"><div class="lbl">字幕和日志的语言</div><div style="max-width:340px">${seg('lang', [['zh', '中文'], ['en', 'English']])}</div></div>
    </section>

    <section class="set-card" id="sec-frames">
      <div><h2>抽帧与切镜头</h2><p class="lead">AI 看不到视频，只看从每个镜头里抽出的几张静帧。帧越多看得越细，花费也越多。</p></div>
      <div class="set-row">
        <div class="lbl"><label for="cfgEvery">抽帧间隔</label><span class="val" id="everyVal">每 ${d.frame_every_sec} 秒一帧</span></div>
        <input id="cfgEvery" type="range" min="1" max="10" step="1" data-cfg="frame_every_sec" value="${d.frame_every_sec}">
        <div class="help" style="display:flex;justify-content:space-between"><span>1 秒 · 看得最细</span><span>默认 4 秒</span><span>10 秒 · 最省</span></div>
      </div>
      <div class="set-grid">
        <div class="set-row"><div class="lbl">每个镜头最多几帧</div>${seg('max_frames_per_shot', [[1, '1'], [2, '2'], [3, '3'], [4, '4'], [6, '6']])}</div>
        <div class="set-row"><div class="lbl">静帧尺寸</div>${seg('frame_width', [[384, '384px'], [512, '512px'], [768, '768px']])}</div>
      </div>
      <div class="estimate" id="estimate">
        <div><span>一个 12 秒的镜头</span><b>${e.f12} 帧</b></div>
        <div><span>每个镜头约</span><b>${e.perShot} tokens</b></div>
        <div><span>500 个镜头约</span><b>${e.p500} tokens</b></div>
        <div class="help" style="grid-column:1/-1;color:var(--text2)">估算：每张静帧约 ${e.perImage} tokens，加上约 600 tokens 的提示和镜头信息。实际用量以每次任务结束时打印的数字为准。</div>
      </div>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgLong">长于多少秒才切分镜头</label>
          <div style="display:flex;align-items:center;gap:8px"><input id="cfgLong" class="field num" type="number" min="3" max="600" step="1" data-cfg="long_clip_sec" value="${d.long_clip_sec}"><span class="muted">秒</span></div>
          <div class="help">短片段整条当一个镜头；长片段按画面变化切开，比如一段边走边拍的长视频。</div></div>
        <div class="set-row"><div class="lbl">切镜头灵敏度</div>${seg('scene_sensitivity', [['low', '低'], ['mid', '中'], ['high', '高']])}
          <div class="help">高：画面稍有变化就切开，镜头更多更碎。需要装 PySceneDetect。</div></div>
      </div>
      <div class="set-row">
        <div class="lbl"><label for="cfgBad">废片判定</label><span class="val" id="badVal">画质分 &lt; ${(+d.bad_quality).toFixed(2)}</span></div>
        <input id="cfgBad" type="range" min="0" max="0.6" step="0.05" data-cfg="bad_quality" value="${d.bad_quality}">
        <div class="help">画质分低于这个值的镜头算“废片”：素材页可以隐藏，统计里单独列出。</div>
      </div>
      <div class="help" style="color:var(--accent);border-top:1px solid var(--line);padding-top:14px">改了这里的设置，只对还没处理过的素材生效。想让已有项目按新设置重新抽帧，要删掉那个项目文件夹里的 shots.json 和 frames 文件夹再分析。</div>
    </section>

    <section class="set-card" id="sec-whisper">
      <div class="toolbar" style="justify-content:space-between;align-items:flex-start">
        <div style="flex:1 1 300px"><h2>语音转写</h2><p class="lead">在本机把视频里说的话转成文字，AI 编故事时会参考。不上传音频。</p></div>
        <button type="button" class="switch" role="switch" aria-checked="${!!d.whisper}" aria-label="默认开启语音转写" data-toggle="whisper"><i></i></button>
      </div>
      ${d.whisper ? `<div class="set-row"><div class="lbl">Whisper 模型</div>${seg('whisper_model', WHISPER.map(([v, s]) => [v, v, s]))}
        <div class="help">${c.has_whisper ? '第一次使用某个模型时会自动下载。' : '<span class="bad-text">还没安装 faster-whisper</span>：在项目的虚拟环境里运行 <span class="mono">pip install faster-whisper</span>，然后重启 studio。'}</div></div>` : ''}
    </section>

    <section class="set-card" id="sec-render">
      <div><h2>预览渲染</h2><p class="lead">只影响 preview.mp4。导出给 Resolve 的时间线始终链接原片。</p></div>
      <div class="set-grid">
        <div class="set-row"><div class="lbl">默认清晰度</div>${seg('render_hd', [[false, '720p · 快'], [true, '1080p']])}</div>
        <div class="set-row"><div class="lbl"><label for="cfgVol">背景音乐音量</label><span class="val" id="volVal">${Math.round(d.music_volume * 100)}%</span></div>
          <input id="cfgVol" type="range" min="0" max="1.5" step="0.05" data-cfg="music_volume" value="${d.music_volume}" style="margin-top:12px"></div>
      </div>
      <div class="section" style="gap:4px">
        <label class="check"><input type="checkbox" data-cfg="beat_snap" ${d.beat_snap ? 'checked' : ''}>剪辑点对齐音乐节拍<span class="help">（每个剪辑点最多前后挪动 40%，有对白的片段不动；导出 XML 也按这个）</span></label>
        <label class="check"><input type="checkbox" data-cfg="duck_music" ${d.duck_music ? 'checked' : ''}>有人说话时自动压低音乐</label>
        <label class="check"><input type="checkbox" data-cfg="burn_captions" ${d.burn_captions ? 'checked' : ''}>烧录字幕</label>
      </div>
      <div class="set-row"><label class="lbl" for="cfgFont">字幕字体</label>
        <div class="toolbar"><input id="cfgFont" class="field mono" style="flex:1 1 300px;font-size:13px" data-cfg="font" value="${esc(d.font)}" placeholder="${c.detected_font ? '自动：' + esc(c.detected_font) : '没找到中文字体，请填写 .ttf/.ttc 路径'}">
          ${d.font ? '<button type="button" class="ghost" data-act="font-auto">改回自动</button>' : ''}</div>
        <div class="help">留空会自动找系统里的中文字体（微软雅黑、黑体、苹方）。</div></div>
    </section>

    <section class="set-card" id="sec-advanced">
      <div><h2>性能与高级</h2></div>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgWorkers">同时处理几个镜头</label>
          <input id="cfgWorkers" class="field num" type="number" min="1" max="32" data-cfg="workers" value="${d.workers}">
          <div class="help">抽帧和看片的并行数。4K 素材多时调高能快不少；遇到 API 限流（429）就调低。</div></div>
        <div class="set-row"><label class="lbl" for="cfgPort">端口</label>
          <input id="cfgPort" class="field num" type="number" min="1024" max="65535" data-cfg="port" value="${d.port}">
          <div class="help">下次启动 studio 时生效（现在运行在 ${esc(c.running_port || '–')}）。被占用会自动换下一个。</div></div>
      </div>
      <div class="set-row"><label class="lbl" for="cfgProj">项目保存位置</label>
        <div class="toolbar"><input id="cfgProj" class="field mono" style="flex:1 1 300px;font-size:13px" data-cfg="projects_dir" value="${esc(d.projects_dir)}">
          <button type="button" class="ghost" data-act="open-projects">打开文件夹</button></div>
        <div class="help">现在用的是 <span class="mono">${esc(c.projects_path)}</span>。改了要重启 studio 才生效，已有项目不会自动搬过去。</div></div>
      <div class="set-row"><div class="lbl">日志详细程度</div>
        <div style="max-width:520px">${seg('log_level', [['DEBUG', '详细', '含每个接口请求'], ['INFO', '正常', '推荐'], ['WARNING', '只记问题', '警告和报错']])}</div>
        <div class="help">日志写在 <span class="mono">logs/vlogai.log</span>，同时显示在运行 studio 的终端里。右上角的 <a href="#logs">运行日志</a> 可以在网页里看。</div></div>
      <div class="set-row"><label class="lbl" for="cfgBase">API 地址 <span class="help" style="font-weight:400">（一般不用改）</span></label>
        <input id="cfgBase" class="field mono" style="font-size:13px" data-cfg="base_url" value="${esc(d.base_url)}" placeholder="https://api.anthropic.com">
        <div class="help">只有通过公司代理或网关访问时才需要填。填错会导致 401 或连不上。</div></div>
    </section>
    </main>
  </div>
  <div class="set-save" id="setSave">${saveBarHtml(n, restart)}</div>`;
}
function saveBarHtml(n, restart) {
  return `<span>${n ? `有 ${n} 项修改还没保存` : '所有设置都已保存'}${restart ? ' · <span style="color:var(--accent)">端口和项目位置要重启 studio 才生效</span>' : ''}</span>
    <button type="button" class="ghost" data-act="cfg-reset">恢复默认</button>
    ${n ? '<button type="button" class="ghost" data-act="cfg-discard">放弃修改</button>' : ''}
    <button type="button" class="btn" data-act="cfg-save" ${n ? '' : 'disabled'}>保存设置</button>`;
}
function settingsLive() {
  const d = S.draft, e = estimate(), subs = navSubs();
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set('everyVal', `每 ${d.frame_every_sec} 秒一帧`);
  set('badVal', `画质分 &lt; ${(+d.bad_quality).toFixed(2)}`);
  set('volVal', `${Math.round(d.music_volume * 100)}%`);
  const est = $('#estimate');
  if (est) { const b = est.querySelectorAll('b'); b[0].textContent = `${e.f12} 帧`; b[1].textContent = `${e.perShot} tokens`; b[2].textContent = `${e.p500} tokens`; }
  Object.entries(subs).forEach(([k, v]) => set('nav-' + k, esc(v)));
  const c = S.cfg;
  set('setSave', saveBarHtml(cfgChanged(), +d.port !== +c.settings.port || d.projects_dir !== c.settings.projects_dir));
}
function setDraft(key, raw) {
  const def = S.cfg.defaults[key];
  let v = raw;
  if (typeof def === 'boolean') v = raw === true || raw === 'true';
  else if (typeof def === 'number') v = raw === '' ? def : +raw;
  S.draft[key] = v;
  S.cfgDirty = cfgChanged() > 0;
}
async function saveSettingsPage() {
  const changed = {};
  for (const k of Object.keys(S.draft)) if (String(S.draft[k]) !== String(S.cfg.settings[k])) changed[k] = S.draft[k];
  const body = { settings: changed };
  if (S.newKey) body.api_key = S.newKey;
  const r = await act(() => api('/api/settings', body));
  if (!r) return;
  S.cfg = r; S.draft = { ...r.settings }; S.cfgDirty = false; S.newKey = ''; S.keyTest = null;
  S.env = { ...S.env, ...(await api('/api/env')) };
  S.whisper = !!r.settings.whisper; S.hd = !!r.settings.render_hd;
  render(); toast('设置已保存');
}


/* ---------------------------------------------------------------- 运行日志 */
const LOG_LEVELS = [['DEBUG', '全部'], ['INFO', '运行信息'], ['WARNING', '警告和报错'], ['ERROR', '只看报错']];
async function refreshLogs() {
  try {
    const d = await api(`/api/logs?${new URLSearchParams({ level: S.logLevel, q: S.logQ, limit: 800 })}`);
    S.logs = d; S.logCounts = d.counts;
    if (S.page === 'logs') { markLogsSeen(); renderLogRows(); renderLogBadge(); }
  } catch (e) { S.logs = { entries: [], error: e.message, counts: {} }; if (S.page === 'logs') renderLogRows(); }
}
async function refreshLogSummary() {
  try { S.logCounts = (await api('/api/logs/summary')).counts; renderLogBadge(); } catch (e) { /* 旧后端没有这个接口 */ }
}
function markLogsSeen() { if (S.logCounts) store('logseen', { started: S.logCounts.started, error: S.logCounts.error, warning: S.logCounts.warning }); }
function renderLogBadge() {
  const b = $('#logBadge'); if (!b) return;
  const c = S.logCounts; if (!c) { b.hidden = true; return; }
  const seen = store('logseen') || {};
  const same = seen.started === c.started;
  const err = c.error - (same ? seen.error || 0 : 0), warn = c.warning - (same ? seen.warning || 0 : 0);
  if (S.page === 'logs' || (err <= 0 && warn <= 0)) { b.hidden = true; return; }
  b.hidden = false;
  b.className = 'badge' + (err > 0 ? '' : ' warn');
  b.textContent = err > 0 ? (err > 99 ? '99+' : err) : (warn > 99 ? '99+' : warn);
  $('#logsBtn').title = err > 0 ? `有 ${err} 条新报错` : `有 ${warn} 条新警告`;
}
function viewLogs() {
  const c = S.logCounts || {};
  return `<div class="logs-wrap">
    <div class="logs-head">
      <h1>运行日志</h1>
      <div class="seg" role="group" aria-label="显示哪些日志" style="grid-template-columns:repeat(4,minmax(0,1fr))">
        ${LOG_LEVELS.map(([v, l]) => `<button type="button" data-loglevel="${v}" aria-pressed="${S.logLevel === v}">${l}</button>`).join('')}</div>
      <label for="logSearch" class="sr">搜索日志</label>
      <input id="logSearch" class="field" type="search" style="width:240px;min-height:38px" placeholder="搜索：项目名、401、镜头 id…" value="${esc(S.logQ)}">
      <label class="check" style="min-height:38px"><input type="checkbox" id="logAuto" ${S.logAuto ? 'checked' : ''}>自动刷新</label>
    </div>
    <div class="logs-box" id="logBox"></div>
    <div class="logs-foot">
      <span>本次启动以来：<b style="color:var(--danger)">${c.error || 0}</b> 条报错 · <b style="color:var(--accent)">${c.warning || 0}</b> 条警告</span>
      <span style="flex:1">日志文件：<span class="mono">${esc((S.logs && S.logs.file) || 'logs/vlogai.log')}</span>（同样的内容也显示在运行 studio 的终端里）</span>
      <button type="button" class="ghost sm" data-act="logs-refresh">刷新</button>
      <button type="button" class="ghost sm" data-act="logs-open">打开日志文件夹</button>
    </div>
  </div>`;
}
function logRowHtml(e) {
  const lines = e.message.split('\n');
  const first = lines[0], rest = lines.slice(1).join('\n');
  const src = e.source === 'vlogai' ? '' : `<span style="color:var(--faint)">${esc(e.source)} </span>`;
  return `<div class="lrow ${esc(e.level)}"><span class="t" title="${esc(e.time)}">${esc(e.time.slice(11))}</span><span class="lv">${esc(e.level === 'WARNING' ? 'WARN' : e.level)}</span>
    <div class="m">${src}${rest ? `<details><summary>${esc(first)}</summary><pre>${esc(rest)}</pre></details>` : esc(first)}</div></div>`;
}
function renderLogRows(force) {
  const box = $('#logBox'); if (!box) return;
  if (!S.logs) { box.innerHTML = '<div class="muted" style="padding:16px">正在读取…</div>'; return; }
  if (S.logs.error) { box.innerHTML = `<div class="err" style="margin:16px">读取日志失败：${esc(S.logs.error)}。如果刚更新过代码，请重启 studio。</div>`; return; }
  const atBottom = force || box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  const open = new Set([...box.querySelectorAll('details[open]')].map((d) => d.parentElement.parentElement.dataset.k));
  box.innerHTML = S.logs.entries.length
    ? S.logs.entries.map((e, i) => logRowHtml(e).replace('<div class="lrow', `<div data-k="${esc(e.time + i)}" class="lrow`)).join('')
    : '<div class="muted" style="padding:16px">没有符合条件的日志。</div>';
  box.querySelectorAll('[data-k]').forEach((r) => { if (open.has(r.dataset.k)) { const d = r.querySelector('details'); if (d) d.open = true; } });
  if (atBottom) box.scrollTop = box.scrollHeight;
  const foot = document.querySelector('.logs-foot span');
  const c = S.logCounts || {};
  if (foot) foot.innerHTML = `本次启动以来：<b style="color:var(--danger)">${c.error || 0}</b> 条报错 · <b style="color:var(--accent)">${c.warning || 0}</b> 条警告`;
}
setInterval(() => {
  if (document.hidden) return;
  if (S.page === 'logs' && S.logAuto && !(document.activeElement && document.activeElement.id === 'logSearch' && S.logTyping)) refreshLogs();
}, 2000);
setInterval(() => { if (!document.hidden && S.page !== 'logs') refreshLogSummary(); }, 10000);

/* ---------------------------------------------------------------- actions */
async function newProject() {
  const r = await browse('new', S.p ? S.p.footage : '');
  if (!r) return;
  await startJob('/api/projects', { footage: r.path, name: r.name }, () => {
    location.hash = '#footage';
    startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper });
  });
}
async function restore(n) {
  if (!(await confirmBox(`恢复 v${n}？`, '当前版本不会丢，恢复会另存成一个新版本。', '恢复'))) return false;
  const r = await act(() => api(`${P()}/restore`, { n }));
  if (!r) return false;
  S.tlDirty = false; await loadProject(); render(); toast(`已恢复 v${n}`);
  return true;
}

document.addEventListener('click', async (e) => {
  const t = e.target.closest('button, a');
  if (!t || t.closest('dialog')) return;
  const d = t.dataset;

  if (d.shot) return selectShot(d.shot);
  if (d.frame != null) { S.fframe = +d.frame; $('#fDetail').innerHTML = detailHtml(); return; }
  if (d.day) { S.day = d.day; $('#fGrid').innerHTML = gridHtml(); $('#fDays').innerHTML = dayNavHtml(); return; }
  if (d.filter) { S.filters[d.filter] = !S.filters[d.filter]; t.setAttribute('aria-pressed', S.filters[d.filter]); $('#fGrid').innerHTML = gridHtml(); return; }
  if (d.flag) {
    const s = S.byId[S.fsel]; const on = d.flag === 'must' ? !s.must : !s.excluded;
    const r = await act(() => api(`${P()}/flag`, { shot: s.id, flag: d.flag, on }));
    if (r) {
      for (const x of S.shots) { x.must = r.flags.must.includes(x.id); x.excluded = r.flags.exclude.includes(x.id); }
      S.p.flags = { must: r.flags.must.length, exclude: r.flags.exclude.length };
      $('#fDetail').innerHTML = detailHtml();
      const card = document.querySelector(`.card[data-shot="${CSS.escape(s.id)}"]`);
      if (card) card.outerHTML = cardHtml(s);
    }
    return;
  }
  if (d.min) { await act(() => saveSettings({ minutes: +d.min })); return render(); }
  if (d.vert) { await act(() => saveSettings({ vertical: d.vert === '1' })); return render(); }
  if (d.hd) { S.hd = d.hd === '1'; return render(); }
  if (d.suggest) { const i = $('#tAskInput'); i.value = d.suggest; i.focus(); return; }
  if (d.clip != null) return selectClip(+d.clip, true);
  if (d.move) {
    const i = S.tsel, j = i + +d.move;
    if (j < 0 || j >= S.tl.length) return;
    [S.tl[i], S.tl[j]] = [S.tl[j], S.tl[i]]; S.tsel = j;
    return tlChanged(true);
  }
  if (d.restore) return restore(+d.restore);
  if (d.loglevel) { S.logLevel = d.loglevel; store('loglevel', S.logLevel); document.querySelectorAll('[data-loglevel]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.loglevel === S.logLevel)); return refreshLogs(); }
  if (d.scroll) { const el = document.getElementById('sec-' + d.scroll); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  if (d.set) { setDraft(d.set, d.val); return render(); }
  if (d.toggle) { setDraft(d.toggle, !S.draft[d.toggle]); return render(); }
  if (d.bmove || d.bdel != null) {
    const i = +t.closest('[data-beat]').dataset.beat;
    if (d.bmove) { const j = i + +d.bmove; [S.outline.beats[i], S.outline.beats[j]] = [S.outline.beats[j], S.outline.beats[i]]; }
    else S.outline.beats.splice(i, 1);
    outlineChanged(); return render();
  }

  switch (d.act) {
    case 'new-project': return newProject();
    case 'toggle-key': S.showKey = !S.showKey; return render();
    case 'test-key': {
      S.keyTest = 'testing'; render();
      const r = await act(() => api('/api/settings/test', { api_key: S.newKey || undefined }));
      S.keyTest = r || { ok: false, reason: '请求失败' }; return render();
    }
    case 'font-auto': setDraft('font', ''); return render();
    case 'open-projects': await act(() => api('/api/settings/open_projects', {})); return;
    case 'cfg-save': return saveSettingsPage();
    case 'logs-refresh': return refreshLogs();
    case 'logs-open': await act(() => api('/api/logs/open', {})); return;
    case 'cfg-retry': S.cfgError = null; await loadSettings(); return render();
    case 'cfg-discard': S.draft = { ...S.cfg.settings }; S.cfgDirty = false; S.newKey = ''; S.keyTest = null; return render();
    case 'cfg-reset': {
      if (!(await confirmBox('恢复默认设置？', 'API Key 不受影响，其他设置都回到默认值。', '恢复默认'))) return;
      const r = await act(() => api('/api/settings', { reset: true }));
      if (r) { S.cfg = r; S.draft = { ...r.settings }; S.cfgDirty = false; render(); toast('已恢复默认设置'); }
      return;
    }
    case 'rescan': {
      const r = await browse('video', S.p.footage);
      if (!r) return;
      return startJob('/api/projects', { footage: r.path, name: S.pid }, () => startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper }));
    }
    case 'analyze':
      if (!mockOn() && !(await confirmBox('开始 AI 看片？', 'AI 会逐个镜头看一遍，按镜头数计费；已经看过的镜头不会重复计费。', '开始'))) return;
      return startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper });
    case 'pick-music': {
      const r = await browse('audio', (S.p.settings || {}).music || '');
      if (r && r.path) { await act(() => saveSettings({ music: r.path })); render(); }
      return;
    }
    case 'clear-music': await act(() => saveSettings({ music: '' })); return render();
    case 'outline': {
      if (S.outline && S.outline.beats.length && !(await confirmBox('重新生成故事线？', '现在的分幕和你的修改会被替换。', '重新生成'))) return;
      const idea = $('#sBrief').value;
      S.outlineDirty = false; store('outline.' + S.pid, null);
      return startJob(`${P()}/outline`, { idea, minutes: +(S.p.settings.minutes || 3), mock: mockOn() });
    }
    case 'add-beat':
      S.outline.beats.push({ id: 'b' + Date.now(), title: '新的一幕', intent: '', seconds: 20, shot_ids: [] });
      outlineChanged(); render();
      { const items = document.querySelectorAll('.beat .title'); const last = items[items.length - 1]; if (last) { last.focus(); last.select(); } }
      return;
    case 'make-story': {
      await act(() => saveSettings({ brief: $('#sBrief').value }));
      if (S.story && !(await confirmBox('按这个结构重新剪一版？', '之前的版本都保留在版本历史里。', '开始剪'))) return;
      const ok = await startJob(`${P()}/story`, { beats: S.outline.beats, minutes: +(S.p.settings.minutes || 3), mock: mockOn() }, () => { location.hash = '#timeline'; });
      if (ok) { S.outlineDirty = false; store('outline.' + S.pid, null); }
      return;
    }
    case 'render': return startRender();
    case 'export-xml': return startJob(`${P()}/export`, {});
    case 'recheck': return startJob(`${P()}/recheck`, {});
    case 'open-folder': await act(() => api(`${P()}/open_folder`, {})); return;
    case 'del-clip':
      S.tl.splice(S.tsel, 1); S.tsel = clamp(S.tsel, 0, S.tl.length - 1);
      return tlChanged(true);
    case 'discard': resetTimeline(); renderTracks(); renderEditor(); renderSavebar(); return;
    case 'save-tl': return saveTimeline(false);
    case 'save-render': return saveTimeline(true);
    default:
  }
});

document.addEventListener('input', (e) => {
  const t = e.target;
  if (t.id === 'fSearch') { S.filters.q = t.value; $('#fGrid').innerHTML = gridHtml(); return; }
  if (t.id === 'logSearch') { S.logQ = t.value; clearTimeout(S.logT); S.logT = setTimeout(refreshLogs, 300); return; }
  if (t.id === 'cfgKey') { S.newKey = t.value.trim(); S.keyTest = null; settingsLive(); return; }
  if (t.dataset.cfg && t.type !== 'checkbox') { setDraft(t.dataset.cfg, t.value); settingsLive(); return; }
  if (t.dataset.bf) {
    const b = S.outline.beats[+t.closest('[data-beat]').dataset.beat];
    b[t.dataset.bf] = t.dataset.bf === 'seconds' ? Math.max(1, +t.value || 1) : t.value;
    return outlineChanged();
  }
  if (S.page === 'timeline' && S.tl && S.tl[S.tsel]) {
    const c = S.tl[S.tsel];
    if (t.id === 'eText') { c.text = t.value; return tlChanged(); }
    if (t.id === 'eChap') { c.chapter = t.value; return tlChanged(); }
  }
});

document.addEventListener('change', async (e) => {
  const t = e.target;
  if (t.id === 'projectSelect') {
    if (t.value === '__new') { t.value = S.pid || ''; return newProject(); }
    if (S.tlDirty && !(await confirmBox('放弃未保存的时间线修改？', '切换项目会丢掉还没保存的修改。', '放弃并切换'))) { t.value = S.pid; return; }
    return switchProject(t.value);
  }
  if (t.id === 'mockToggle') { store('mock', t.checked); return render(); }
  if (t.dataset.cfg && t.type === 'checkbox') { setDraft(t.dataset.cfg, t.checked); settingsLive(); return; }
  if (t.id === 'fWhisper') { S.whisper = t.checked; return; }
  if (t.id === 'logAuto') { S.logAuto = t.checked; return; }
  if (t.id === 'sBrief') { await act(() => saveSettings({ brief: t.value })); return; }
  if (t.id === 'sMinCustom' && t.value) { await act(() => saveSettings({ minutes: +t.value })); return render(); }
  if (t.id === 'tVersion') {
    const n = +t.value, cur = S.versions[0];
    if (cur && n !== cur.n && !(await restore(n))) t.value = cur.n;
    return;
  }
  if (S.page === 'timeline' && S.tl && S.tl[S.tsel]) {
    const c = S.tl[S.tsel], s = S.byId[c.shot_id];
    const max = s ? s.duration : 1e9;
    if (t.id === 'eIn' || t.id === 'eOut') {
      let a = clamp(+$('#eIn').value || 0, 0, max), b = clamp(+$('#eOut').value || 0, 0, max);
      if (b - a < 0.8) { if (t.id === 'eIn') a = Math.max(0, b - 0.8); else b = Math.min(max, a + 0.8); }
      c.in = +a.toFixed(2); c.out = +b.toFixed(2);
      return tlChanged(true);
    }
    if (t.id === 'eAudio') { c.keep_audio = t.checked; return tlChanged(); }
  }
});

document.addEventListener('submit', async (e) => {
  if (e.target.id !== 'tAsk') return;
  e.preventDefault();
  const v = $('#tAskInput').value.trim();
  if (!v) return;
  if (S.tlDirty) {
    if (!(await confirmBox('先放弃手动修改吗？', 'AI 会在已保存的版本上修改，没保存的手动修改会丢掉。想留着的话先点“保存”。', '放弃并继续'))) return;
    resetTimeline();
  }
  startJob(`${P()}/revise`, { instruction: v, minutes: +(S.p.settings.minutes || 3), mock: mockOn() });
});

/* 拖动排序：分幕和时间线镜头 */
let drag = null;
document.addEventListener('dragstart', (e) => {
  const b = e.target.closest && e.target.closest('[data-beat], [data-clip]');
  if (!b) return;
  if (b.dataset.beat != null && ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && b.contains(document.activeElement)) { e.preventDefault(); return; }
  drag = b.dataset.beat != null ? { kind: 'beat', i: +b.dataset.beat } : { kind: 'clip', i: +b.dataset.clip };
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', String(drag.i)); } catch (err) { /* ignore */ }
});
document.addEventListener('dragover', (e) => {
  if (!drag) return;
  const over = e.target.closest(drag.kind === 'beat' ? '[data-beat]' : '[data-clip]');
  document.querySelectorAll('.drag-over').forEach((x) => x !== over && x.classList.remove('drag-over'));
  if (over) { e.preventDefault(); over.classList.add('drag-over'); }
});
document.addEventListener('drop', (e) => {
  if (!drag) return;
  const over = e.target.closest(drag.kind === 'beat' ? '[data-beat]' : '[data-clip]');
  document.querySelectorAll('.drag-over').forEach((x) => x.classList.remove('drag-over'));
  if (!over) { drag = null; return; }
  e.preventDefault();
  const to = +(drag.kind === 'beat' ? over.dataset.beat : over.dataset.clip), from = drag.i, kind = drag.kind;
  drag = null;
  if (to === from) return;
  const arr = kind === 'beat' ? S.outline.beats : S.tl;
  const [item] = arr.splice(from, 1);
  arr.splice(to, 0, item);
  if (kind === 'beat') { outlineChanged(); render(); } else { S.tsel = to; tlChanged(true); }
});
document.addEventListener('dragend', () => { drag = null; document.querySelectorAll('.drag-over').forEach((x) => x.classList.remove('drag-over')); });

$('#jobToggle').addEventListener('click', () => { S.jobOpen = $('#jobLog').hidden; renderJob(); if (!S.jobOpen) $('#jobLog').hidden = true; });
$('#jobClose').addEventListener('click', () => { S.jobHidden = true; S.jobOpen = false; $('#jobPanel').hidden = true; });
window.addEventListener('hashchange', async () => {
  if (location.hash === '#settings' && !S.cfgDirty) await loadSettings();
  if (location.hash === '#logs') { S.logs = null; }
  render(); window.scrollTo(0, 0);
});
window.addEventListener('beforeunload', (e) => { if (S.tlDirty) { e.preventDefault(); e.returnValue = ''; } });

/* ---------------------------------------------------------------- 启动 */
(async function boot() {
  try {
    S.env = { drives: [], ...(await api('/api/env')) };
    await loadSettings();
    S.logLevel = store('loglevel') || 'INFO';
    refreshLogSummary();
    if (S.cfg) { S.whisper = !!S.cfg.settings.whisper; S.hd = !!S.cfg.settings.render_hd; }
    await loadProjects();
    const want = store('project');
    S.pid = S.projects.some((p) => p.id === want) ? want : (S.projects[0] || {}).id || null;
    await loadProject();
    if (!location.hash && S.p) location.hash = S.p.has_story ? '#timeline' : S.p.has_outline ? '#story' : '#footage';
    render();
    const j = await api('/api/job');
    if (j.status === 'running') { S.job = j; S.jobSince = 0; pollJob(); }
  } catch (e) {
    $('#app').innerHTML = `<div class="page"><div class="content"><div class="empty"><h2>连不上 vlogai 后端</h2>
      <p style="margin:0">先在终端运行 <span class="mono">python -m vlogai.studio</span>，再刷新这个页面。</p><p class="err">${esc(e.message)}</p></div></div></div>`;
  }
})();
