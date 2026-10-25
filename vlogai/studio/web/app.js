/* vlogai studio —— 前端（无构建步骤，原生 JS）。接口见 API.md。 */
'use strict';

const S = {
  env: { has_key: false, home: '', drives: [] },
  projects: [], pid: null, p: null,          // p = GET /api/projects/{id}
  shots: [], byId: {}, outline: null, story: null, versions: [],
  page: 'footage',
  fsel: null, fframe: null, day: 'all',
  filters: { q: '', star: false, talk: false, vert: false, hideBlur: false, prot: false },
  outlineDirty: false,
  tl: null, tlDirty: false, tsel: 0,
  job: null, jobLines: [], jobSince: 0, jobOpen: false, jobHidden: false,
  hd: false, whisper: false,
  plan: null, planEdit: null, planDirty: false, planDay: 0,
  logs: null, logLevel: 'INFO', logQ: '', logAuto: true, logCounts: null,
  cfg: null, cfgError: null, draft: null, cfgDirty: false, newKey: '', showKey: false, keyTest: null,
};

/* ---------------------------------------------------------------- helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function mmss(sec) { sec = Math.max(0, Math.round(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`; }
const SHOT_TYPES = { wide: '远景', medium: '中景', 'close-up': '近景', detail: '特写', selfie: '自拍', pov: '第一人称', drone: '航拍', timelapse: '延时', screen: '屏幕' };
const shotType = (t) => (SHOT_TYPES[t] ? tr(SHOT_TYPES[t]) : t || '');
const enc = encodeURIComponent;
const locale = () => I18N.locale();
const P = () => `/api/projects/${enc(S.pid)}`;
/* 镜头工坊做出的片段也在 /shots 里（时间线要用），但素材页不显示它们 */
function setShots(list) { S.byId = Object.fromEntries(list.map((s) => [s.id, s])); S.shots = list.filter((s) => !s.lab); }
const jobRunning = () => !!(S.job && S.job.status === 'running');
const dis = () => (jobRunning() ? 'disabled' : '');
const media = (name, mt) => `/media/${enc(S.pid)}/${name}?t=${mt || Date.now()}`;
const thumbOf = (id) => (S.byId[id] || {}).thumb || '';
function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem('vlogai.' + k)); localStorage.setItem('vlogai.' + k, JSON.stringify(v)); } catch (e) { return null; } return null; }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opt); } catch (e) { const err = new Error(e.message); err.offline = true; throw err; }
  const d = await r.json().catch(() => ({ error: tr('服务器没有返回 JSON') }));
  if (!r.ok || d.error) throw new Error(trServer(d.error) || tr('请求失败 ({0})', r.status));
  return d;
}
/* /api/job 的 error 字段是“上一个任务为什么失败”，不是这次请求失败，所以不能走 api()。 */
async function getJob(since = 0) {
  let r;
  try { r = await fetch(`/api/job?since=${since}`); } catch (e) { const err = new Error(e.message); err.offline = true; throw err; }
  if (!r.ok) throw new Error(tr('请求失败 ({0})', r.status));
  return r.json();
}
const linkify = (html) => html.replace(/https?:\/\/[^\s，。；、）)<]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
function toast(msg, bad) {
  const p = $('#jobPanel');
  p.hidden = false; p.className = 'job ' + (bad ? 'fail' : 'done');
  $('#jobName').textContent = bad ? tr('出错了') : tr('好了');
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
async function confirmBox(title, text, ok = tr('确定')) {
  const r = await modal(`<h2>${esc(title)}</h2><p class="muted" style="margin:0;line-height:1.6;white-space:pre-line;word-break:break-all">${esc(text)}</p>
    <div class="row-end"><button class="ghost" value="">${tr('取消')}</button><button class="btn" value="ok">${esc(ok)}</button></div>`);
  return r === 'ok';
}

/** 服务器端的文件夹浏览器（浏览器拿不到真实路径）。kind: 'video' 选文件夹，'audio' 选音乐（输入完整路径或在文件夹里找）。 */
async function browse(kind, start) {
  let chosen = '', name = '';
  const isNew = kind === 'new';
  const html = `<h2>${kind === 'audio' ? tr('选择背景音乐') : isNew ? tr('新建项目：选择素材文件夹') : tr('选择素材文件夹')}</h2>
    ${S.env.drives.length ? `<div class="drives">${S.env.drives.map((d) => `<button type="button" class="chip" style="min-height:32px" data-go="${esc(d)}">${esc(d)}</button>`).join('')}</div>` : ''}
    <div class="toolbar"><button type="button" class="ghost sm" id="bUp">${tr('↑ 上一级')}</button>
      <input class="field mono" id="bPath" style="font-size:12px;min-height:36px" aria-label="${tr('路径')}"><button type="button" class="ghost sm" id="bGo">${tr('前往')}</button></div>
    <div class="browse-list" id="bList"></div>
    ${kind === 'audio' ? `<label class="section"><span class="label">${tr('音乐文件的完整路径（mp3 / m4a / wav）')}</span>
      <input class="field mono" id="bFile" style="font-size:12px" placeholder="${tr('比如')} D:\\Music\\bgm.mp3"></label>` : ''}
    ${isNew ? `<label class="section"><span class="label">${tr('项目名称（可留空，默认用文件夹名）')}</span><input class="field" id="bName" autocomplete="off"></label>
      <button type="button" class="ghost sm" id="bPlanOnly" style="align-self:flex-start">${tr('还没有素材？先建项目写拍摄脚本')}</button>` : ''}
    <div class="row-end"><span class="muted" id="bInfo" style="flex:1"></span><button class="ghost" value="">${tr('取消')}</button>
      <button class="btn" type="button" id="bPick">${kind === 'audio' ? tr('使用这个文件') : isNew ? tr('开始扫描') : tr('选择这个文件夹')}</button></div>`;
  const r = await modal(html, (b, close) => {
    let cur = null;
    const load = async (path) => {
      const d = await act(() => api(`/api/ls?path=${enc(path || '')}`));
      if (!d) return;
      cur = d;
      $('#bPath', b).value = d.path;
      $('#bUp', b).disabled = !d.parent;
      if (kind !== 'audio') {
        $('#bInfo', b).textContent = d.videos_here ? tr('这个文件夹里有 {0} 个视频', d.videos_here) : tr('这里没有视频，进下一层看看');
        $('#bPick', b).disabled = !d.videos_here;
      }
      $('#bList', b).innerHTML = d.dirs.map((x) => `<button type="button" data-dir="${esc(x.path)}"><span class="ico">▸</span><span style="flex:1">${esc(x.name)}</span><span class="muted mono" style="font-size:12px">${x.videos ? tr('{0} 个视频', x.videos) : ''}</span></button>`).join('')
        || `<div class="muted" style="padding:14px">${tr('这里没有子文件夹')}</div>`;
    };
    b.addEventListener('click', (e) => {
      const t = e.target.closest('button'); if (!t) return;
      if (t.dataset.dir) load(t.dataset.dir);
      else if (t.dataset.go) load(t.dataset.go);
      else if (t.id === 'bUp' && cur) load(cur.parent);
      else if (t.id === 'bGo') load($('#bPath', b).value);
      else if (t.id === 'bPlanOnly') { chosen = ''; name = $('#bName', b).value.trim(); close('plan'); }
      else if (t.id === 'bPick') {
        if (kind === 'audio') {
          chosen = $('#bFile', b).value.trim().replace(/^"|"$/g, '');
          if (!chosen) { $('#bInfo', b).textContent = tr('先填音乐文件路径'); return; }
        } else { if (!cur) return; chosen = cur.path; }
        if (isNew) name = $('#bName', b).value.trim();
        close('ok');
      }
    });
    $('#bPath', b).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); load(e.target.value); } });
    if (kind === 'audio') {
      $('#bInfo', b).textContent = tr('浏览器拿不到文件路径：可以在资源管理器里按住 Shift 右键音乐文件，选“复制文件地址”，粘贴到下面。');
      if (start) $('#bFile', b).value = start;
    }
    load(kind === 'audio' ? (start ? start.replace(/[\\/][^\\/]*$/, '') : S.env.home) : (start || S.env.home));
  });
  if (r === 'plan') return { planOnly: true, name };
  return r === 'ok' ? { path: chosen, name } : null;
}

/* ---------------------------------------------------------------- data */
async function loadProjects() { S.projects = (await api('/api/projects')).projects; }
async function loadProject() {
  S.lab = null;                       // 镜头工坊的数据在打开那一页时再读
  if (!S.pid) { S.p = null; S.shots = []; S.byId = {}; S.outline = null; S.story = null; S.versions = []; S.plan = null; S.planEdit = null; return; }
  const [p, sh, ol, st, vs, pl] = await Promise.all([
    api(P()), api(`${P()}/shots`), api(`${P()}/outline`), api(`${P()}/story`), api(`${P()}/versions`),
    api(`${P()}/shootplan`).catch(() => ({}))]);
  S.plan = pl && pl.days ? pl : null;
  if (!S.planDirty) planCopy();
  S.p = p; setShots(sh.shots);
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
  S.fsel = null; S.day = 'all'; S.tsel = 0; S.outlineDirty = false; S.tlDirty = false; S.planDirty = false; S.planDay = 0; S.lb = null;
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
  try { j = await getJob(S.jobSince); } catch (e) { pollTimer = setTimeout(pollJob, 2000); return; }
  S.jobSince = j.line_count || 0;
  S.jobLines = S.jobLines.concat(j.lines || []).slice(-400);
  const was = S.job && S.job.status;
  S.job = j;
  renderJob();
  if (j.status === 'running') {
    tick += 1;
    if (S.page === 'footage' && j.project === S.pid && tick % 3 === 0) {
      S.p = await api(P()).catch(() => S.p);
      if (tick % 6 === 0) { const sh = await api(`${P()}/shots`).catch(() => null); if (sh) setShots(sh.shots); }
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
function applyStaticText() {
  document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = tr(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-aria]').forEach((el) => el.setAttribute('aria-label', tr(el.dataset.i18nAria)));
  document.querySelectorAll('[data-i18n-title]').forEach((el) => el.setAttribute('title', tr(el.dataset.i18nTitle)));
  const sel = $('#uiLang');
  if (sel) sel.innerHTML = I18N.LANGS.map(([c, n]) => `<option value="${c}" ${c === I18N.lang ? 'selected' : ''}>${esc(n)}</option>`).join('');
}
function setUiLang(code) {
  I18N.set(code);
  render(); renderJob();
}
function renderHeader() {
  applyStaticText();
  $('#projectSelect').innerHTML = (S.projects.length ? S.projects.map((p) => `<option value="${esc(p.id)}" ${p.id === S.pid ? 'selected' : ''}>${esc(p.name)}</option>`).join('') : `<option value="">${tr('还没有项目')}</option>`)
    + `<option value="__new">${tr('＋ 新建项目…')}</option>`;
  const p = S.p || {};
  const steps = [['plan', '拍摄脚本', !!p.has_plan], ['footage', '素材', p.logged > 0], ['story', '故事线', !!p.has_outline], ['timeline', '时间线', !!p.has_story], ['lab', '镜头工坊', !!(S.lab && S.lab.assets && S.lab.assets.length)], ['export', '导出', !!p.has_xml]];
  $('#steps').innerHTML = steps.map(([id, name, done], i) => `<a href="#${id}" ${S.page === id ? 'aria-current="page"' : ''}>
    ${done && S.page !== id ? '<span class="done">✓</span>' : `<span class="n">${i}</span>`}${tr(name)}</a>`).join('');
  $('#mockToggle').checked = mockOn();
  if (S.page === 'settings') $('#gearBtn').setAttribute('aria-current', 'page'); else $('#gearBtn').removeAttribute('aria-current');
  if (S.page === 'logs') $('#logsBtn').setAttribute('aria-current', 'page'); else $('#logsBtn').removeAttribute('aria-current');
  renderLogBadge();
  const k = $('#keyState');
  k.textContent = S.env.has_key ? tr('API Key 已就绪') : tr('未设置 API Key');
  k.className = 'pill' + (S.env.has_key ? ' ok' : '');
  const kh = S.env.key || {};
  k.title = S.env.has_key ? tr('studio 正在用的 Key：{0}（{1} 位，来自 {2}）。点这里到设置页修改。', kh.hint, kh.length, kh.source)
    : tr('还没有 API Key，只能用离线模拟。点这里到设置页填写。');
  if (S.env.has_key) k.textContent = `Key ${kh.hint}`;
}

function renderJob() {
  const j = S.job, p = $('#jobPanel');
  if (!j || j.status === 'idle' || !j.name || S.jobHidden) { p.hidden = true; return; }
  p.hidden = false;
  p.className = 'job' + (j.status === 'error' ? ' fail' : j.status === 'done' ? ' done' : '');
  $('#jobName').textContent = j.status === 'running' ? tr(j.name) + '…' : j.status === 'error' ? tr('{0}失败', tr(j.name)) : tr('{0}完成', tr(j.name));
  const last = S.jobLines.filter((l) => !l.startsWith('Traceback')).slice(-1)[0] || '';
  if (j.status === 'error') $('#jobLast').innerHTML = linkify(esc(trServer(j.error)));
  else $('#jobLast').textContent = (j.total ? `${j.done}/${j.total} · ` : '') + trServer(last.replace('[vlogai] ', ''));
  if (j.status === 'error' && j.started) store('seenJobError', j.started);
  $('#jobLast').classList.toggle('wrap', j.status === 'error');
  const bar = $('#jobBar'), fill = $('#jobFill');
  bar.hidden = j.status !== 'running';
  bar.classList.toggle('indet', j.pct == null);
  fill.style.width = (j.pct || 0) + '%';
  const log = $('#jobLog');
  log.hidden = !(S.jobOpen || j.status === 'error');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
  log.textContent = S.jobLines.join('\n');
  if (atBottom) log.scrollTop = log.scrollHeight;
  $('#jobToggle').textContent = log.hidden ? tr('日志') : tr('收起');
  if (j.status !== 'running') refreshLogSummary();
  if (j.status === 'done' && !S.jobOpen) { clearTimeout(renderJob.t); renderJob.t = setTimeout(() => { if (S.job && S.job.status === 'done' && !S.jobOpen) p.hidden = true; }, 4000); }
}

function render() {
  const page = (location.hash || '#footage').slice(1);
  S.page = ['plan', 'footage', 'story', 'timeline', 'lab', 'export', 'settings', 'logs'].includes(page) ? page : 'footage';
  document.body.classList.toggle('on-settings', S.page === 'settings');
  renderHeader();
  const active = document.activeElement;
  const keep = active && active.id ? { id: active.id, a: active.selectionStart, b: active.selectionEnd } : null;
  $('#app').innerHTML = S.page === 'settings' ? viewSettings() : S.page === 'logs' ? viewLogs() : !S.pid || !S.p ? viewWelcome() : { plan: viewPlan, footage: viewFootage, story: viewStory, timeline: viewTimeline, lab: viewLab, export: viewExport }[S.page]();
  if (S.pid && S.p && S.page === 'timeline') { renderTracks(); renderEditor(); renderSavebar(); }
  if (S.page === 'logs') { renderLogRows(true); if (S.logLevel === 'USAGE' ? !S.usage : !S.logs) refreshLogs(); }
  if (S.page === 'lab') cropMount();
  if (keep) {
    const el = document.getElementById(keep.id);
    if (el) { el.focus(); try { if (keep.a != null) el.setSelectionRange(keep.a, keep.b); } catch (e) { /* not text */ } }
  }
}

function viewWelcome() {
  return `<div class="page"><div class="content"><div class="empty"><h2>${tr('把一堆素材，变成一个有故事的片子')}</h2>
    <div class="welcome-grid">
      <section class="ex-card"><h3 style="font-size:18px">${tr('还没出发？')}</h3>
        <p>${tr('先写一份拍摄脚本：AI 帮你想好每天要拍哪些镜头，回来之后剪起来更顺。')}</p>
        <div class="foot"><button type="button" class="ghost outline" data-act="new-plan-project" ${dis()}>${tr('先写拍摄脚本')}</button></div></section>
      <section class="ex-card"><h3 style="font-size:18px">${tr('已经拍完了？')}</h3>
        <p>${tr('选一个放着这趟旅行视频的文件夹（子文件夹也行）。AI 会逐个镜头看一遍，你写下想讲的故事，它用真实拍到的素材搭出结构，再剪成一条可以继续编辑的时间线。')}</p>
        <div class="foot"><button type="button" class="btn" data-act="new-project" ${dis()}>${tr('选择素材文件夹')}</button></div></section>
    </div></div></div></div>`;
}


/* ---------------------------------------------------------------- 0 拍摄脚本 */
const FRAMINGS = ['wide', 'medium', 'close-up', 'detail', 'selfie', 'pov', 'drone'];
const SHIELD = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v6c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/></svg>';

function planCopy() { S.planEdit = S.plan ? JSON.parse(JSON.stringify(S.plan)) : null; S.planDirty = false; }
function planStats(pl) {
  const shots = (pl.days || []).flatMap((d) => d.shots || []);
  const done = new Set(pl.done || []);
  const must = shots.filter((s) => s.must_have);
  return { total: shots.length, done: shots.filter((s) => done.has(s.shot_id)).length,
    must: must.length, mustDone: must.filter((s) => done.has(s.shot_id)).length };
}
function planProgressHtml() {
  const st = planStats(S.planEdit);
  const pct = st.total ? Math.round((100 * st.done) / st.total) : 0;
  return `<div class="toolbar" style="justify-content:space-between"><span>${tr('已拍 {0} / {1}', st.done, st.total)}</span>
      <span class="${st.mustDone < st.must ? 'muted' : 'ok-text'}">${tr('必拍 {0} / {1}', st.mustDone, st.must)}</span></div>
    <div class="bar"><i style="width:${pct}%"></i></div>`;
}
function planShotHtml(s, di, si) {
  const done = (S.planEdit.done || []).includes(s.shot_id);
  return `<li class="pshot ${done ? 'done' : ''} ${s.must_have ? 'must' : ''}" data-pday="${di}" data-pidx="${si}">
    <label class="pcheck"><input type="checkbox" data-pcheck="${esc(s.shot_id)}" ${done ? 'checked' : ''} aria-label="${tr('拍完了')}"></label>
    <div class="pbody">
      <div class="ppics ${S.planEdit.vertical ? 'v' : ''}">
        ${s.sketch_svg === undefined ? '' : `<div class="psketch" title="${tr('构图草图')}">${s.sketch_svg || `<span class="note">${tr('保存后更新草图')}</span>`}</div>`}
        ${(s.ref_urls || []).map((u, ri) => `<div class="pref"><a href="${esc(u)}" target="_blank" rel="noopener"><img src="${esc(u)}" alt="${tr('参考图')}" loading="lazy"></a>
          <button type="button" class="pref-del" data-prefdel="${esc((s.refs || [])[ri] || '')}" aria-label="${tr('删除这张参考图')}">×</button></div>`).join('')}
        ${(s.refs || []).length < 3 ? `<button type="button" class="pref-add" data-prefadd title="${tr('也可以把图片拖到这里，或者点一下这个镜头再粘贴')}">${tr('+ 参考图')}</button>` : ''}
      </div>
      <div class="prow">
        <select data-pf="framing" aria-label="${tr('景别')}">${FRAMINGS.concat(FRAMINGS.includes(s.framing) || !s.framing ? [] : [s.framing])
          .map((f) => `<option value="${esc(f)}" ${f === s.framing ? 'selected' : ''}>${esc(shotType(f) || f)}</option>`).join('')}</select>
        <input data-pf="hold" value="${esc(s.hold || '')}" placeholder="${tr('拍多久')}" aria-label="${tr('拍多久')}">
        <button type="button" class="chip pmust" data-pmust aria-pressed="${!!s.must_have}">${tr('必拍')}</button>
        ${s.how ? `<span class="phow">${esc(s.how.split(' · ').map((x) => tr(x)).join(' · '))}</span>` : ''}
        <span class="mono muted" style="font-size:11px;margin-left:auto">${esc(s.shot_id || '')}</span>
      </div>
      <textarea data-pf="what" rows="2" aria-label="${tr('拍什么')}" placeholder="${tr('拍什么：画面里有什么、发生什么')}">${esc(s.what || '')}</textarea>
      ${s.beat || s.why ? `<div class="pwhy">${esc(s.beat || '')}${s.beat && s.why ? ' — ' : ''}${esc(s.why || '')}</div>` : ''}
    </div>
    <button type="button" class="ghost sm danger" data-pdel aria-label="${tr('删除这个镜头')}">${tr('删除')}</button>
  </li>`;
}
function planSaveHtml() {
  return S.planDirty ? `<div class="savebar"><span>${tr('有未保存的修改')}</span>
    <button type="button" class="ghost sm" data-act="plan-discard">${tr('放弃')}</button>
    <button type="button" class="btn sm" data-act="plan-save">${tr('保存')}</button></div>` : '';
}
function viewPlan() {
  const s = S.p.settings || {};
  const pl = S.planEdit;
  const minutes = +(s.minutes || 3);
  const lens = [1, 2, 4, 8];
  const custom = !lens.includes(minutes);
  const days = (pl && pl.days) || [];
  S.planDay = clamp(S.planDay || 0, 0, Math.max(0, days.length - 1));
  const day = days[S.planDay];
  return `<div class="page">
    <aside class="side wide">
      <div><h1 style="font-size:24px;margin-bottom:6px">${tr('出发前，先想好要拍什么')}</h1>
        <p class="muted" style="margin:0;line-height:1.6">${tr('告诉 AI 你要去哪、和谁、想要什么感觉。它会写一份能用手机照着拍的分镜脚本；回来之后按同一套结构剪，素材和故事更对得上。')}</p></div>
      <label for="pBrief" class="sr">${tr('这趟旅行')}</label>
      <textarea id="pBrief" class="field" rows="7" placeholder="${tr('比如：8 月和 James 从多伦多自驾去加拿大东海岸，三个省，想拍得轻松、有海风的感觉，结尾落在灯塔。')}">${esc(s.plan_brief || s.brief || '')}</textarea>
      <div class="set-grid" style="gap:14px;grid-template-columns:repeat(2,minmax(0,1fr))">
        <label class="section"><span class="label">${tr('拍几天')}</span>
          <input id="pDays" class="field mono" type="number" min="1" max="30" value="${esc(s.plan_days || 3)}"></label>
        <label class="section"><span class="label">${tr('设备')}</span>
          <input id="pGear" class="field" value="${esc(s.plan_gear || '')}" placeholder="${tr('比如：iPhone + DJI 无人机')}"></label>
      </div>
      <div class="section"><div class="label">${tr('成片长度')}</div>
        <div class="seg" style="grid-template-columns:repeat(4,minmax(0,1fr)) minmax(0,1.4fr)" role="group" aria-label="${tr('成片长度')}">
          ${lens.map((m) => `<button type="button" data-min="${m}" aria-pressed="${minutes === m}">${tr('{0} 分', m)}</button>`).join('')}
          <input id="sMinCustom" class="field mono" style="min-height:44px;padding:0 8px;${custom ? 'border-color:var(--accent)' : ''}" type="number" min="0.5" max="120" step="0.5" value="${custom ? esc(minutes) : ''}" placeholder="${tr('自定')}" aria-label="${tr('自定义分钟数')}">
        </div></div>
      <button type="button" class="btn" style="min-height:48px;font-size:15px" data-act="make-plan" ${dis()}>${pl ? tr('重新生成拍摄脚本') : tr('生成拍摄脚本')}</button>
      ${mockOn() ? `<p class="note" style="color:var(--accent)">${tr('现在是离线模拟：脚本是假的，只用来测试流程。')}</p>` : ''}
      ${pl ? `<p class="note">${tr('重新生成会替换现在的脚本，勾选记录和参考图也会清掉。')}</p>` : ''}
    </aside>
    <div class="content">
      ${!pl ? `<div class="empty"><h2>${tr('拍摄脚本会出现在这里')}</h2>
          <p style="margin:0;max-width:460px;line-height:1.7">${tr('每一条都写清楚拍什么、怎么构图、拍多久，以及剪辑时为什么需要它。剪不出来的“必拍”镜头会单独标出来。')}</p></div>`
      : `<div class="toolbar" style="gap:12px;align-items:flex-start">
          <div style="flex:1 1 260px"><h2 style="font-size:22px">${esc(pl.title || tr('拍摄脚本'))}</h2>
            <div class="muted" style="margin-top:2px">${esc(pl.logline || '')}</div></div>
          ${pl.has_html ? `<a class="ghost" href="${media('shootplan.html')}" target="_blank" rel="noopener">${tr('打开手机清单')}</a>
            <a class="ghost" href="${media('shootplan.html')}" download="shootplan.html">${tr('下载清单')}</a>` : ''}
        </div>
        <div class="section" id="planProgress" style="gap:6px">${planProgressHtml()}</div>
        ${(pl.beats || []).length ? `<div class="toolbar" style="gap:6px">${pl.beats.map((b, i) => `<span class="beatchip" title="${esc(b.intent || '')}"><b>${String(i + 1).padStart(2, '0')}</b>${esc(b.title || '')}</span>`).join('')}</div>` : ''}
        <div class="seg plandays" role="tablist" style="grid-template-columns:repeat(${Math.min(days.length, 7) || 1},minmax(0,1fr))">
          ${days.map((d, i) => `<button type="button" role="tab" data-planday="${i}" aria-selected="${i === S.planDay}" aria-pressed="${i === S.planDay}">${tr('第 {0} 天', d.day || i + 1)}<span style="display:block;font-size:11px;color:var(--muted)">${tr('{0} 个镜头', (d.shots || []).length)}</span></button>`).join('')}</div>
        ${day ? `<section class="section">
          <div class="dayhead"><h2>${esc(day.where || tr('第 {0} 天', day.day))}</h2></div>
          ${day.golden_hours ? `<p class="note" style="font-size:13px">${tr('光线最好的时候：')}${esc(day.golden_hours)}</p>` : ''}
          <ol class="pshots">${(day.shots || []).map((sh, si) => planShotHtml(sh, S.planDay, si)).join('')}</ol>
          <button type="button" class="ghost" style="align-self:flex-start" data-act="plan-add-shot">${tr('+ 加一个镜头')}</button>
        </section>` : ''}
        <div id="planSave">${planSaveHtml()}</div>`}
    </div>
    <aside class="detail">
      ${pl ? `<div><h2 style="font-size:18px">${tr('非拍不可')}</h2>
          <ul class="plist">${(pl.must_haves || []).map((m) => `<li>${esc(m)}</li>`).join('') || `<li class="muted">–</li>`}</ul></div>
        <div><h2 style="font-size:18px">${tr('别忘了')}</h2>
          <ul class="plist">${(pl.reminders || []).map((m) => `<li>${esc(m)}</li>`).join('') || `<li class="muted">–</li>`}</ul></div>` : ''}
      <div style="margin-top:${pl ? 'auto' : '0'};border-top:1px solid var(--line);padding-top:14px" class="section">
        <h2 style="font-size:16px">${tr('拍完之后')}</h2>
        <p class="note" style="font-size:13px">${tr('回到“素材”选择素材文件夹，AI 会逐个镜头看一遍。拍摄脚本的分幕可以直接作为故事线的起点。')}</p>
        ${pl && (pl.beats || []).length ? `<button type="button" class="ghost" data-act="plan-to-story">${tr('用这个脚本的分幕作为故事线')}</button>` : ''}
        <a class="ghost" href="#footage">${S.p.footage ? tr('去素材') : tr('选择素材文件夹')}</a>
      </div>
    </aside>
  </div>`;
}
function planChanged() {
  S.planDirty = true;
  const el = $('#planSave'); if (el) el.innerHTML = planSaveHtml();
}
async function savePlan(quiet) {
  const r = await act(() => api(`${P()}/plan_save`, { days: S.planEdit.days, title: S.planEdit.title, logline: S.planEdit.logline }));
  if (!r) return false;
  S.plan = r; planCopy(); render(); if (!quiet) toast(tr('拍摄脚本已保存'));
  return true;
}
/* 参考图：先在浏览器里缩到 1600px 以内、转成 JPG，再传给后端（原图可能有十几 MB） */
async function shrinkImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
    const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
    const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.82);
  } catch (e) {
    throw new Error(tr('读不了这张图片。iPhone 的 HEIC 照片请先转成 JPG（或者截个图再粘贴）。'));
  } finally { URL.revokeObjectURL(url); }
}
async function addPlanRefs(shotId, files) {
  files = [...files].filter((f) => f.type.startsWith('image/') || /\.(jpe?g|png|webp|gif|heic)$/i.test(f.name || ''));
  if (!files.length) { toast(tr('只能添加图片'), true); return; }
  if (S.planDirty && !(await savePlan(true))) return;        // 新加的镜头要先存进脚本，后端才认得
  for (const f of files) {
    const r = await act(async () => api(`${P()}/plan_ref`, { shot_id: shotId, data: await shrinkImage(f) }));
    if (!r) break;
    S.plan = r; planCopy();
  }
  render();
}
function pickPlanRef(shotId) {
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true;
  inp.onchange = () => { if (inp.files.length) addPlanRefs(shotId, inp.files); };
  inp.click();
}
async function newPlanProject(name) {
  let input;
  const res = await modal(`<h2>${tr('新建项目（还没有素材）')}</h2>
    <p class="muted" style="margin:0;line-height:1.6">${tr('先写拍摄脚本，回来之后再在“素材”里选择素材文件夹。')}</p>
    <label class="section"><span class="label">${tr('项目名称')}</span><input class="field" id="npName" autocomplete="off" value="${esc(name || '')}" placeholder="${tr('比如：海洋三省自驾')}"></label>
    <div class="row-end"><button class="ghost" value="">${tr('取消')}</button><button class="btn" value="ok">${tr('创建')}</button></div>`,
  (b) => { input = $('#npName', b); setTimeout(() => input.focus(), 30); });
  if (res !== 'ok' || !input.value.trim()) return;
  const r = await act(() => api('/api/projects', { name: input.value.trim() }));
  if (!r) return;
  await loadProjects();
  await switchProject(r.id);
  location.hash = '#plan';
}


/* ---------------------------------------------------------------- 4 镜头工坊 */
const LAB_TOOLS = [['fill', '补镜头'], ['slowmo', '慢动作'], ['crop', '裁切特写']];
const LAB_KIND = { fill: '补镜头', slowmo: '慢动作', crop: '裁切特写' };
function labState() {
  if (!S.lb) S.lb = { tool: 'fill', shot: null, a: 0, b: 0, t: 0, box: null, motion: 'push_in', speed: 0.5, method: 'auto', quality: 'fine',
    seconds: 5, gap: '', note: '', prompt: '', insert: 'end', faces: null, faceMethod: '', still: '', still_t: null, preview: '', busy: '',
    cr: { mode: 'fixed', keys: null, edit: 1, tight: 0.38, tracked: null, seed: null } };
  return S.lb;
}
async function loadLab() {
  try { S.lab = await api(`${P()}/lab`); } catch (e) { S.lab = { error: /not found|404/i.test(e.message) ? tr('后端还是旧版本，没有镜头工坊接口。重启 studio 后再打开。') : e.message, assets: [], gaps: [] }; }
}
/** 选中一个镜头（从素材页、时间线、或工坊里的“换一个镜头”进来） */
async function labPick(shotId, opts = {}) {
  const L = labState(), s = S.byId[shotId];
  if (!s) return;
  L.shot = shotId; L.faces = null; L.box = null; L.still = ''; L.preview = '';
  L.cr.keys = null; L.cr.tracked = null; L.cr.seed = null;
  L.a = opts.a ?? 0; L.b = opts.b ?? +s.duration.toFixed(2); L.t = opts.t ?? +(s.duration / 2).toFixed(2);
  if (opts.tool) L.tool = opts.tool;
  if (opts.insert != null) L.insert = String(opts.insert);
  if (S.page === 'lab') render();
  labLoadMedia();
}
async function labLoadMedia() {
  const L = labState(); if (!L.shot) return;
  const sid = L.shot;
  if (L.tool === 'slowmo' || L.tool === 'crop') {
    if (!L.preview) {
      L.busy = 'preview'; labMediaRender();
      const r = await act(() => api(`${P()}/lab_preview`, { shot_id: sid }));
      if (L.shot === sid) { L.preview = r ? r.url : ''; L.busy = ''; labMediaRender(); }
    }
  } else if (L.still_t !== L.t || !L.still) {
    const t = L.t;
    L.busy = 'still'; labMediaRender();
    const r = await act(() => api(`${P()}/lab_still`, { shot_id: sid, t }));
    if (L.shot === sid) { L.still = r ? r.url : ''; L.still_t = t; L.busy = ''; labMediaRender(); }
  }
}
function labAspect() { const o = (S.lab && S.lab.out) || [1920, 1080]; return o[0] / o[1]; }
function labFaceBox(f, imgAR) {
  // 让脸大约占画面高度的 38%，眼睛在上三分之一附近
  const h = Math.min(1, f.h / 0.38), w = (h * labAspect()) / imgAR;
  return cropFix({ x: f.x + f.w / 2 - w / 2, y: f.y + f.h * 0.45 - h * 0.4, w, h }, imgAR);
}
function labShotCard(s) {
  if (!s) return `<button type="button" class="lab-pick empty" data-act="lab-choose">${tr('选择一个镜头')}</button>`;
  return `<div class="lab-pick">${s.thumb ? `<img src="${esc(s.thumb)}" alt="">` : ''}
    <div style="min-width:0"><div class="mono muted" style="font-size:11px">${esc(s.id)} · ${tr('{0} 秒', (+s.duration).toFixed(1))}${s.fps ? ` · ${Math.round(s.fps)}fps` : ''}</div>
      <div class="t">${esc(s.summary || s.file || s.id)}</div></div>
    <button type="button" class="ghost sm" data-act="lab-choose">${tr('换一个')}</button></div>`;
}
function labRangeHtml(s) {
  const L = labState();
  return `<div class="set-grid" style="gap:12px;grid-template-columns:repeat(2,minmax(0,1fr))">
    <label class="section"><span class="label">${tr('从（秒）')}</span><input id="lbA" class="field mono" type="number" step="0.1" min="0" max="${s.duration}" value="${L.a}"></label>
    <label class="section"><span class="label">${tr('到（秒）')}</span><input id="lbB" class="field mono" type="number" step="0.1" min="0" max="${s.duration}" value="${L.b}"></label></div>`;
}
function labInsertHtml() {
  const L = labState(), tl = (S.story && S.story.timeline) || [];
  if (!tl.length) return `<p class="note">${tr('还没有时间线。做好的片段先留在右边，剪出时间线后再放进去。')}</p>`;
  return `<label class="section"><span class="label">${tr('做好以后')}</span>
    <select id="lbInsert" class="field">
      <option value="none" ${L.insert === 'none' ? 'selected' : ''}>${tr('先不放进时间线')}</option>
      <option value="end" ${L.insert === 'end' ? 'selected' : ''}>${tr('放到时间线最后')}</option>
      ${tl.map((t, i) => `<option value="${i + 1}" ${L.insert === String(i + 1) ? 'selected' : ''}>${esc(tr('放在 #{0} 后面：{1}', i + 1, ((S.byId[t.shot_id] || {}).summary || t.shot_id).slice(0, 24)))}</option>`).join('')}
    </select></label>`;
}
function labFormHtml() {
  const L = labState(), s = S.byId[L.shot], lb = S.lab || {};
  if (!s) return `<p class="note">${tr('先选一个镜头：补镜头用它的一帧当参考；慢动作和裁切在它上面做。')}</p>`;
  if (L.tool === 'fill') {
    const mock = lb.provider === 'mock', price = +lb.price_per_sec || 0;
    return `<label class="section"><span class="label">${tr('要补什么')}</span>
        <select id="lbGap" class="field"><option value="">${tr('自己写…')}</option>
          ${(lb.gaps || []).map((g) => `<option value="${esc(g)}" ${L.gap === g ? 'selected' : ''}>${esc(g.slice(0, 40))}</option>`).join('')}</select></label>
      <textarea id="lbNote" class="field" rows="2" placeholder="${tr('比如：开场缺一个从高处看海边小镇的远景')}">${esc(L.note || L.gap)}</textarea>
      <div class="toolbar" style="justify-content:space-between"><span class="label">${tr('给视频模型的提示词')}</span>
        <button type="button" class="ghost sm" data-act="lab-prompt" ${L.busy === 'prompt' ? 'disabled' : ''}>${L.busy === 'prompt' ? tr('正在写…') : tr('让 AI 写')}</button></div>
      <textarea id="lbPrompt" class="field mono" rows="5" style="font-size:12.5px" placeholder="${tr('英文效果最好。描述画面里有什么、怎么动、镜头怎么走。')}">${esc(L.prompt)}</textarea>
      <div class="section"><div class="label">${tr('时长')}</div>${labSeg('seconds', [[5, '5 秒'], [10, '10 秒']])}</div>
      <p class="note" style="color:${mock ? 'var(--accent)' : 'var(--text2)'}">${mock
        ? tr('现在是离线模拟：用参考帧做一个慢推，不花钱，只用来测流程。要真的生成，到设置页“镜头工坊”选视频生成服务。')
        : tr('预计花费约 ${0}（{1} 秒 × ${2}/秒，以服务商账单为准）。生成一般要 1–5 分钟。', (price * L.seconds).toFixed(2), L.seconds, price)}</p>`;
  }
  if (L.tool === 'slowmo') {
    const fps = +s.fps || 30, out = (+(S.cfg && S.cfg.settings ? S.cfg.settings.render_fps : 30)) || 30;
    const native = fps >= (out / L.speed) * 0.9;
    return `${labRangeHtml(s)}
      <div class="section"><div class="label">${tr('放慢到')}</div>${labSeg('speed', [[0.5, '½ 速'], [0.33, '⅓ 速'], [0.25, '¼ 速']])}</div>
      <div class="section"><div class="label">${tr('补帧方式')}</div>${labSeg('method', [['auto', '自动'], ['flow', '光流补帧'], ['blend', '帧混合']])}</div>
      ${!native && L.method !== 'blend' ? `<div class="section"><div class="label">${tr('光流质量')}</div>${labSeg('quality', [['fine', '标准'], ['fast', '快速']])}</div>` : ''}
      <p class="note">${native ? tr('原片是 {0}fps，直接放慢就够顺，不用补帧。', Math.round(fps))
        : tr('原片是 {0}fps，要补帧。光流补帧更顺但慢一些；帧混合快，但动作快时会有重影。', Math.round(fps)) + (L.method !== 'blend' ? ' ' + tr('“快速”大约快一半，动作复杂时偶尔有小块瑕疵。') : '')}
        <span id="lbDur">${tr('做出来约 {0} 秒，不带原声。', ((L.b - L.a) / L.speed).toFixed(1))}</span></p>`;
  }
  return cropFormHtml(s);
}
function labSeg(key, opts) {
  const L = labState();
  return `<div class="seg" role="group" style="grid-template-columns:repeat(${opts.length},minmax(0,1fr))">${opts.map(([v, l]) =>
    `<button type="button" data-lbset="${key}" data-val="${v}" aria-pressed="${String(L[key]) === String(v)}">${tr(l)}</button>`).join('')}</div>`;
}
function labMediaHtml() {
  const L = labState(), s = S.byId[L.shot];
  if (!s) return `<div class="empty"><h2>${tr('镜头工坊')}</h2><p style="margin:0;max-width:480px;line-height:1.7">${tr('在已有素材的基础上做新片段：补一个素材里没有的镜头、把一段动作放慢、或者从画面里裁出一个特写。做好的片段可以直接放进时间线。')}</p>
    <button type="button" class="btn" data-act="lab-choose">${tr('选择一个镜头')}</button></div>`;
  if (L.tool === 'crop') return cropStageHtml(s);
  if (L.tool === 'slowmo') {
    if (!L.preview) return `<div class="lab-stage"><p class="muted">${L.busy === 'preview' ? tr('正在准备预览…（第一次打开一个镜头要几秒）') : tr('预览没准备好')}</p></div>`;
    return `<div class="lab-stage"><video id="lbVideo" src="${esc(L.preview)}" controls playsinline preload="metadata"></video></div>
      <div class="toolbar" style="justify-content:center"><button type="button" class="ghost sm" data-act="lab-set-a">${tr('当前位置设为“从”')}</button>
        <button type="button" class="ghost sm" data-act="lab-set-b">${tr('当前位置设为“到”')}</button>
        <button type="button" class="ghost sm" data-act="lab-play-range">${tr('播放选中的这段')}</button></div>`;
  }
  const img = s.size && s.size[0] ? s.size : [16, 9];
  return `<div class="lab-stage"><div class="lab-frame" style="--ar:${img[0] / img[1]}">
      ${L.still ? `<img src="${esc(L.still)}" alt="" draggable="false">` : `<span class="muted">${L.busy === 'still' ? tr('正在取这一帧…') : ''}</span>`}
    </div></div>
    <div class="lab-scrub"><span class="mono">${tr('取哪一帧')}</span><input id="lbT" type="range" min="0" max="${s.duration}" step="0.1" value="${L.t}"><span class="mono" id="lbTVal">${(+L.t).toFixed(1)}s</span></div>`;
}
function labMediaRender() { const el = $('#lbMedia'); if (el) { el.innerHTML = labMediaHtml(); cropMount(); } }
/** 自动起的名字按界面语言显示；你自己写的（或补镜头时写的缺口）原样显示 */
function labLabel(a) {
  if (!a.auto_label) return a.label;
  const pr = a.params || {};
  const what = a.kind === 'slowmo' ? `×${pr.speed}` : a.kind === 'crop' ? tr({ fixed: '固定', push: '推拉', track: '跟随人脸', static: '固定', push_in: '推拉', pull_out: '推拉' }[pr.mode || pr.motion] || '') : '';
  return `${tr(LAB_KIND[a.kind] || a.kind)}${what ? ' ' + what : ''} · ${a.src_shot || ''}`;
}
function labAssetHtml(a) {
  return `<div class="lab-asset">
    <video src="${esc(a.url)}" poster="${esc(a.thumb_url || '')}" controls preload="none" playsinline></video>
    <div class="toolbar" style="gap:6px;align-items:baseline"><span class="tag-k ${esc(a.kind)}">${tr(LAB_KIND[a.kind] || a.kind)}</span>
      <b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(labLabel(a))}">${esc(labLabel(a))}</b>
      <span class="mono muted" style="font-size:12px">${(+a.duration).toFixed(1)}s</span></div>
    <div class="toolbar" style="gap:6px">
      ${a.in_timeline ? `<span class="note ok-text">${tr('✓ 已在时间线里')}</span>` : S.lab && S.lab.has_story ? `<button type="button" class="ghost sm" data-labins="${esc(a.id)}">${tr('放到时间线最后')}</button>` : ''}
      ${a.kind === 'fill' && a.params && a.params.provider === 'mock' ? `<span class="note">${tr('离线模拟')}</span>` : ''}
      <button type="button" class="ghost sm danger" style="margin-left:auto" data-labdel="${esc(a.id)}" ${a.in_timeline ? 'disabled title="' + esc(tr('先在时间线里删掉它')) + '"' : ''}>${tr('删除')}</button></div></div>`;
}
function viewLab() {
  const L = labState(), lb = S.lab;
  if (!lb) { setTimeout(async () => { await loadLab(); if (S.page === 'lab') render(); }, 0); return `<div class="page"><div class="content"><p class="muted">${tr('正在读取…')}</p></div></div>`; }
  if (lb.error) return `<div class="page"><div class="content"><div class="empty"><h2>${tr('打不开镜头工坊')}</h2><p class="err" style="margin:0">${esc(lb.error)}</p></div></div></div>`;
  const s = S.byId[L.shot];
  const label = { fill: tr('生成补镜头'), slowmo: tr('做慢动作'), crop: tr('裁切') }[L.tool];
  return `<div class="page">
    <aside class="side wide">
      <div><h1 style="font-size:24px;margin-bottom:6px">${tr('镜头工坊')}</h1>
        <p class="muted" style="margin:0;line-height:1.6">${tr('补镜头、慢动作、裁切特写。原片不会被改动，做出来的都是新片段。')}</p></div>
      <div class="seg" role="tablist" style="grid-template-columns:repeat(3,minmax(0,1fr))">${LAB_TOOLS.map(([v, l]) =>
        `<button type="button" role="tab" data-labtool="${v}" aria-selected="${L.tool === v}" aria-pressed="${L.tool === v}">${tr(l)}</button>`).join('')}</div>
      <div class="section"><div class="label">${L.tool === 'fill' ? tr('参考镜头') : tr('在哪个镜头上做')}</div>${labShotCard(s)}</div>
      <div class="section" id="lbForm" style="gap:12px">${labFormHtml()}</div>
      ${s ? labInsertHtml() : ''}
      ${s ? `<button type="button" class="btn" style="min-height:48px;font-size:15px" data-act="lab-make" ${dis()}>${label}</button>` : ''}
    </aside>
    <div class="content" id="lbMedia">${labMediaHtml()}</div>
    <aside class="detail">
      <div class="toolbar" style="justify-content:space-between"><h2 style="font-size:18px">${tr('做好的片段')}</h2><span class="muted mono" style="font-size:12px">${lb.assets.length}</span></div>
      ${lb.assets.length ? lb.assets.map(labAssetHtml).join('') : `<p class="muted" style="margin:0">${tr('还没有。左边选好以后点按钮，做好的片段会出现在这里。')}</p>`}
      <p class="help" style="margin-top:auto">${tr('片段存在项目文件夹的 lab/ 里。在时间线、预览渲染和导出的 XML 里都当普通镜头用。')}</p>
    </aside>
  </div>`;
}
/** 选镜头的小窗口：缩略图 + 搜索 */
function pickShot(current) {
  const list = S.shots.filter((s) => s.frames && s.frames.length);
  const grid = (q) => list.filter((s) => !q || `${s.id} ${s.summary} ${s.file} ${(s.tags || []).join(' ')}`.toLowerCase().includes(q))
    .map((s) => `<button type="button" class="pick-item ${s.id === current ? 'sel' : ''}" data-pick="${esc(s.id)}">
      <img src="${esc(s.thumb)}" alt="" loading="lazy"><span>${esc((s.summary || s.file || s.id).slice(0, 40))}</span>
      <small class="mono">${esc(s.id)} · ${(+s.duration).toFixed(1)}s</small></button>`).join('') || `<p class="muted">${tr('没有符合的镜头')}</p>`;
  return modal(`<h2>${tr('选择一个镜头')}</h2>
    <input id="pkQ" class="field" type="search" placeholder="${tr('搜索：灯塔、日落、镜头 id…')}" autocomplete="off">
    <div class="pick-grid" id="pkGrid">${grid('')}</div>
    <div class="row-end"><button class="ghost" value="">${tr('取消')}</button></div>`, (b, close) => {
    $('#pkQ', b).addEventListener('input', (e) => { $('#pkGrid', b).innerHTML = grid(e.target.value.trim().toLowerCase()); });
    b.addEventListener('click', (e) => { const it = e.target.closest('[data-pick]'); if (it) close(it.dataset.pick); });
    setTimeout(() => $('#pkQ', b).focus(), 30);
  });
}
async function labMake() {
  const L = labState(), s = S.byId[L.shot]; if (!s) return;
  const body = { kind: L.tool, shot_id: L.shot, mock: mockOn() };
  const tl = (S.story && S.story.timeline) || [];
  if (tl.length && L.insert !== 'none') body.insert_at = L.insert === 'end' ? tl.length : +L.insert;
  if (L.tool === 'fill') {
    if (!L.prompt.trim()) { toast(tr('先写提示词，或者点“让 AI 写”'), true); return; }
    Object.assign(body, { t: L.t, prompt: L.prompt.trim(), seconds: L.seconds, gap: (L.note || L.gap || '').trim() });
    const lb = S.lab || {};
    if (lb.provider !== 'mock' && !mockOn()) {
      if (!lb.fal_key || !lb.fal_key.has_key) { toast(tr('还没有填视频生成服务的 Key，到设置页“镜头工坊”填写'), true); return; }
      const cost = ((+lb.price_per_sec || 0) * L.seconds).toFixed(2);
      if (!(await confirmBox(tr('开始生成？'), tr('会调用 {0}，生成 {1} 秒，预计花费约 ${2}（以服务商账单为准）。', lb.model, L.seconds, cost), tr('生成')))) return;
    }
  } else {
    if (!(L.b - L.a >= 0.3)) { toast(tr('“到”要比“从”晚至少 0.3 秒'), true); return; }
    Object.assign(body, { in: L.a, out: L.b });
    if (L.tool === 'slowmo') Object.assign(body, { speed: L.speed, method: L.method, quality: L.quality });
    else {
      const C = L.cr;
      if (C.mode === 'track' && !C.tracked) { toast(tr('先点“开始跟随”，看一下效果再裁切'), true); return; }
      cropEnsure();
      body.keys = (C.keys || []).map((k) => ({ t: k.t, x: k.x, y: k.y, w: k.w, h: k.h }));
    }
  }
  startJob(`${P()}/lab_make`, body);
}


/* ---- 裁切特写：在预览视频上框选，三种方式——固定 / 推拉（起点框 → 终点框）/ 跟随人脸 */
const CROP_MODES = [['fixed', '固定'], ['push', '推拉'], ['track', '跟随人脸']];
function cropImgAR() {
  const L = labState(), s = S.byId[L.shot], v = $('#lbVideo');
  if (v && v.videoWidth) return v.videoWidth / v.videoHeight;
  return s && s.size && s.size[0] ? s.size[0] / s.size[1] : 16 / 9;
}
/** 画面里能放下的最大输出比例区域（0-1） */
function cropFull(imgAR, cx = 0.5, cy = 0.5) {
  const ar = labAspect();
  let w = 1, h = imgAR / ar;
  if (h > 1) { h = 1; w = ar / imgAR; }
  return { x: Math.min(Math.max(0, cx - w / 2), 1 - w), y: Math.min(Math.max(0, cy - h / 2), 1 - h), w, h };
}
/** 修正成输出宽高比、不超出画面；以框中心为准 */
function cropFix(b, imgAR) {
  const full = cropFull(imgAR), ar = labAspect();
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  let w = Math.min(Math.max(0.04, b.w), full.w), h = (w * imgAR) / ar;
  if (h > full.h) { h = full.h; w = (h * ar) / imgAR; }
  return { x: Math.min(Math.max(0, cx - w / 2), 1 - w), y: Math.min(Math.max(0, cy - h / 2), 1 - h), w, h };
}
function cropBoxAt(keys, t) {
  if (!keys || !keys.length) return null;
  if (t <= keys[0].t) return keys[0];
  if (t >= keys[keys.length - 1].t) return keys[keys.length - 1];
  for (let i = 0; i < keys.length - 1; i++) {
    const k0 = keys[i], k1 = keys[i + 1];
    if (t >= k0.t && t <= k1.t) {
      let p = (t - k0.t) / Math.max(1e-6, k1.t - k0.t);
      if (keys.length === 2) p = p * p * (3 - 2 * p);
      return { t, x: k0.x + (k1.x - k0.x) * p, y: k0.y + (k1.y - k0.y) * p, w: k0.w + (k1.w - k0.w) * p, h: k0.h + (k1.h - k0.h) * p };
    }
  }
  return keys[keys.length - 1];
}
/** 保证当前模式有合适的关键帧（切换模式、改了起止时间时调用） */
function cropEnsure() {
  const L = labState(), C = L.cr, ar = cropImgAR();
  const mid = cropFix({ x: 0.3, y: 0.25, w: 0.4, h: 0.4 }, ar);
  const cur = (C.keys && C.keys.length && C.mode !== 'track') ? C.keys[C.keys.length - 1] : null;
  if (C.mode === 'fixed') C.keys = [{ t: L.a, ...(cur ? cropFix(cur, ar) : mid) }];
  else if (C.mode === 'push') {
    if (!C.keys || C.keys.length !== 2 || C.tracked) {
      const end = cur ? cropFix(cur, ar) : mid;
      C.keys = [{ t: L.a, ...cropFull(ar, end.x + end.w / 2, end.y + end.h / 2) }, { t: L.b, ...end }];
    }
    C.keys[0].t = L.a; C.keys[1].t = L.b;
  } else if (!C.tracked) {
    C.seed = C.seed || (cur ? cropFix(cur, ar) : mid);
    C.keys = null;
  }
}
/** 现在可以拖的那个框：固定 → 唯一的框；推拉 → 正在编辑的起点或终点；跟随 → 还没跟随前用来选脸的框 */
function cropEditable() {
  const C = labState().cr;
  if (C.mode === 'fixed') return C.keys && C.keys[0];
  if (C.mode === 'push') return C.keys && C.keys[C.edit];
  return C.tracked ? null : C.seed;
}
function cropSetEditable(box, raw) {
  const C = labState().cr, b = raw ? box : cropFix(box, cropImgAR());
  if (C.mode === 'track') { C.seed = b; C.tracked = null; C.keys = null; return; }
  const k = cropEditable(); if (k) Object.assign(k, b);
}
/** 视频当前时刻应该显示的框 */
function cropShownBox() {
  const L = labState(), C = L.cr, v = $('#lbVideo');
  const t = v ? L.a + Math.max(0, v.currentTime - L.a) : L.a;
  const playing = v && !v.paused;
  if (C.mode === 'track') return C.tracked ? cropBoxAt(C.keys, v ? v.currentTime : L.a) : C.seed;
  if (C.mode === 'push' && playing) return cropBoxAt(C.keys, t);
  return cropEditable();
}
function cropFormHtml(s) {
  const L = labState(), C = L.cr;
  const minW = C.mode === 'track' ? (C.keys ? Math.min(...C.keys.map((k) => k.w)) : 1) : Math.min(...((C.keys || []).map((k) => k.w).concat([1])));
  const px = Math.round(minW * ((s.size && s.size[0]) || 1920)), need = labAspect() > 1 ? 1920 : 1080;
  let body = '';
  if (C.mode === 'fixed') body = `<p class="note">${tr('拖动画面上的框选位置，拖右下角的圆点改大小。宽高比按成片画幅锁定。')}</p>`;
  else if (C.mode === 'push') body = `<div class="seg" role="group" style="grid-template-columns:1fr 1fr">
      <button type="button" data-cropedit="0" aria-pressed="${C.edit === 0}">${tr('起点框')}<span class="sub">${(+L.a).toFixed(1)}s</span></button>
      <button type="button" data-cropedit="1" aria-pressed="${C.edit === 1}">${tr('终点框')}<span class="sub">${(+L.b).toFixed(1)}s</span></button></div>
    <div class="toolbar" style="gap:6px">
      <button type="button" class="ghost sm" data-croppreset="in">${tr('全景 → 特写')}</button>
      <button type="button" class="ghost sm" data-croppreset="out">${tr('特写 → 全景')}</button>
      <button type="button" class="ghost sm" data-croppreset="same">${tr('终点 = 起点')}</button></div>
    <p class="note">${tr('点“起点框 / 终点框”切换要调的框，画面会跳到那一刻。按播放看推拉效果。')}</p>`;
  else body = `<div class="set-row" style="gap:6px"><div class="lbl" style="display:flex;justify-content:space-between"><span>${tr('脸占画面高度')}</span><span class="mono" id="cropTightVal" style="color:var(--accent)">${Math.round(C.tight * 100)}%</span></div>
      <input id="cropTight" type="range" min="0.2" max="0.6" step="0.02" value="${C.tight}" style="accent-color:var(--accent)"></div>
    <button type="button" class="ghost outline" data-act="crop-track" ${L.busy === 'track' ? 'disabled' : ''}>${L.busy === 'track' ? tr('正在跟随…') : C.tracked ? tr('重新跟随') : tr('开始跟随')}</button>
    <p class="note">${C.tracked ? tr('{0} 帧里跟上了 {1} 帧。按播放看效果；不满意就把框拖到那张脸上再点“重新跟随”。', C.tracked.samples, C.tracked.found)
      : tr('画面里有几个人时，先把框拖到要跟的那张脸上（可以先点“自动找脸”），再点“开始跟随”。')}</p>`;
  return `${labRangeHtml(s)}
    <div class="section"><div class="label">${tr('方式')}</div><div class="seg" role="group" style="grid-template-columns:repeat(3,minmax(0,1fr))">${CROP_MODES.map(([v, l]) =>
      `<button type="button" data-cropmode="${v}" aria-pressed="${C.mode === v}">${tr(l)}</button>`).join('')}</div></div>
    ${body}
    ${C.mode !== 'track' || !C.tracked ? `<div class="toolbar"><button type="button" class="ghost sm" data-act="lab-faces" ${L.busy === 'faces' ? 'disabled' : ''}>${L.busy === 'faces' ? tr('正在找…') : tr('自动找脸')}</button>
      <span class="note">${L.faces == null ? '' : L.faces.length ? tr('找到 {0} 张脸，点一下虚线框就用它。', L.faces.length) : tr('没找到清楚的正脸，手动框一下。')}</span></div>` : ''}
    ${px < need * 0.35 ? `<p class="note" style="color:var(--accent)">${tr('框出来的区域只有约 {0} 像素宽，放大后会比较糊。', px)}</p>` : ''}`;
}
function cropStageHtml(s) {
  const L = labState();
  if (!L.preview) return `<div class="lab-stage"><p class="muted">${L.busy === 'preview' ? tr('正在准备预览…（第一次打开一个镜头要几秒）') : tr('预览没准备好')}</p></div>`;
  const ar = s.size && s.size[0] ? s.size[0] / s.size[1] : 16 / 9, oar = labAspect();
  return `<div class="lab-stage crop">
      <div class="cropstage" style="--ar:${ar}">
        <video id="lbVideo" src="${esc(L.preview)}" muted playsinline preload="auto"></video>
        <div class="cropshade" id="cropLayer">
          ${(L.faces || []).map((f, i) => `<button type="button" class="lab-face" data-face="${i}" title="${tr('用这张脸')}" style="left:${f.x * 100}%;top:${f.y * 100}%;width:${f.w * 100}%;height:${f.h * 100}%"></button>`).join('')}
          <div class="lab-box" id="lbBox"><span class="lab-box-tag" id="cropTag"></span><i class="lab-handle" data-handle="1"></i></div>
        </div>
      </div>
    </div>
    <div class="crop-transport">
      <button type="button" class="ghost sm" data-act="crop-play" id="cropPlay" aria-label="${tr('播放')}">▶</button>
      <input id="cropTime" type="range" min="${L.a}" max="${L.b}" step="0.01" value="${L.a}">
      <span class="mono" id="cropTimeVal">${(+L.a).toFixed(2)}s</span>
    </div>
    <div class="crop-result"><div class="label">${tr('成片效果')}</div>
      <canvas id="cropCanvas" width="${oar >= 1 ? 480 : 270}" height="${oar >= 1 ? Math.round(480 / oar) : 480}"></canvas></div>`;
}
let cropRAF = 0;
function cropDraw() {
  const L = labState(), C = L.cr, v = $('#lbVideo'), el = $('#lbBox'); if (!v || !el) return;
  const b = cropShownBox();
  const tag = $('#cropTag');
  if (b) {
    Object.assign(el.style, { display: '', left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` });
    const locked = !cropEditable() || (C.mode === 'push' && !v.paused);
    el.classList.toggle('locked', locked);
    if (tag) tag.textContent = C.mode === 'push' ? (v.paused ? tr(C.edit ? '终点框' : '起点框') : '') : C.mode === 'track' && C.tracked ? tr('跟随中') : '';
  } else el.style.display = 'none';
  const tv = $('#cropTime'), tl = $('#cropTimeVal');
  if (tv && document.activeElement !== tv) tv.value = v.currentTime;
  if (tl) tl.textContent = `${v.currentTime.toFixed(2)}s`;
  const play = $('#cropPlay'); if (play) play.textContent = v.paused ? '▶' : '❚❚';
  const cv = $('#cropCanvas');
  if (cv && b && v.videoWidth) {
    const g = cv.getContext('2d');
    g.imageSmoothingQuality = 'high';
    try { g.drawImage(v, b.x * v.videoWidth, b.y * v.videoHeight, b.w * v.videoWidth, b.h * v.videoHeight, 0, 0, cv.width, cv.height); } catch (e) { /* 还没解码出画面 */ }
  }
}
function cropLoop() { cancelAnimationFrame(cropRAF); const v = $('#lbVideo'); cropDraw(); if (v && !v.paused) cropRAF = requestAnimationFrame(cropLoop); }
/** 视频元素插进页面后挂上事件 */
function cropMount() {
  const L = labState(), v = $('#lbVideo');
  if (!v || L.tool !== 'crop' || v.dataset.mounted) return;
  v.dataset.mounted = '1';
  const seekTo = (t) => { try { v.currentTime = Math.min(Math.max(L.a, t), L.b); } catch (e) { /* 还没加载 */ } };
  v.addEventListener('loadedmetadata', () => { cropEnsure(); seekTo(L.cr.mode === 'push' && L.cr.edit === 1 ? L.b : L.a); const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml(); });
  v.addEventListener('seeked', cropDraw);
  v.addEventListener('loadeddata', cropDraw);
  v.addEventListener('play', cropLoop);
  v.addEventListener('pause', cropDraw);
  v.addEventListener('timeupdate', () => { if (v.currentTime >= L.b - 0.02) { v.pause(); } });
  if (v.readyState >= 1) { cropEnsure(); seekTo(L.a); }
}
async function cropTrack() {
  const L = labState(), C = L.cr;
  L.busy = 'track'; $('#lbForm').innerHTML = labFormHtml();
  const v = $('#lbVideo');
  const r = await act(() => api(`${P()}/lab_track`, { shot_id: L.shot, in: L.a, out: L.b, seed: C.seed, seed_t: v ? v.currentTime : L.a, tight: C.tight, mock: mockOn() }));
  L.busy = '';
  if (r) { C.keys = r.keys; C.tracked = { samples: r.samples, found: r.found, method: r.method }; L.faces = null; document.querySelectorAll('.lab-face').forEach((x) => x.remove()); }
  const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml();
  cropDraw();
}
/* 拖动 / 缩放框（只在框可编辑时） */
let cropDrag = null;
document.addEventListener('pointerdown', (e) => {
  const box = e.target.closest && e.target.closest('#lbBox'); if (!box || box.classList.contains('locked')) return;
  const b = cropEditable(); if (!b) return;
  const fr = $('#cropLayer').getBoundingClientRect();
  cropDrag = { resize: !!e.target.dataset.handle, x0: e.clientX, y0: e.clientY, b0: { x: b.x, y: b.y, w: b.w, h: b.h }, fr };
  box.setPointerCapture(e.pointerId); e.preventDefault();
});
document.addEventListener('pointermove', (e) => {
  if (!cropDrag) return;
  const dx = (e.clientX - cropDrag.x0) / cropDrag.fr.width, dy = (e.clientY - cropDrag.y0) / cropDrag.fr.height, b = cropDrag.b0;
  if (cropDrag.resize) {                     // 以左上角为锚点改大小，宽高比锁定
    const ar = cropImgAR(), oar = labAspect();
    let w = Math.max(0.04, b.w + dx);
    w = Math.min(w, 1 - b.x, ((1 - b.y) * oar) / ar);
    cropSetEditable({ x: b.x, y: b.y, w, h: (w * ar) / oar }, true);
  } else cropSetEditable({ ...b, x: b.x + dx, y: b.y + dy });
  cropDraw();
});
document.addEventListener('pointerup', () => { if (cropDrag) { cropDrag = null; const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml(); } });
document.addEventListener('click', (e) => {
  const t = e.target.closest && e.target.closest('[data-cropmode],[data-cropedit],[data-croppreset]'); if (!t) return;
  const L = labState(), C = L.cr, v = $('#lbVideo'), ar = cropImgAR();
  if (t.dataset.cropmode) { if (C.mode !== t.dataset.cropmode) { C.mode = t.dataset.cropmode; if (C.mode !== 'track') C.tracked = null; cropEnsure(); } if (v) { v.pause(); v.currentTime = L.a; } }
  if (t.dataset.cropedit) { C.edit = +t.dataset.cropedit; if (v) { v.pause(); v.currentTime = C.edit ? Math.max(L.a, L.b - 0.03) : L.a; } }
  if (t.dataset.croppreset && C.keys && C.keys.length === 2) {
    const [k0, k1] = C.keys, p = t.dataset.croppreset;
    const tight = C.edit === 0 ? k0 : k1, isFull = (k) => k.w >= cropFull(ar).w - 0.001;
    const close = isFull(tight) ? (isFull(k0) ? k1 : k0) : tight;
    const full = cropFull(ar, close.x + close.w / 2, close.y + close.h / 2);
    if (p === 'in') { Object.assign(k0, full); Object.assign(k1, cropFix(close, ar)); }
    if (p === 'out') { Object.assign(k0, cropFix(close, ar)); Object.assign(k1, full); }
    if (p === 'same') Object.assign(k1, { x: k0.x, y: k0.y, w: k0.w, h: k0.h });
  }
  const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml();
  cropDraw();
});
document.addEventListener('input', (e) => {
  const t = e.target;
  if (t.id === 'cropTime') { const v = $('#lbVideo'); if (v) { v.pause(); v.currentTime = +t.value; } return; }
  if (t.id === 'cropTight') { const C = labState().cr; C.tight = +t.value; const el = $('#cropTightVal'); if (el) el.textContent = `${Math.round(C.tight * 100)}%`; }
});

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
  if (f.prot && !s.protected) return false;
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
      <span class="name">${tr(name)}</span><span class="v">${val}</span></div>`;
  };
  const left = p.shots - p.logged;
  let btn;
  if (scanning || analyzing) btn = `<button type="button" class="btn" disabled>${scanning ? tr('扫描中…') : tr('看片中…')}</button>`;
  else if (!p.clips) btn = '';
  else if (!p.shots || left > 0) btn = `<button type="button" class="btn" data-act="analyze" ${dis()}>${!p.shots ? tr('开始分析') : tr('继续看片（还剩 {0} 个镜头）', left)}</button>`;
  else btn = `<button type="button" class="ghost" data-act="analyze" ${dis()}>${tr('重新分析（只补新加的镜头）')}</button>`;
  return `<div class="label">${tr('处理进度')}</div>
    ${row(0, '扫描元数据', p.clips > 0, p.clips || '–')}
    ${row(1, '切镜头 · 抽帧 · 打分', p.shots > 0, p.shots ? `${p.shots}${p.unusable ? ` · ${tr('{0} 废片', p.unusable)}` : ''}` : '–')}
    <div class="section" style="gap:8px">${row(3, 'AI 看片', p.shots > 0 && p.logged >= p.shots, p.shots ? `${p.logged} / ${p.shots}` : '–')}
      <div class="bar" style="margin-left:32px"><i style="width:${pct}%"></i></div></div>
    <label class="section" style="gap:6px"><span class="label">${tr('这部片子的主人公')}</span>
      <input id="fSubject" class="field" value="${esc((p.settings || {}).subject || '')}" placeholder="${tr('比如：我（长发女生）和男朋友 James')}">
      <span class="note">${tr('AI 会据此判断哪些镜头有主人公的正脸、两人同框，这些镜头剪片时一定保留。')}${p.logged ? ' ' + tr('改了以后，只对还没看过的镜头生效。') : ''}</span></label>
    <label class="check" style="min-height:32px;font-size:13px"><input type="checkbox" id="fWhisper" ${S.whisper ? 'checked' : ''}>${tr('同时转写人声（需要 faster-whisper）')}</label>
    ${btn}
    ${mockOn() ? `<p class="note" style="color:var(--accent)">${tr('现在是离线模拟：AI 日志是假的，只用来测试流程。')}</p>` : ''}
    <p class="note">${tr('原片始终留在本机，只上传 512px 静帧和文字。中途停了也没关系，已完成的部分不会重复计费。')}</p>`;
}

function cardHtml(s) {
  return `<button type="button" class="card ${s.id === S.fsel ? 'sel' : ''} ${s.excluded ? 'excluded' : ''}" data-shot="${esc(s.id)}" aria-label="${esc(s.summary || s.file)}">
    <div class="thumb ${s.vertical ? 'contain' : ''}">${s.thumb ? `<img loading="lazy" src="${s.thumb}" alt="">` : ''}
      ${(s.highlight || 0) >= 8 ? `<span class="tag star">${tr('★ 精彩')}</span>` : ''}
      ${s.must ? `<span class="tag must">${tr('必用')}</span>` : s.excluded ? `<span class="tag must" style="color:var(--danger)">${tr('已排除')}</span>` : ''}
      ${s.protected && !s.must && !s.excluded ? `<span class="tag prot" title="${esc(tr('受保护：{0}', tr(s.protected)))}">${SHIELD}${tr('保护')}</span>` : ''}
      <span class="tag dur">${mmss(s.duration)}</span></div>
    <div class="body"><div class="t">${esc(s.summary || tr('AI 还没看过这个镜头'))}</div>
      <div class="m">${esc([shotType(s.shot_type), s.mood, s.file].filter(Boolean).join(' · '))}</div></div></button>`;
}

function gridHtml() {
  if (!S.shots.length && !S.p.footage) {
    return `<div class="empty"><h2>${tr('还没有素材')}</h2>
      <p style="margin:0;max-width:440px;line-height:1.7">${tr('拍完之后，在左边选择素材文件夹。出发前可以先去“拍摄脚本”想好要拍什么。')}</p>
      <a class="ghost" href="#plan">${tr('去写拍摄脚本')}</a></div>`;
  }
  if (!S.shots.length) {
    return `<div class="empty"><h2>${S.p.clips ? tr('找到 {0} 条素材', S.p.clips) : tr('还没有镜头')}</h2>
      <p style="margin:0;max-width:440px;line-height:1.7">${tr('点左边的“开始分析”：切镜头、抽关键帧、打质量分，然后 AI 逐个镜头写日志——内容、情绪、景别和最佳片段。')}</p></div>`;
  }
  const days = shotDays().filter((d) => S.day === 'all' || String(d.day) === String(S.day));
  let shown = 0;
  const html = days.map((d) => {
    const list = d.shots.filter(shotMatches);
    shown += list.length;
    if (!list.length) return '';
    return `<section class="section"><div class="dayhead"><h2>${tr('第 {0} 天', d.day)}</h2><span class="muted">${d.date} · ${tr('{0} 个镜头', list.length)}</span></div>
      <div class="grid">${list.map(cardHtml).join('')}</div></section>`;
  }).join('');
  return shown ? html : `<div class="empty">${tr('没有符合筛选条件的镜头')}</div>`;
}

function detailHtml() {
  const s = S.byId[S.fsel];
  if (!s) return `<div class="label">${tr('镜头日志')}</div><p class="muted" style="margin:0">${tr('点一个镜头，看 AI 写的日志。')}</p>`;
  const frames = s.frames || [];
  const fi = S.fframe ?? Math.floor(frames.length / 2);
  const bm = s.best_moment;
  const best = bm ? tr('{0}–{1} 秒', (+bm.start).toFixed(1), (+bm.end).toFixed(1)) : '–';
  const gps = s.gps ? `${(+s.gps[0]).toFixed(3)}, ${(+s.gps[1]).toFixed(3)}` : '–';
  return `<div class="label">${tr('镜头日志')}</div>
    <div class="thumb big ${s.vertical ? 'contain' : ''}">${frames.length ? `<img src="${frames[fi]}" alt="${esc(s.summary)}">` : ''}
      ${bm ? `<span class="tag dur" style="left:10px;right:auto">${tr('最佳片段 {0}', best)}</span>` : ''}</div>
    ${frames.length > 1 ? `<div class="strip">${frames.map((u, i) => `<button type="button" data-frame="${i}" aria-pressed="${i === fi}" aria-label="${tr('第 {0} 帧', i + 1)}"><img src="${u}" alt=""></button>`).join('')}</div>` : ''}
    <div><div class="mono muted" style="font-size:12px">${esc(s.id)} · ${esc(s.file)}</div>
      <h3 style="font-size:19px;margin-top:4px;line-height:1.45">${esc(s.summary || tr('AI 还没看过这个镜头'))}</h3></div>
    <dl class="kv">
      <dt>${tr('景别')}</dt><dd>${esc(shotType(s.shot_type) || '–')}</dd>
      <dt>${tr('情绪')}</dt><dd>${esc(s.mood || '–')}</dd>
      <dt>${tr('精彩度')}</dt><dd>${s.highlight != null ? esc(s.highlight) + ' / 10' : '–'}</dd>
      <dt>${tr('地点')}</dt><dd>${esc(s.place || '–')}</dd>
      <dt>${tr('画质')}</dt><dd>${s.quality != null ? `${(+s.quality).toFixed(2)}${s.quality < badQ() ? tr('（偏模糊或过暗）') : ''}` : '–'}</dd>
      <dt>${tr('拍摄')}</dt><dd>${tr('第 {0} 天', s.day)} ${esc(s.time)} · ${mmss(s.duration)}</dd>
      <dt>GPS</dt><dd>${gps}</dd>
      <dt>${tr('对白')}</dt><dd>${esc(s.speech || tr('无'))}</dd>
      <dt>${tr('标签')}</dt><dd>${esc((s.tags || []).join(tr('、')) || '–')}</dd>
      <dt>${tr('保护')}</dt><dd>${protectText(s)}</dd>
      <dt>${tr('AI 判断')}</dt><dd>${signalsHtml(s)}</dd>
    </dl>
    <div class="toolbar" style="margin-top:auto">
      <button type="button" class="ghost ${s.must ? 'outline' : ''}" style="flex:1" data-flag="must" aria-pressed="${s.must}">${s.must ? tr('✓ 必用') : tr('标为必用')}</button>
      <button type="button" class="ghost ${s.excluded ? 'danger' : ''}" style="flex:1" data-flag="exclude" aria-pressed="${s.excluded}">${s.excluded ? tr('已排除（点击恢复）') : tr('排除这个镜头')}</button>
    </div>
    ${(s.protected && !s.must) || s.unprotected ? `<button type="button" class="ghost sm" data-flag="unprotect" aria-pressed="${!!s.unprotected}">${s.unprotected ? tr('恢复自动保护') : tr('不保护这个镜头')}</button>` : ''}
    <div class="toolbar" style="gap:6px"><span class="label" style="margin-right:4px">${tr('镜头工坊')}</span>
      <button type="button" class="ghost sm" data-labgo="slowmo" data-shot-id="${esc(s.id)}">${tr('慢动作')}</button>
      <button type="button" class="ghost sm" data-labgo="crop" data-shot-id="${esc(s.id)}">${tr('裁切特写')}</button>
      <button type="button" class="ghost sm" data-labgo="fill" data-shot-id="${esc(s.id)}">${tr('用它补镜头')}</button></div>`;
}
function protectText(s) {
  if (s.excluded) return esc(tr('已排除，AI 不会用'));
  if (s.protected) return `<span class="prot-text">${SHIELD} ${esc(tr(s.protected))}</span><div class="note">${tr('剪片时一定保留；AI 漏掉会自动补回。')}</div>`;
  if (s.unprotected) return esc(tr('已关闭自动保护'));
  return esc(tr('不会被自动保护'));
}
function signalsHtml(s) {
  if (s.faces_clear == null && s.subject_present == null && s.people_together == null && s.scenery_value == null) return '–';
  const on = (v, label) => `<span class="sig ${v ? 'on' : ''}">${v ? '✓' : '·'} ${esc(tr(label))}</span>`;
  return `${on(s.faces_clear, '清晰正脸')}${on(s.subject_present, '主人公在场')}${on(s.people_together, '多人同框')}<span class="sig ${(s.scenery_value || 0) >= 8 ? 'on' : ''}">${esc(tr('风景 {0}/10', s.scenery_value ?? '–'))}</span>`;
}

function dayNavHtml() {
  const items = [{ k: 'all', n: tr('全部'), c: S.shots.length }].concat(shotDays().map((d) => ({ k: String(d.day), n: `${tr('第 {0} 天', d.day)} · ${d.date}`, c: d.shots.length })));
  return items.map((i) => `<button type="button" data-day="${i.k}" aria-pressed="${String(S.day) === i.k}"><span>${esc(i.n)}</span><span class="c">${i.c}</span></button>`).join('');
}

function viewFootage() {
  const p = S.p, f = S.filters;
  const chip = (k, label) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${f[k]}">${tr(label)}</button>`;
  return `<div class="page">
    <aside class="side">
      <section class="section">
        <div class="label">${tr('素材文件夹')}</div>
        <div class="path">${esc(p.footage || '–')}</div>
        <button type="button" class="${p.footage ? 'ghost' : 'btn'}" data-act="rescan" ${dis()}>${p.footage ? tr('更换文件夹 / 重新扫描') : tr('选择素材文件夹')}</button>
        <div class="stats" style="margin-top:4px"><div><b>${p.clips}</b><span>${tr('文件')}</span></div><div><b>${p.shots}</b><span>${tr('镜头')}</span></div><div><b>${mmss(p.duration_min * 60)}</b><span>${tr('总时长')}</span></div></div>
      </section>
      <section class="section" id="fProgress">${progressHtml()}</section>
      ${S.shots.length ? `<section class="section"><div class="label">${tr('按天浏览')}</div><div class="daynav" id="fDays">${dayNavHtml()}</div></section>` : ''}
    </aside>
    <div class="content">
      ${S.shots.length ? `<div class="toolbar">
        <label for="fSearch" class="sr">${tr('搜索镜头日志')}</label>
        <input id="fSearch" class="field" type="search" placeholder="${tr('搜索镜头日志：灯塔、退潮、车里聊天…')}" value="${esc(f.q)}">
        ${chip('star', '★ 精彩')}${chip('prot', '受保护')}${chip('talk', '有对白')}${chip('vert', '竖屏')}${chip('hideBlur', '隐藏废片')}</div>` : ''}
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
  return tr('{0} 幕 · 约 {1} · 可以拖动排序，直接改文字', b.length, mmss(b.reduce((a, x) => a + (+x.seconds || 0), 0)));
}

function beatHtml(b, i) {
  const ids = (b.shot_ids || []).filter((id) => S.byId[id]).slice(0, 8);
  return `<li class="beat" draggable="true" data-beat="${i}">
    <div class="lead"><span class="handle" aria-hidden="true" title="${tr('拖动排序')}"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg></span>
      <span class="num">${String(i + 1).padStart(2, '0')}</span></div>
    <div class="main">
      <div class="row"><input class="title" data-bf="title" value="${esc(b.title)}" aria-label="${tr('这一幕的标题')}">
        <label class="secs">${tr('约')}<input type="number" min="1" step="1" data-bf="seconds" value="${esc(Math.round(b.seconds || 0))}" aria-label="${tr('秒数')}">${tr('秒')} · ${tr('{0} 个候选镜头', (b.shot_ids || []).length)}</label></div>
      <textarea data-bf="intent" rows="2" aria-label="${tr('这一幕要做什么')}">${esc(b.intent || '')}</textarea>
      ${b.evidence ? `<div class="ev">${tr('素材依据：')}${esc(b.evidence)}</div>` : ''}
      ${ids.length ? `<div class="minis">${ids.map((id) => `<img loading="lazy" src="${thumbOf(id)}" alt="" title="${esc(S.byId[id].summary || id)}">`).join('')}</div>` : ''}
    </div>
    <div class="ops"><button type="button" class="ghost sm" data-bmove="-1" aria-label="${tr('上移')}" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button type="button" class="ghost sm" data-bmove="1" aria-label="${tr('下移')}" ${i === S.outline.beats.length - 1 ? 'disabled' : ''}>↓</button>
      <button type="button" class="ghost sm danger" data-bdel aria-label="${tr('删除这一幕')}">${tr('删除')}</button></div>
  </li>`;
}

function protectSummaryHtml() {
  const prot = S.shots.filter((x) => x.protected && !x.excluded);
  if (!prot.length) return '';
  const by = {};
  prot.forEach((x) => { by[x.protected] = (by[x.protected] || 0) + 1; });
  return `<div class="gap prot-box"><b>${SHIELD} ${tr('关键画面保护：{0} 个镜头', prot.length)}</b>
    ${Object.entries(by).map(([r, n]) => `${esc(tr(r))} ${n}`).join(tr('、'))}
    <div class="note" style="margin-top:6px">${tr('AI 编故事时会被要求保留这些镜头，漏掉的会自动补回。')} <a href="#footage" data-act="show-protected">${tr('查看')}</a></div></div>`;
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
      <div><h1 style="font-size:24px;margin-bottom:6px">${tr('你想讲一个什么故事？')}</h1>
        <p class="muted" style="margin:0;line-height:1.6">${tr('跟谁去的、这趟旅行意味着什么、想要什么感觉、结尾落在哪。用自己的话写就行。')}</p></div>
      <label for="sBrief" class="sr">${tr('故事描述')}</label>
      <textarea id="sBrief" class="field" rows="8" placeholder="${tr('比如：和 James 从多伦多一路开到大西洋边，前半段热闹，越往东越安静，结尾停在傍晚的灯塔。')}">${esc(s.brief || '')}</textarea>
      <div class="section"><div class="label">${tr('成片长度')}</div>
        <div class="seg" style="grid-template-columns:repeat(4,minmax(0,1fr)) minmax(0,1.4fr)" role="group" aria-label="${tr('成片长度')}">
          ${lens.map((m) => `<button type="button" data-min="${m}" aria-pressed="${minutes === m}">${tr('{0} 分', m)}</button>`).join('')}
          <input id="sMinCustom" class="field mono" style="min-height:44px;padding:0 8px;${custom ? 'border-color:var(--accent)' : ''}" type="number" min="0.5" max="120" step="0.5" value="${custom ? esc(minutes) : ''}" placeholder="${tr('自定')}" aria-label="${tr('自定义分钟数')}">
        </div></div>
      <div class="section"><div class="label">${tr('画幅')}</div>
        <div class="seg" style="grid-template-columns:repeat(2,minmax(0,1fr))">
          <button type="button" data-vert="0" aria-pressed="${!s.vertical}">${tr('横屏 16:9')}</button>
          <button type="button" data-vert="1" aria-pressed="${!!s.vertical}">${tr('竖屏 9:16')}</button></div></div>
      <div class="section"><div class="label">${tr('背景音乐（渲染时自动卡点，对白时自动压低）')}</div>
        <div class="path" style="display:flex;align-items:center;gap:8px">${s.music ? `<span style="flex:1">${esc(s.music.split(/[\\/]/).pop())}</span>` : `<span style="flex:1" class="muted">${tr('不加音乐')}</span>`}
          ${s.music ? `<button type="button" class="ghost sm" data-act="clear-music">${tr('移除')}</button>` : ''}
          <button type="button" class="ghost sm" data-act="pick-music">${s.music ? tr('更换') : tr('选择')}</button></div></div>
      <button type="button" class="btn" style="min-height:48px;font-size:15px" data-act="outline" ${!ready || jobRunning() ? 'disabled' : ''}>${ol ? tr('重新生成故事线') : tr('让 AI 提出分幕')}</button>
      ${!ready ? `<p class="note">${tr('需要先在“素材”里完成 AI 看片。')}</p>` : ''}
    </aside>
    <div class="content">
      ${ol ? `<div class="toolbar" style="gap:12px">
          <div style="flex:1 1 240px"><h2 style="font-size:22px">${esc(ol.title || tr('故事线'))}</h2>
            <div class="muted" style="margin-top:2px">${esc(ol.logline || '')}</div>
            <div class="muted mono" style="font-size:12px;margin-top:4px" id="oSummary">${outlineSummary()}</div></div>
          <button type="button" class="ghost" data-act="add-beat">${tr('+ 加一幕')}</button>
          <button type="button" class="btn" data-act="make-story" ${!ol.beats.length || jobRunning() ? 'disabled' : ''}>${tr('按这个结构剪 →')}</button></div>
        <ol class="beats" id="beats">${ol.beats.map(beatHtml).join('')}</ol>`
      : `<div class="empty"><h2>${tr('故事线会出现在这里')}</h2>
          ${S.plan && (S.plan.beats || []).length ? `<button type="button" class="ghost outline" data-act="plan-to-story">${tr('用拍摄脚本的分幕作为故事线')}</button>` : ''}<p style="margin:0;max-width:440px;line-height:1.7">${tr('在左边写下你想要的故事，AI 会对照你真实拍到的素材，给出一组可以编辑、排序、删除的分幕。')}</p></div>`}
    </div>
    <aside class="detail">
      <div><h2 style="font-size:18px">${tr('素材撑不起的部分')}</h2>
        <p class="muted" style="margin:6px 0 0;line-height:1.6">${tr('AI 对照了你写的内容和实际拍到的东西，这些地方需要你决定。')}</p></div>
      ${missing.length ? missing.map((m) => `<div class="gap">${esc(m)}<div style="margin-top:6px"><button type="button" class="ghost sm" data-labgap="${esc(m)}">${tr('去镜头工坊补一个')}</button></div></div>`).join('') : `<p class="muted" style="margin:0">${ol ? tr('没有发现明显的缺口。') : tr('生成故事线后显示。')}</p>`}
      ${ol && ol.notes ? `<div class="gap" style="background:var(--card);border-color:var(--line)"><b style="color:var(--text)">${tr('剪辑建议')}</b>${esc(ol.notes)}</div>` : ''}
      ${protectSummaryHtml()}
      ${S.p.flags && (S.p.flags.must || S.p.flags.exclude) ? `<p class="note">${tr('已标记：{0} 个必用镜头，{1} 个排除镜头（排除的 AI 不会用）。', S.p.flags.must, S.p.flags.exclude)}</p>` : ''}
      <p class="note" style="margin-top:auto;border-top:1px solid var(--line);padding-top:14px;font-size:13px">${tr('提示：录一段 5 分钟的语音回忆放进素材文件夹，开启转写后，AI 能找到更清楚的结构。')}</p>
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
    const res = tr('v{0}：{1} 个镜头，{2}', v.n, v.shots, mmss(v.seconds));
    if (v.note.startsWith('revise: ')) return `<div class="msg me">${esc(v.note.slice(8))}</div><div class="msg ai">${tr('改好了 → {0}', res)}</div>`;
    if (v.note.startsWith('initial') || v.note.startsWith('from approved')) return `<div class="msg ai">${tr('按故事线剪了一版 → {0}', res)}</div>`;
    return `<div class="msg ai">${esc(versionNote(v.note))} → ${res}</div>`;
  }).join('');
  const cur = S.versions[0];
  return `<div class="toolbar" style="justify-content:space-between">
      <h2 style="font-size:18px">${tr('用一句话改')}</h2>
      ${S.versions.length ? `<label class="muted" style="display:flex;align-items:center;gap:6px;font-size:13px">${tr('版本')}
        <select id="tVersion" style="min-height:34px;background:var(--raise);border:1px solid var(--line2);border-radius:6px;padding:0 6px">
          ${S.versions.map((v) => `<option value="${v.n}">v${v.n}${v === cur ? ' · ' + tr('当前') : ''}</option>`).join('')}</select></label>` : ''}</div>
    <div class="msgs" id="tMsgs">${msgs || `<div class="muted">${tr('还没有修改记录。')}</div>`}</div>
    ${mockOn() ? `<p class="note" style="color:var(--accent)">${tr('离线模拟听不懂修改要求，只会重新校验。')}</p>` : ''}
    <div class="toolbar" style="gap:6px">
      ${['开头再有力一点', '少一些空镜', '第二段节奏再快点', '去掉重复的镜头'].map(tr).map((x) => `<button type="button" class="chip" style="min-height:34px;font-size:13px" data-suggest="${esc(x)}">${esc(x)}</button>`).join('')}</div>
    <form id="tAsk"><label for="tAskInput" class="sr">${tr('修改要求')}</label>
      <input id="tAskInput" class="field" placeholder="${tr('比如：结尾停在灯塔，不要回程')}" autocomplete="off">
      <button class="btn" ${dis()}>${tr('发送')}</button></form>`;
}

function viewTimeline() {
  if (!S.tl) {
    return `<div class="page"><div class="content"><div class="empty"><h2>${tr('还没有时间线')}</h2>
      <p style="margin:0">${tr('先在“故事线”里确认分幕，再按结构剪。')}</p><a class="btn" href="#story">${tr('去写故事线')}</a></div></div></div>`;
  }
  const total = S.tl.reduce((a, t) => a + (t.out - t.in), 0);
  const m = S.p.mtimes || {};
  return `<div class="tl-top">
    <section class="player">
      <div class="screen" id="tScreen">${S.p.has_preview ? `<video id="tVideo" controls preload="metadata" src="${media('preview.mp4', m['preview.mp4'])}"></video>`
        : `<div class="ph"><p style="margin:0 0 12px">${tr('还没有渲染预览')}</p><button type="button" class="btn" data-act="render" ${dis()}>${tr('渲染预览')}</button></div>`}</div>
      <div class="toolbar">
        <span class="mono">${tr('{0} 个镜头', S.tl.length)} · ${mmss(total)}</span>
        <span class="muted" style="flex:1">${previewStale() ? tr('预览是上一次渲染的，和当前时间线不一致') : S.p.has_preview ? tr('点时间线上的镜头会跳到对应位置') : ''}</span>
        ${S.p.has_preview ? `<button type="button" class="ghost sm" data-act="render" ${dis()}>${tr('重新渲染预览')}</button>` : ''}
      </div>
    </section>
    <aside class="chat" id="tChat">${chatHtml()}</aside>
  </div>
  <div id="tSave"></div>
  <section class="tracks" aria-label="${tr('时间线')}"><div class="tracks-in" id="tTracks"></div></section>
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
    <div class="lane">${ch.map((c, k) => `<div class="chap" style="flex:${chDur(c)} 1 0" title="${esc(c.name)}"><b>${String(k + 1).padStart(2, '0')}</b>${esc(c.name || tr('（无章节）'))}</div>`).join('')}</div>
    <div class="lab">V1</div>
    <div class="lane">${ch.map((c) => `<div class="chapgroup" style="flex:${chDur(c)} 1 0">${c.items.map((i) => {
      const t = S.tl[i], s = S.byId[t.shot_id] || {};
      const bg = s.thumb ? `background-image:url('${s.thumb}')` : '';
      const prot = s.protected && !s.excluded;
      return `<button type="button" class="clip ${i === S.tsel ? 'sel' : ''} ${t.role === 'protected' ? 'auto-prot' : ''}" draggable="true" data-clip="${i}" style="flex:${dur(i)} 1 0;${bg}"
        aria-label="${esc((s.summary || t.shot_id) + ' ' + tr('{0} 秒', dur(i).toFixed(1)) + (prot ? ' · ' + tr('受保护：{0}', tr(s.protected)) : ''))}">${prot ? `<span class="shield" title="${esc(tr('受保护：{0}', tr(s.protected)))}">${SHIELD}</span>` : ''}${t.text ? `<span class="cap">${esc(t.text)}</span>` : ''}</button>`;
    }).join('')}</div>`).join('')}</div>
    <div class="lab">A1</div>
    <div class="lane">${ch.map((c) => `<div class="chapgroup" style="flex:${chDur(c)} 1 0">${c.items.map((i) => `<span class="aud ${S.tl[i].keep_audio ? 'on' : ''}" style="flex:${dur(i)} 1 0" title="${S.tl[i].keep_audio ? tr('保留原声') : tr('原声静音')}"></span>`).join('')}</div>`).join('')}</div>
    <div class="lab">A2</div>
    <div class="music ${music ? '' : 'none'}">${music ? '♪ ' + esc(music.split(/[\\/]/).pop()) : tr('没有背景音乐（在“故事线”里选择）')}</div>`;
}

function renderEditor() {
  const el = $('#tEditor'); if (!el) return;
  const t = S.tl[S.tsel];
  if (!t) { el.innerHTML = `<span class="muted">${tr('时间线是空的')}</span>`; return; }
  const s = S.byId[t.shot_id] || { duration: t.out, has_audio: false };
  el.innerHTML = `<div class="who"><div class="mono muted" style="font-size:12px">#${S.tsel + 1} · ${esc(t.shot_id)} · ${esc(t.role || '')} · ${tr('镜头全长 {0} 秒', (+s.duration).toFixed(1))}</div>
      <div style="font:600 16px var(--serif);margin-top:4px">${esc(s.summary || t.shot_id)}</div>
      ${t.why && t.role !== 'protected' ? `<div class="muted" style="font-size:13px;margin-top:4px">${tr('剪辑理由：')}${esc(t.why)}</div>` : ''}
      ${s.protected && !s.excluded ? `<div class="prot-text" style="font-size:13px;margin-top:4px">${SHIELD} ${esc(tr('受保护：{0}', tr(s.protected)))}${t.role === 'protected' ? ' · ' + esc(tr('AI 漏掉了，已自动补回')) : ''}</div>` : ''}</div>
    <label class="f num">${tr('入点（秒）')}<input id="eIn" type="number" step="0.1" min="0" max="${s.duration}" value="${t.in}"></label>
    <label class="f num">${tr('出点（秒）')}<input id="eOut" type="number" step="0.1" min="0" max="${s.duration}" value="${t.out}"></label>
    <label class="f grow">${tr('字幕')}<input id="eText" value="${esc(t.text)}" placeholder="${tr('可留空')}"></label>
    <label class="f">${tr('章节')}<input id="eChap" value="${esc(t.chapter)}" style="width:150px"></label>
    <label class="check"><input id="eAudio" type="checkbox" ${t.keep_audio ? 'checked' : ''} ${s.has_audio ? '' : 'disabled'}>${tr('保留原声')}</label>
    <div class="toolbar" style="gap:6px">
      <button type="button" class="ghost sm" data-move="-1" ${S.tsel === 0 ? 'disabled' : ''}>${tr('← 前移')}</button>
      <button type="button" class="ghost sm" data-move="1" ${S.tsel === S.tl.length - 1 ? 'disabled' : ''}>${tr('后移 →')}</button>
      <button type="button" class="ghost sm danger" data-act="del-clip">${tr('删除镜头')}</button>
      ${s.lab ? '' : `<button type="button" class="ghost sm" data-labgo="slowmo" data-from-clip="${S.tsel}">${tr('做慢动作')}</button>
      <button type="button" class="ghost sm" data-labgo="crop" data-from-clip="${S.tsel}">${tr('裁切特写')}</button>`}</div>`;
}

function renderSavebar() {
  const el = $('#tSave'); if (!el) return;
  el.innerHTML = S.tlDirty ? `<div class="savebar" style="margin-top:16px"><span>${tr('有未保存的修改')}</span>
    <button type="button" class="ghost sm" data-act="discard">${tr('放弃')}</button>
    <button type="button" class="ghost sm outline" data-act="save-tl" ${dis()}>${tr('保存')}</button>
    <button type="button" class="btn sm" data-act="save-render" ${dis()}>${tr('保存并重新渲染')}</button></div>` : '';
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
  else { render(); toast(tr('已保存为新版本')); }
}
function startRender(extra = {}) {
  const s = S.p.settings || {};
  return startJob(`${P()}/render`, { music: s.music || '', vertical: !!s.vertical, hd: S.hd, ...extra });
}

/* ---------------------------------------------------------------- 4 导出 */
function viewExport() {
  const p = S.p, st = S.story;
  if (!st) return `<div class="page"><div class="content"><div class="empty"><h2>${tr('还没有可以导出的时间线')}</h2><a class="btn" href="#story">${tr('去写故事线')}</a></div></div></div>`;
  const m = p.mtimes || {}, s = p.settings || {};
  const cur = S.versions[0];
  const when = (k) => (m[k] ? `<span class="muted mono" style="font-size:12px">${tr('上次生成 {0}', new Date(m[k] * 1000).toLocaleString(locale()))}</span>` : '');
  const icon = (d) => `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">${d}</svg>`;
  return `<div class="ex-wrap"><main class="ex-main">
    <div><h1 style="font-size:26px">${tr('把这一版带走')}</h1>
      <p class="muted" style="margin:6px 0 0">${cur ? `v${cur.n} · ` : ''}${esc(st.title || '')} · ${tr('{0} 幕', new Set(st.timeline.map((t) => t.chapter)).size)} · ${tr('{0} 个镜头', st.timeline.length)} · ${mmss(st.total_seconds)}</p></div>
    <div class="ex-grid">
      <section class="ex-card"><div class="hd">${icon('<polygon points="6 4 20 12 6 20 6 4"/>')}<h2>${tr('MP4 预览')}</h2></div>
        <p>${tr('带字幕、卡节拍、对白时音乐自动压低。硬切，不调色，适合快速看效果。长片建议跳过，直接导时间线去 Resolve 精修。')}</p>
        <div class="seg" style="grid-template-columns:repeat(2,minmax(0,1fr))" role="group" aria-label="${tr('清晰度')}">
          <button type="button" data-hd="0" aria-pressed="${!S.hd}">${s.vertical ? tr('竖屏 1080×1920') : '720p'}</button>
          <button type="button" data-hd="1" aria-pressed="${S.hd}" ${s.vertical ? 'disabled' : ''}>1080p</button></div>
        <p style="font-size:13px">${tr('音乐：')}${s.music ? esc(s.music.split(/[\\/]/).pop()) : tr('无（在“故事线”里选择）')}</p>
        ${when('preview.mp4')}
        <div class="foot">${p.has_preview ? `<a class="ghost" href="${media('preview.mp4', m['preview.mp4'])}" target="_blank" rel="noopener">${tr('打开 preview.mp4')}</a>` : ''}
          <button type="button" class="btn" data-act="render" ${dis()}>${tr('渲染 preview.mp4')}</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>')}<h2>${tr('Resolve / Premiere 时间线')}</h2></div>
        <p>${tr('导出 timeline.xml，直接关联你的原片，接着在专业软件里精剪、调色、加转场。')}</p>
        <ul class="tracklist"><li><b>V1</b>${tr('画面')}</li><li><b>A1</b>${tr('原声（没保留的部分是静音，可以随时打开）')}</li><li><b>A2</b>${tr('音乐')}</li><li><b>◆</b>${tr('每幕的名字和 AI 的选片理由作为标记')}</li></ul>
        <div class="howto">Resolve：File → Import → Timeline<br>Premiere：File → Import<br>${tr('Final Cut Pro 暂不支持')}</div>
        ${when('timeline.xml')}
        <div class="foot">${p.has_xml ? `<a class="ghost" href="${media('timeline.xml', m['timeline.xml'])}" download="timeline.xml">${tr('下载')}</a>` : ''}
          <button type="button" class="ghost outline" data-act="export-xml" ${dis()}>${tr('导出 timeline.xml')}</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<path d="M8 4H6a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h2M16 4h2a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-2"/>')}<h2>${tr('剪辑清单 story.json')}</h2></div>
        <p>${tr('纯文本 JSON，可以直接手改入点、出点、字幕。改完点“重新校验”，故事板和 XML 会一起更新。')}</p>
        <p class="mono" style="font-size:12px;word-break:break-all">${esc(p.path || '')}</p>
        <div class="foot"><button type="button" class="ghost" data-act="open-folder">${tr('打开项目文件夹')}</button>
          <button type="button" class="ghost" data-act="recheck" ${dis()}>${tr('重新校验')}</button></div></section>
      <section class="ex-card"><div class="hd">${icon('<path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v5"/>')}<h2>${tr('分镜表')}</h2></div>
        <p>${tr('每个镜头的缩略图、选它的理由、它在成片里的位置。可以打印，也方便发给同行的人看。')}</p>
        <div class="foot">${p.has_storyboard ? `<a class="ghost" href="${media('storyboard.html')}" target="_blank" rel="noopener">${tr('打开 storyboard.html')}</a>` : `<span class="muted">${tr('生成时间线后会自动出现')}</span>`}</div></section>
    </div></main>
    <aside class="versions"><h2 style="font-size:18px">${tr('版本历史')}</h2><p class="muted" style="margin:0;font-size:13px">${tr('每一版都留着，随时可以回去。')}</p>
      <ol>${S.versions.map((v, k) => `<li class="ver ${k === 0 ? 'cur' : ''}"><div class="r"><b class="mono">v${v.n}</b><span>${mmss(v.seconds)} · ${esc((v.created || '').slice(5, 16).replace('T', ' '))}</span></div>
        <p>${esc(versionNote(v.note))}</p>
        ${k ? `<button type="button" class="ghost sm" data-restore="${v.n}" ${dis()}>${tr('恢复这一版')}</button>` : ''}</li>`).join('')}</ol></aside></div>`;
}
function versionNote(n) {
  if (n.startsWith('revise: ')) return `“${n.slice(8)}”`;
  if (n.startsWith('initial') || n.startsWith('from approved')) return tr('按故事线剪的版本');
  const m = n.match(/^恢复 v(\d+)$/);
  if (m) return tr('恢复 v{0}', m[1]);
  return tr(n);
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
      ? tr('后端还是更新前的版本，没有设置接口。在运行 studio 的窗口按 Ctrl+C 停掉，重新运行 python -m vlogai.studio，再刷新这个页面。')
      : e.message;
  }
}
function cfgChanged() {
  const a = S.cfg.settings, d = S.draft;
  return Object.keys(d).filter((k) => String(d[k]) !== String(a[k])).length + (S.newKey ? 1 : 0) + (S.newFalKey ? 1 : 0);
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
    `<button type="button" data-set="${key}" data-val="${esc(v)}" aria-pressed="${String(S.draft[key]) === String(v)}">${esc(tr(label))}${sub ? `<span style="display:block;font-size:11px;color:var(--muted)">${esc(tr(sub))}</span>` : ''}</button>`).join('')}</div>`;
}
/* 设置页的通用小部件：数字框（带取值范围和“恢复默认”）、勾选框 */
const CFG_LABELS = {};
function defHint(key) {
  const d = S.cfg.defaults[key];
  return String(S.draft[key]) !== String(d)
    ? `<button type="button" class="ghost sm" data-cfgdef="${key}" title="${esc(tr('恢复默认值 {0}', d))}">${esc(tr('默认 {0}', d))}</button>` : '';
}
function num(key, label, o = {}) {
  CFG_LABELS[key] = label;
  const r = (S.cfg.rules || {})[key] || [];
  const id = 'cfg_' + key;
  return `<div class="set-row"><label class="lbl" for="${id}">${tr(label)}</label>
    <div class="numrow"><input id="${id}" class="field num" type="number" ${r.length === 2 && typeof r[0] === 'number' ? `min="${r[0]}" max="${r[1]}"` : ''} step="${o.step || 'any'}" data-cfg="${key}" value="${esc(S.draft[key])}">
      ${o.unit ? `<span class="muted">${tr(o.unit)}</span>` : ''}${r.length === 2 && typeof r[0] === 'number' ? `<span class="help">${r[0]}–${r[1]}</span>` : ''}${defHint(key)}</div>
    ${o.help ? `<div class="help">${o.help}</div>` : ''}</div>`;
}
function chk(key, label, help) {
  CFG_LABELS[key] = label;
  return `<label class="check"><input type="checkbox" data-cfg="${key}" ${S.draft[key] ? 'checked' : ''}>${tr(label)}${help ? `<span class="help">${help}</span>` : ''}</label>`;
}
function navSubs() {
  const d = S.draft;
  return { ui: (I18N.LANGS.find(([c]) => c === I18N.lang) || [, ''])[1], api: '', frames: tr('每 {0} 秒', d.frame_every_sec),
    protect: d.protect_enabled ? tr('最多 {0}%', Math.round(d.protect_max_share * 100)) : tr('关'),
    edit: tr('长片 ≥ {0} 分', d.longform_min), whisper: d.whisper ? d.whisper_model : tr('关'),
    render: `${d.render_hd ? '1080p' : '720p'} · ${d.render_fps}fps`, lab: d.lab_provider === 'mock' ? tr('离线模拟') : 'fal.ai', advanced: '' };
}

function viewSettings() {
  if (!S.cfg) {
    if (!S.cfgError) { setTimeout(async () => { await loadSettings(); if (S.page === 'settings') render(); }, 0); }
    return `<div class="page"><div class="content">${S.cfgError
      ? `<div class="empty"><h2>${tr('读取设置失败')}</h2><p class="err" style="max-width:560px;line-height:1.7;margin:0">${esc(S.cfgError)}</p>
         <button type="button" class="btn" data-act="cfg-retry">${tr('重试')}</button></div>`
      : `<p class="muted">${tr('正在读取设置…')}</p>`}</div></div>`;
  }
  const d = S.draft, c = S.cfg, k = c.key || {}, e = estimate();
  const subs = navSubs();
  const nav = [['ui', '界面'], ['api', 'API Key 与模型'], ['frames', '抽帧与切镜头'], ['protect', '关键画面保护'], ['edit', '剪辑节奏'], ['whisper', '语音转写'], ['render', '预览渲染'], ['lab', '镜头工坊'], ['advanced', '性能与高级']];
  const keyStatus = S.keyTest
    ? (S.keyTest === 'testing' ? `<span class="muted">${tr('正在测试…')}</span>`
      : S.keyTest.ok ? `<span class="ok-text">${tr('✓ 连接成功')}${S.keyTest.models && S.keyTest.models.length ? ' · ' + tr('可用模型 {0} 个', S.keyTest.models.length) : ''}</span>${S.keyTest.missing && S.keyTest.missing.length ? `<br><span class="bad-text">${tr('你选的模型不在可用列表里：')}${esc(S.keyTest.missing.join(tr('、')))}</span>` : ''}`
        : `<span class="bad-text">✗ ${esc(trServer(S.keyTest.reason))}</span>`)
    : (k.has_key ? `<span class="muted">${esc(tr('当前：{0}（{1} 位，来自 {2}）', k.hint, k.length, k.source))}</span>` : `<span class="bad-text">${tr('还没有 API Key，只能用离线模拟')}</span>`);
  const models = [...new Set(MODEL_CHOICES.concat((S.keyTest && S.keyTest.models) || []).concat([d.describe_model, d.story_model]))];
  const restart = (+d.port !== +c.settings.port || d.projects_dir !== c.settings.projects_dir);
  const n = cfgChanged();
  return `<div class="set-wrap">
    <aside class="set-nav">
      <h1 style="font-size:26px;margin:0 0 14px">${tr('设置')}</h1>
      ${nav.map(([id, label]) => `<button type="button" data-scroll="${id}"><span>${tr(label)}</span><span class="sub" id="nav-${id}">${esc(subs[id])}</span></button>`).join('')}
      <p class="help" style="margin-top:16px">${tr('这些设置对所有项目生效，保存在 {0}。API Key 存在 .env，不会被提交到 git。', `<span class="mono">${esc(c.settings_file.split(/[\\/]/).pop())}</span>`)}</p>
    </aside>
    <main class="set-main">

    <section class="set-card" id="sec-ui">
      <div><h2>${tr('界面')}</h2></div>
      <div class="set-row"><div class="lbl">${tr('界面语言')}</div>
        <div class="seg" role="group" aria-label="${tr('界面语言')}" style="max-width:340px;grid-template-columns:repeat(${I18N.LANGS.length},minmax(0,1fr))">
          ${I18N.LANGS.map(([c, n]) => `<button type="button" data-uilang="${c}" aria-pressed="${I18N.lang === c}">${esc(n)}</button>`).join('')}</div>
        <div class="help">${tr('只改变界面上的文字，保存在这个浏览器里。AI 写的镜头日志和字幕用什么语言，在下面的「字幕和日志的语言」里设置。')}</div></div>
    </section>

    <section class="set-card" id="sec-api">
      <div><h2>${tr('API Key 与模型')}</h2><p class="lead">${tr('AI 看片和编故事用的都是 Anthropic 的模型。')}</p></div>
      <div class="set-row">
        <label class="lbl" for="cfgKey">Anthropic API Key</label>
        <div class="toolbar">
          <input id="cfgKey" class="field mono" style="flex:1 1 320px;font-size:13px" type="${S.showKey ? 'text' : 'password'}" autocomplete="off" spellcheck="false"
            value="${esc(S.newKey || '')}" placeholder="${k.has_key ? esc(k.hint) + tr('（想换就粘贴新的）') : 'sk-ant-api03-…'}">
          <button type="button" class="ghost" data-act="toggle-key">${S.showKey ? tr('隐藏') : tr('显示')}</button>
          <button type="button" class="ghost outline" data-act="test-key">${tr('测试连接')}</button>
        </div>
        <div style="font-size:13px;line-height:1.6" id="keyStatus">${keyStatus}</div>
        <div class="help">${tr('保存到 {0}。在 {1} 创建。保存后下一个任务就会用新的 Key，不用重启。', `<span class="mono">${esc(c.env_file)}</span>`, '<a href="https://platform.claude.com" target="_blank" rel="noopener">platform.claude.com</a>')}</div>
      </div>
      <datalist id="modelList">${models.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgM1">${tr('看片模型')}</label>
          <input id="cfgM1" class="field mono" style="font-size:13px" list="modelList" data-cfg="describe_model" value="${esc(d.describe_model)}">
          <div class="help">${tr('每个镜头调用一次，镜头多时调用次数也多。选快一点、便宜一点的。')}</div></div>
        <div class="set-row"><label class="lbl" for="cfgM2">${tr('编故事模型')}</label>
          <input id="cfgM2" class="field mono" style="font-size:13px" list="modelList" data-cfg="story_model" value="${esc(d.story_model)}">
          <div class="help">${tr('分幕、剪时间线、一句话修改都用它。每次只调用一次，故事好不好主要看它。')}</div></div>
      </div>
      <div class="set-row"><div class="lbl">${tr('字幕和日志的语言')}</div><div style="max-width:340px">${seg('lang', [['zh', '中文'], ['en', 'English']])}</div></div>
    </section>

    <section class="set-card" id="sec-frames">
      <div><h2>${tr('抽帧与切镜头')}</h2><p class="lead">${tr('AI 看不到视频，只看从每个镜头里抽出的几张静帧。帧越多看得越细，花费也越多。')}</p></div>
      <div class="set-row">
        <div class="lbl"><label for="cfgEvery">${tr('抽帧间隔')}</label><span class="val" id="everyVal">${tr('每 {0} 秒一帧', d.frame_every_sec)}</span></div>
        <input id="cfgEvery" type="range" min="0.5" max="10" step="0.5" data-cfg="frame_every_sec" value="${d.frame_every_sec}">
        <div class="help" style="display:flex;justify-content:space-between"><span>${tr('1 秒 · 看得最细')}</span><span>${tr('默认 4 秒')}</span><span>${tr('10 秒 · 最省')}</span></div>
      </div>
      <div class="set-grid">
        <div class="set-row"><div class="lbl">${tr('每个镜头最多几帧')}</div>${seg('max_frames_per_shot', [[1, '1'], [2, '2'], [3, '3'], [4, '4'], [6, '6'], [8, '8']])}</div>
        <div class="set-row"><div class="lbl">${tr('静帧尺寸')}</div>${seg('frame_width', [[384, '384px'], [512, '512px'], [768, '768px']])}</div>
      </div>
      <div class="estimate" id="estimate">
        <div><span>${tr('一个 12 秒的镜头')}</span><b>${tr('{0} 帧', e.f12)}</b></div>
        <div><span>${tr('每个镜头约')}</span><b>${e.perShot} tokens</b></div>
        <div><span>${tr('500 个镜头约')}</span><b>${e.p500} tokens</b></div>
        <div class="help" style="grid-column:1/-1;color:var(--text2)">${tr('估算：每张静帧约 {0} tokens，加上约 600 tokens 的提示和镜头信息。实际用量以每次任务结束时打印的数字为准。', e.perImage)}</div>
      </div>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgLong">${tr('长于多少秒才切分镜头')}</label>
          <div style="display:flex;align-items:center;gap:8px"><input id="cfgLong" class="field num" type="number" min="3" max="600" step="1" data-cfg="long_clip_sec" value="${d.long_clip_sec}"><span class="muted">${tr('秒')}</span></div>
          <div class="help">${tr('短片段整条当一个镜头；长片段按画面变化切开，比如一段边走边拍的长视频。')}</div></div>
        <div class="set-row"><div class="lbl">${tr('切镜头灵敏度')}</div>${seg('scene_sensitivity', [['low', '低'], ['mid', '中'], ['high', '高']])}
          <div class="help">${tr('高：画面稍有变化就切开，镜头更多更碎。需要装 PySceneDetect。')} ${tr('已经切好的素材不会重新切，改了只对新加的素材生效。')}</div></div>
      </div>
      <div class="set-row">
        <div class="lbl"><label for="cfgBad">${tr('废片判定')}</label><span class="val" id="badVal">${tr('画质分 &lt; {0}', (+d.bad_quality).toFixed(2))}</span></div>
        <input id="cfgBad" type="range" min="0" max="0.6" step="0.05" data-cfg="bad_quality" value="${d.bad_quality}">
        <div class="help">${tr('画质分低于这个值的镜头算“废片”：素材页可以隐藏，统计里单独列出。')}</div>
      </div>
      <details class="more" ${store('cfgMore.frames') ? 'open' : ''} data-more="frames"><summary>${tr('更多参数')}</summary>
        <div class="set-grid">
          ${num('min_clip_sec', '忽略短于多少秒的文件', { unit: '秒', step: 0.1, help: tr('误触录下的一两下会被跳过。') })}
          ${num('min_scene_sec', '每个镜头至少多长', { unit: '秒', step: 0.5, help: tr('切镜头时不会切出比这更短的镜头。') })}
        </div>
        <div class="section" style="gap:4px">
          ${chk('use_proxy', '先转成低分辨率代理再切镜头', tr('（推荐。4K 长视频快很多，切点基本一样）'))}
          ${d.use_proxy ? chk('hwaccel', '转代理时尝试用显卡解码', tr('（不支持时会自动改用 CPU）')) : ''}
        </div>
        ${d.use_proxy ? `<div class="set-grid">
          ${num('proxy_width', '代理宽度', { unit: 'px', step: 16 })}
          ${num('proxy_fps', '代理每秒几帧', { unit: 'fps', step: 1, help: tr('越高越不容易漏掉很快的切换，转得也越慢。') })}
        </div>` : ''}
        <div class="set-grid">
          ${num('sharp_ref', '清晰度基准', { step: 10, help: tr('清晰度达到这个值算满分。手机素材普遍偏软、被判成废片太多时调低。') })}
          ${num('exposure_target', '理想亮度', { unit: '/255', step: 5, help: tr('平均亮度越接近它，曝光分越高。夜景多的旅行可以调低。') })}
        </div>
      </details>
      <div class="help" style="color:var(--accent);border-top:1px solid var(--line);padding-top:14px">${tr('改了这里的设置，只对还没处理过的素材生效。想让已有项目按新设置重新抽帧，要删掉那个项目文件夹里的 shots.json 和 frames 文件夹再分析。')}</div>
    </section>

    <section class="set-card" id="sec-protect">
      <div class="toolbar" style="justify-content:space-between;align-items:flex-start">
        <div style="flex:1 1 300px"><h2>${tr('关键画面保护')}</h2><p class="lead">${tr('AI 看片时判断出的重要镜头，剪片时一定保留；AI 漏掉会自动补回。你标的“必用”镜头不受这里的开关影响。')}</p></div>
        <button type="button" class="switch" role="switch" aria-checked="${!!d.protect_enabled}" aria-label="${tr('开启关键画面保护')}" data-toggle="protect_enabled"><i></i></button>
      </div>
      ${d.protect_enabled ? `<div class="section" style="gap:4px">
          ${chk('protect_faces', '主人公正脸')}
          ${chk('protect_together', '关键人物同框')}
          ${chk('protect_ending', '适合作结尾的镜头')}
        </div>
        <div class="set-grid">
          ${num('protect_scenery', '风景分达到多少就保护', { unit: '/10', step: 1, help: tr('0 = 不按风景保护。') })}
          ${num('protect_highlight', '精彩度达到多少就保护', { unit: '/10', step: 1, help: tr('0 = 不按精彩度保护。') })}
        </div>
        <div class="set-row"><div class="lbl"><label for="cfgShare">${tr('受保护镜头最多占成片')}</label><span class="val" id="shareVal">${Math.round(d.protect_max_share * 100)}%</span></div>
          <input id="cfgShare" type="range" min="0.1" max="1" step="0.05" data-cfg="protect_max_share" value="${d.protect_max_share}">
          <div class="help">${tr('超过就按优先级舍弃一部分（正脸 > 同框 > 风景 > 精彩 > 结尾）。太高 AI 就没什么可剪的了。')}</div></div>
        <div class="set-row"><div class="lbl"><label for="cfgPq">${tr('画质低于多少不保护')}</label><span class="val" id="pqVal">${(+d.protect_min_quality).toFixed(2)}</span></div>
          <input id="cfgPq" type="range" min="0" max="0.6" step="0.02" data-cfg="protect_min_quality" value="${d.protect_min_quality}">
          <div class="help">${tr('糊成一片的镜头再重要也救不回来。')}</div></div>` : ''}
    </section>

    <section class="set-card" id="sec-edit">
      <div><h2>${tr('剪辑节奏')}</h2><p class="lead">${tr('告诉 AI 剪多少段、每段大概多长。实际节奏还会看素材和你的故事描述。')}</p></div>
      <div class="set-grid">
        ${num('longform_min', '成片长于几分钟按长片剪', { unit: '分钟', step: 0.5, help: tr('短片：快节奏、只留精华；长片：镜头更舒展，保留更多旅程。') })}
        ${num('short_seg_sec', '短片平均每段几秒', { unit: '秒', step: 0.5 })}
        ${num('long_seg_sec', '长片平均每段几秒', { unit: '秒', step: 0.5 })}
        ${num('plan_shots_per_day', '拍摄脚本每天几个镜头', { unit: '个', step: 1, help: tr('0 = 自动（成片 3 分钟以内 10 个，更长 16 个）。') })}
      </div>
      <div class="set-row"><div class="lbl"><label for="cfgSnap">${tr('对齐节拍时每段最多拉长 / 缩短')}</label><span class="val" id="snapVal">${Math.round(d.beat_snap_range * 100)}%</span></div>
        <input id="cfgSnap" type="range" min="0" max="0.9" step="0.05" data-cfg="beat_snap_range" value="${d.beat_snap_range}">
        <div class="help">${tr('只在“剪辑点对齐音乐节拍”打开时有用；有对白的片段不动。')}</div></div>
    </section>

    <section class="set-card" id="sec-whisper">
      <div class="toolbar" style="justify-content:space-between;align-items:flex-start">
        <div style="flex:1 1 300px"><h2>${tr('语音转写')}</h2><p class="lead">${tr('在本机把视频里说的话转成文字，AI 编故事时会参考。不上传音频。')}</p></div>
        <button type="button" class="switch" role="switch" aria-checked="${!!d.whisper}" aria-label="${tr('默认开启语音转写')}" data-toggle="whisper"><i></i></button>
      </div>
      ${d.whisper ? `<div class="set-row"><div class="lbl">${tr('Whisper 模型')}</div>${seg('whisper_model', WHISPER.map(([v, s]) => [v, v, s]))}
        <div class="help">${c.has_whisper ? tr('第一次使用某个模型时会自动下载。') : tr('{0}：在项目的虚拟环境里运行 {1}，然后重启 studio。', `<span class="bad-text">${tr('还没安装 faster-whisper')}</span>`, '<span class="mono">pip install faster-whisper</span>')}</div></div>` : ''}
    </section>

    <section class="set-card" id="sec-render">
      <div><h2>${tr('预览渲染')}</h2><p class="lead">${tr('只影响 preview.mp4。导出给 Resolve 的时间线始终链接原片。')}</p></div>
      <div class="set-grid">
        <div class="set-row"><div class="lbl">${tr('默认清晰度')}</div>${seg('render_hd', [[false, '720p · 快'], [true, '1080p']])}</div>
        <div class="set-row"><div class="lbl"><label for="cfgVol">${tr('背景音乐音量')}</label><span class="val" id="volVal">${Math.round(d.music_volume * 100)}%</span></div>
          <input id="cfgVol" type="range" min="0" max="1.5" step="0.05" data-cfg="music_volume" value="${d.music_volume}" style="margin-top:12px"></div>
      </div>
      <div class="section" style="gap:4px">
        <label class="check"><input type="checkbox" data-cfg="beat_snap" ${d.beat_snap ? 'checked' : ''}>${tr('剪辑点对齐音乐节拍')}<span class="help">${tr('（每段最多拉长 / 缩短 {0}%，在“剪辑节奏”里改；有对白的片段不动；导出 XML 也按这个）', Math.round(d.beat_snap_range * 100))}</span></label>
        <label class="check"><input type="checkbox" data-cfg="duck_music" ${d.duck_music ? 'checked' : ''}>${tr('有人说话时自动压低音乐')}</label>
        <label class="check"><input type="checkbox" data-cfg="burn_captions" ${d.burn_captions ? 'checked' : ''}>${tr('烧录字幕')}</label>
      </div>
      <div class="set-row"><label class="lbl" for="cfgFont">${tr('字幕字体')}</label>
        <div class="toolbar"><input id="cfgFont" class="field mono" style="flex:1 1 300px;font-size:13px" data-cfg="font" value="${esc(d.font)}" placeholder="${c.detected_font ? tr('自动：') + esc(c.detected_font) : tr('没找到中文字体，请填写 .ttf/.ttc 路径')}">
          ${d.font ? `<button type="button" class="ghost" data-act="font-auto">${tr('改回自动')}</button>` : ''}</div>
        <div class="help">${tr('留空会自动找系统里的中文字体（微软雅黑、黑体、苹方）。')}</div></div>
      <div class="set-grid">
        <div class="set-row"><div class="lbl">${tr('帧率')}</div>${seg('render_fps', [[24, '24'], [25, '25'], [30, '30'], [50, '50'], [60, '60']])}</div>
        ${num('render_crf', '画质（CRF）', { step: 1, help: tr('越小越清晰、文件越大。18 接近无损，28 明显压缩。') })}
        ${num('caption_size', '字幕字号', { unit: tr('% 画面高度'), step: 0.5 })}
        ${num('caption_pos', '字幕位置', { unit: tr('% 从顶部算'), step: 1 })}
        ${num('music_fade_in', '音乐淡入', { unit: '秒', step: 0.5 })}
        ${num('music_fade_out', '音乐淡出', { unit: '秒', step: 0.5 })}
        ${num('duck_ratio', '说话时音乐压低力度', { step: 1, help: tr('1 = 不压低，越大压得越狠。') })}
        ${num('render_workers', '渲染时同时转几段', { step: 1, help: tr('电脑核心多可以调高；内存小的电脑别超过 4。') })}
      </div>
    </section>

    <section class="set-card" id="sec-lab">
      <div><h2>${tr('镜头工坊')}</h2><p class="lead">${tr('“补镜头”要用视频生成服务（按生成的秒数收费，和 Anthropic 分开结算）。慢动作和裁切在本机做，不花钱。')}</p></div>
      <div class="set-grid">
        ${num('lab_slowmo_workers', '慢动作同时算几块', { step: 1, help: tr('0 = 自动（按 CPU 核数和空闲内存）。光流补帧每块约占 1 GB 内存，电脑卡就调小。') })}
      </div>
      <div class="set-row"><div class="lbl">${tr('视频生成服务')}</div>
        <div style="max-width:420px">${seg('lab_provider', [['mock', '离线模拟', '不花钱，测流程'], ['fal', 'fal.ai', '可选 Kling、Veo 等']])}</div></div>
      ${d.lab_provider === 'fal' ? `<div class="set-row"><label class="lbl" for="cfgFal">fal.ai Key</label>
          <div class="toolbar"><input id="cfgFal" class="field mono" style="flex:1 1 320px;font-size:13px" type="${S.showKey ? 'text' : 'password'}" autocomplete="off" spellcheck="false"
            value="${esc(S.newFalKey || '')}" placeholder="${c.fal_key && c.fal_key.has_key ? esc(c.fal_key.hint) + tr('（想换就粘贴新的）') : tr('粘贴 fal.ai 的 Key')}"></div>
          <div class="help">${c.fal_key && c.fal_key.has_key ? tr('已设置：{0}。', esc(c.fal_key.hint)) : `<span class="bad-text">${tr('还没有填。')}</span>`}
            ${tr('保存到 .env 的 FAL_KEY。在 {0} 创建。', '<a href="https://fal.ai/dashboard/keys" target="_blank" rel="noopener">fal.ai/dashboard/keys</a>')}</div></div>
        <div class="set-row"><label class="lbl" for="cfgLabModel">${tr('模型')}</label>
          <input id="cfgLabModel" class="field mono" style="font-size:13px" data-cfg="lab_model" value="${esc(d.lab_model)}">
          <div class="help">${tr('图片生成视频（image-to-video）的模型名，在 fal.ai 模型页面复制。不同模型价格和效果差别很大。')}</div></div>
        <div class="set-grid">
          ${num('lab_price_per_sec', '估算单价', { unit: tr('美元 / 秒'), step: 0.01, help: tr('只用来在生成前提醒大概花多少，填你选的模型在官网上的价格。') })}
        </div>
        <details class="more" ${store('cfgMore.lab') ? 'open' : ''} data-more="lab"><summary>${tr('更多参数')}</summary>
          <div class="set-row"><label class="lbl" for="cfgLabBase">${tr('接口地址')}</label>
            <input id="cfgLabBase" class="field mono" style="font-size:13px" data-cfg="lab_base_url" value="${esc(d.lab_base_url)}"></div>
          <div class="set-row"><label class="lbl" for="cfgLabExtra">${tr('额外参数（JSON）')}</label>
            <input id="cfgLabExtra" class="field mono" style="font-size:13px" data-cfg="lab_extra" value="${esc(d.lab_extra)}" placeholder='{"negative_prompt": "blur, distort"}'>
            <div class="help">${tr('会原样加进请求里，用来传模型特有的参数。')}</div></div>
        </details>` : ''}
    </section>

    <section class="set-card" id="sec-advanced">
      <div><h2>${tr('性能与高级')}</h2></div>
      <div class="set-grid">
        <div class="set-row"><label class="lbl" for="cfgWorkers">${tr('同时处理几个镜头')}</label>
          <input id="cfgWorkers" class="field num" type="number" min="1" max="32" data-cfg="workers" value="${d.workers}">
          <div class="help">${tr('抽帧和看片的并行数。4K 素材多时调高能快不少；遇到 API 限流（429）就调低。')}</div></div>
        ${num('api_retries', 'API 出错时重试几次', { step: 1, help: tr('只对超时、过载这类临时错误重试；Key 无效、余额不足不会重试。') })}
        <div class="set-row"><label class="lbl" for="cfgPort">${tr('端口')}</label>
          <input id="cfgPort" class="field num" type="number" min="1024" max="65535" data-cfg="port" value="${d.port}">
          <div class="help">${tr('下次启动 studio 时生效（现在运行在 {0}）。被占用会自动换下一个。', esc(c.running_port || '–'))}</div></div>
      </div>
      <div class="set-row"><label class="lbl" for="cfgProj">${tr('项目保存位置')}</label>
        <div class="toolbar"><input id="cfgProj" class="field mono" style="flex:1 1 300px;font-size:13px" data-cfg="projects_dir" value="${esc(d.projects_dir)}">
          <button type="button" class="ghost" data-act="open-projects">${tr('打开文件夹')}</button></div>
        <div class="help">${tr('现在用的是 {0}。改了要重启 studio 才生效，已有项目不会自动搬过去。', `<span class="mono">${esc(c.projects_path)}</span>`)}</div></div>
      <div class="set-row"><div class="lbl">${tr('日志详细程度')}</div>
        <div style="max-width:520px">${seg('log_level', [['DEBUG', '详细', '含每个接口请求'], ['INFO', '正常', '推荐'], ['WARNING', '只记问题', '警告和报错']])}</div>
        <div class="help">${tr('日志写在 {0}，同时显示在运行 studio 的终端里。右上角的 {1} 可以在网页里看。', '<span class="mono">logs/vlogai.log</span>', `<a href="#logs">${tr('运行日志')}</a>`)}</div></div>
      <div class="set-row"><label class="lbl" for="cfgBase">${tr('API 地址')} <span class="help" style="font-weight:400">${tr('（一般不用改）')}</span></label>
        <input id="cfgBase" class="field mono" style="font-size:13px" data-cfg="base_url" value="${esc(d.base_url)}" placeholder="https://api.anthropic.com">
        <div class="help">${tr('只有通过公司代理或网关访问时才需要填。填错会导致 401 或连不上。')}</div></div>
      <div class="set-row"><label class="lbl" for="cfgOrigins">${tr('允许访问的网页')} <span class="help" style="font-weight:400">${tr('（一般不用改）')}</span></label>
        <input id="cfgOrigins" class="field mono" style="font-size:13px" data-cfg="allowed_origins" value="${esc(d.allowed_origins || '')}" placeholder="https://my-app.vercel.app">
        <div class="help">${tr('只有本机网页和 Lovable 可以调用这个后端。别的前端（比如自己部署的网站）要在这里填它的地址，多个用逗号隔开。不要填 *。')}</div></div>
    </section>
    </main>
  </div>
  <div class="set-save" id="setSave">${saveBarHtml(n, restart)}</div>`;
}
function saveBarHtml(n, restart) {
  return `<span>${n ? tr('有 {0} 项修改还没保存', n) : tr('所有设置都已保存')}${restart ? ` · <span style="color:var(--accent)">${tr('端口和项目位置要重启 studio 才生效')}</span>` : ''}</span>
    <button type="button" class="ghost" data-act="cfg-reset">${tr('恢复默认')}</button>
    ${n ? `<button type="button" class="ghost" data-act="cfg-discard">${tr('放弃修改')}</button>` : ''}
    <button type="button" class="btn" data-act="cfg-save" ${n ? '' : 'disabled'}>${tr('保存设置')}</button>`;
}
function settingsLive() {
  const d = S.draft, e = estimate(), subs = navSubs();
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set('everyVal', tr('每 {0} 秒一帧', d.frame_every_sec));
  set('badVal', tr('画质分 &lt; {0}', (+d.bad_quality).toFixed(2)));
  set('volVal', `${Math.round(d.music_volume * 100)}%`);
  set('shareVal', `${Math.round(d.protect_max_share * 100)}%`);
  set('pqVal', (+d.protect_min_quality).toFixed(2));
  set('snapVal', `${Math.round(d.beat_snap_range * 100)}%`);
  const est = $('#estimate');
  if (est) { const b = est.querySelectorAll('b'); b[0].textContent = tr('{0} 帧', e.f12); b[1].textContent = `${e.perShot} tokens`; b[2].textContent = `${e.p500} tokens`; }
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
  for (const [k, v] of Object.entries(changed)) {           // 先在网页里检查范围，出错时说清楚是哪一项
    const r = (S.cfg.rules || {})[k];
    if (r && r.length === 2 && typeof r[0] === 'number' && (!Number.isFinite(+v) || +v < r[0] || +v > r[1])) {
      toast(tr('“{0}”要在 {1}–{2} 之间', tr(CFG_LABELS[k] || k), r[0], r[1]), true);
      const el = document.querySelector(`[data-cfg="${k}"]`); if (el) { el.closest('details') && (el.closest('details').open = true); el.focus(); }
      return;
    }
  }
  const body = { settings: changed };
  if (S.newKey) body.api_key = S.newKey;
  if (S.newFalKey) body.fal_key = S.newFalKey;
  const r = await act(() => api('/api/settings', body));
  if (!r) return;
  S.cfg = r; S.draft = { ...r.settings }; S.cfgDirty = false; S.newKey = ''; S.newFalKey = ''; S.keyTest = null; S.lab = null;
  S.env = { ...S.env, ...(await api('/api/env')) };
  S.whisper = !!r.settings.whisper; S.hd = !!r.settings.render_hd;
  render(); toast(tr('设置已保存'));
}


/* ---------------------------------------------------------------- 运行日志 */
const LOG_LEVELS = [['DEBUG', '全部'], ['INFO', '运行信息'], ['WARNING', '警告和报错'], ['ERROR', '只看报错'], ['USAGE', 'Token 用量']];
async function refreshLogs() {
  if (S.logLevel === 'USAGE') return refreshUsage();
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
  $('#logsBtn').title = err > 0 ? tr('有 {0} 条新报错', err) : tr('有 {0} 条新警告', warn);
}
/* ---- Token 用量：logs/usage.jsonl 的汇总 */
async function refreshUsage() {
  try { S.usage = await api('/api/usage?days=30'); }
  catch (e) { S.usage = { error: /not found|404/i.test(e.message) ? tr('后端还是旧版本，没有用量接口。重启 studio 后再看。') : e.message }; }
  if (S.page === 'logs' && S.logLevel === 'USAGE') renderUsage();
}
const fmtTok = (n) => (n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 1 : 2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k' : String(n || 0));
function usageTile(label, x) {
  return `<div class="utile"><span>${tr(label)}</span><b title="${(x.in + x.out).toLocaleString()}">${fmtTok(x.in + x.out)}</b>
    <small>${tr('输入 {0} · 输出 {1}', fmtTok(x.in), fmtTok(x.out))} · ${tr('{0} 次调用', x.calls)}</small>
    ${x.video_sec ? `<small class="ok-text">${tr('视频生成 {0} 秒 · 约 ${1}', x.video_sec, (+x.cost).toFixed(2))}</small>` : ''}</div>`;
}
function usageTable(title, rows, nameLabel) {
  if (!rows.length) return '';
  const max = Math.max(...rows.map((r) => r.in + r.out), 1);
  return `<section class="ucard"><h3>${tr(title)}</h3><table class="utable"><thead><tr><th>${tr(nameLabel)}</th><th>${tr('调用')}</th><th>${tr('输入')}</th><th>${tr('输出')}</th><th>${tr('合计')}</th></tr></thead><tbody>
    ${rows.slice(0, 12).map((r) => `<tr><td><span class="ubar" style="--w:${Math.round(100 * (r.in + r.out) / max)}%"></span><span class="uname">${esc(tr(r.name))}</span></td><td>${r.calls}</td><td>${fmtTok(r.in)}</td><td>${fmtTok(r.out)}</td><td><b>${fmtTok(r.in + r.out)}</b></td></tr>`).join('')}
  </tbody></table></section>`;
}
function renderUsage() {
  const box = $('#logBox'); if (!box) return;
  const u = S.usage;
  if (!u) { box.innerHTML = `<div class="muted" style="padding:16px">${tr('正在读取…')}</div>`; return; }
  if (u.error) { box.innerHTML = `<div class="err" style="margin:16px">${esc(u.error)}</div>`; return; }
  if (!u.records) {
    box.innerHTML = `<div class="empty" style="margin:16px"><h2>${tr('还没有用量记录')}</h2><p style="margin:0;max-width:520px;line-height:1.7">${tr('从这个版本开始，每次调用 Claude 都会记一笔（看片、编故事、改剪辑、写拍摄脚本）。离线模拟不花钱，不记。')}</p></div>`;
    return;
  }
  const T = u.totals, maxDay = Math.max(...u.by_day.map((d) => d.in + d.out), 1);
  box.innerHTML = `<div class="usage">
    <div class="utiles">${usageTile('今天', T.today)}${usageTile('近 7 天', T.week)}${usageTile('本月', T.month)}${usageTile('全部', T.all)}</div>
    <section class="ucard"><div class="toolbar" style="justify-content:space-between"><h3>${tr('近 30 天')}</h3>
      <span class="ulegend"><i class="in"></i>${tr('输入')} <i class="out"></i>${tr('输出')}</span></div>
      <div class="uchart" role="img" aria-label="${tr('近 30 天每天的 token 用量')}">${u.by_day.map((d) => {
        const tot = d.in + d.out, h = Math.round(100 * tot / maxDay), hi = tot ? Math.round(h * d.in / tot) : 0;
        return `<div class="ucol" title="${esc(d.day)} · ${tr('输入 {0} · 输出 {1}', d.in.toLocaleString(), d.out.toLocaleString())} · ${tr('{0} 次调用', d.calls)}">
          <div class="ustack" style="height:${h}%"><i class="out" style="flex:${h - hi}"></i><i class="in" style="flex:${hi}"></i></div>
          <span>${d.day.slice(8)}</span></div>`;
      }).join('')}</div></section>
    <div class="ugrid">${usageTable('按项目', u.by_project, '项目')}${usageTable('按任务', u.by_step, '任务')}${usageTable('按模型', u.by_model, '模型')}</div>
    <section class="ucard"><h3>${tr('最近的任务')}</h3><table class="utable"><thead><tr><th>${tr('开始时间')}</th><th>${tr('任务')}</th><th>${tr('项目')}</th><th>${tr('调用')}</th><th>${tr('输入')}</th><th>${tr('输出')}</th></tr></thead><tbody>
      ${u.tasks.map((x) => `<tr><td class="mono">${esc((x.started || '').replace('T', ' ').slice(5, 16))}</td><td>${esc(tr(x.job) || '—')}</td><td>${esc(x.project || '—')}</td><td>${x.calls}</td><td>${fmtTok(x.in)}</td><td>${fmtTok(x.out)}</td></tr>`).join('')}
    </tbody></table></section>
    <p class="help">${tr('输入包含图片（静帧）和提示文字；离线模拟不计。记录在 {0}。', `<span class="mono">${esc(u.file)}</span>`)}</p>
  </div>`;
}
function viewLogs() {
  const c = S.logCounts || {};
  const usage = S.logLevel === 'USAGE';
  return `<div class="logs-wrap">
    <div class="logs-head">
      <h1>${tr('运行日志')}</h1>
      <div class="seg" role="group" aria-label="${tr('显示哪些日志')}" style="grid-template-columns:repeat(5,minmax(0,1fr))">
        ${LOG_LEVELS.map(([v, l]) => `<button type="button" data-loglevel="${v}" aria-pressed="${S.logLevel === v}">${tr(l)}</button>`).join('')}</div>
      ${usage ? '' : `<label for="logSearch" class="sr">${tr('搜索日志')}</label>
      <input id="logSearch" class="field" type="search" style="width:240px;min-height:38px" placeholder="${tr('搜索：项目名、401、镜头 id…')}" value="${esc(S.logQ)}">`}
      <label class="check" style="min-height:38px"><input type="checkbox" id="logAuto" ${S.logAuto ? 'checked' : ''}>${tr('自动刷新')}</label>
    </div>
    <div class="logs-box ${usage ? 'usage-box' : ''}" id="logBox"></div>
    <div class="logs-foot">
      <span>${countsHtml(c)}</span>
      <span style="flex:1">${tr('日志文件：{0}（同样的内容也显示在运行 studio 的终端里）', `<span class="mono">${esc((S.logs && S.logs.file) || 'logs/vlogai.log')}</span>`)}</span>
      <button type="button" class="ghost sm" data-act="logs-refresh">${tr('刷新')}</button>
      <button type="button" class="ghost sm" data-act="logs-open">${tr('打开日志文件夹')}</button>
    </div>
  </div>`;
}
function countsHtml(c) {
  return tr('本次启动以来：{0} 条报错 · {1} 条警告', `<b style="color:var(--danger)">${c.error || 0}</b>`, `<b style="color:var(--accent)">${c.warning || 0}</b>`);
}
function logRowHtml(e) {
  const lines = e.message.split('\n');
  const first = lines[0], rest = lines.slice(1).join('\n');
  const src = e.source === 'vlogai' ? '' : `<span style="color:var(--faint)">${esc(e.source)} </span>`;
  return `<div class="lrow ${esc(e.level)}"><span class="t" title="${esc(e.time)}">${esc(e.time.slice(11))}</span><span class="lv">${esc(e.level === 'WARNING' ? 'WARN' : e.level)}</span>
    <div class="m">${src}${rest ? `<details><summary>${esc(first)}</summary><pre>${esc(rest)}</pre></details>` : esc(first)}</div></div>`;
}
function renderLogRows(force) {
  if (S.logLevel === 'USAGE') return renderUsage();
  const box = $('#logBox'); if (!box) return;
  if (!S.logs) { box.innerHTML = `<div class="muted" style="padding:16px">${tr('正在读取…')}</div>`; return; }
  if (S.logs.error) { box.innerHTML = `<div class="err" style="margin:16px">${esc(tr('读取日志失败：{0}。如果刚更新过代码，请重启 studio。', S.logs.error))}</div>`; return; }
  const atBottom = force || box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  const open = new Set([...box.querySelectorAll('details[open]')].map((d) => d.parentElement.parentElement.dataset.k));
  box.innerHTML = S.logs.entries.length
    ? S.logs.entries.map((e, i) => logRowHtml(e).replace('<div class="lrow', `<div data-k="${esc(e.time + i)}" class="lrow`)).join('')
    : `<div class="muted" style="padding:16px">${tr('没有符合条件的日志。')}</div>`;
  box.querySelectorAll('[data-k]').forEach((r) => { if (open.has(r.dataset.k)) { const d = r.querySelector('details'); if (d) d.open = true; } });
  if (atBottom) box.scrollTop = box.scrollHeight;
  const foot = document.querySelector('.logs-foot span');
  const c = S.logCounts || {};
  if (foot) foot.innerHTML = countsHtml(c);
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
  if (r.planOnly) return newPlanProject(r.name);
  await startJob('/api/projects', { footage: r.path, name: r.name }, () => {
    location.hash = '#footage';
    startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper });
  });
}
async function restore(n) {
  if (!(await confirmBox(tr('恢复 v{0}？', n), tr('当前版本不会丢，恢复会另存成一个新版本。'), tr('恢复')))) return false;
  const r = await act(() => api(`${P()}/restore`, { n }));
  if (!r) return false;
  S.tlDirty = false; await loadProject(); render(); toast(tr('已恢复 v{0}', n));
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
    const s = S.byId[S.fsel]; const on = d.flag === 'must' ? !s.must : d.flag === 'exclude' ? !s.excluded : !s.unprotected;
    const r = await act(() => api(`${P()}/flag`, { shot: s.id, flag: d.flag, on }));
    if (r) {
      const sh = await api(`${P()}/shots`).catch(() => null);
      if (sh) setShots(sh.shots);
      else for (const x of S.shots) { x.must = r.flags.must.includes(x.id); x.excluded = r.flags.exclude.includes(x.id); }
      S.p.flags = { must: r.flags.must.length, exclude: r.flags.exclude.length };
      $('#fDetail').innerHTML = detailHtml();
      const card = document.querySelector(`.card[data-shot="${CSS.escape(s.id)}"]`);
      if (card) card.outerHTML = cardHtml(S.byId[s.id] || s);
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
  if (d.uilang) return setUiLang(d.uilang);
  if (d.loglevel) {
    const switching = (d.loglevel === 'USAGE') !== (S.logLevel === 'USAGE');   // 用量和日志的版面不一样，要整页重画
    S.logLevel = d.loglevel; store('loglevel', S.logLevel);
    if (switching) { render(); return refreshLogs(); } document.querySelectorAll('[data-loglevel]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.loglevel === S.logLevel)); return refreshLogs(); }
  if (d.scroll) { const el = document.getElementById('sec-' + d.scroll); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  if (d.set) { setDraft(d.set, d.val); return render(); }
  if (d.toggle) { setDraft(d.toggle, !S.draft[d.toggle]); return render(); }
  if (d.cfgdef) { setDraft(d.cfgdef, S.cfg.defaults[d.cfgdef]); return render(); }
  if (d.planday != null) { S.planDay = +d.planday; return render(); }
  if (d.labtool) { const L = labState(); L.tool = d.labtool; render(); return labLoadMedia(); }
  if (d.lbset) { const L = labState(); L[d.lbset] = isNaN(+d.val) ? d.val : +d.val; const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml();
    document.querySelectorAll(`[data-lbset="${d.lbset}"]`).forEach((b) => b.setAttribute('aria-pressed', b.dataset.val === d.val)); return; }
  if (d.face != null) { const L = labState(); cropSetEditable(labFaceBox(L.faces[+d.face], cropImgAR())); cropDraw(); const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml(); return; }
  if (d.labgo) {
    const L = labState();
    if (d.fromClip != null) {          // 从时间线进来：用这段的入出点，做好以后放在它后面
      const c = S.tl[+d.fromClip];
      L.tool = d.labgo; location.hash = '#lab';
      return labPick(c.shot_id, { a: +c.in, b: +c.out, t: +((+c.in + +c.out) / 2).toFixed(2), insert: +d.fromClip + 1 });
    }
    L.tool = d.labgo; location.hash = '#lab';
    return labPick(d.shotId);
  }
  if (d.labgap != null) {
    const L = labState(); L.tool = 'fill'; L.gap = d.labgap; L.note = d.labgap; L.prompt = '';
    location.hash = '#lab'; return;
  }
  if (d.labins) {
    const r = await act(() => api(`${P()}/lab_insert`, { id: d.labins }));
    if (r) { S.lab = r; await loadProject(); S.lab = r; render(); toast(tr('已放到时间线最后，去“时间线”里调整位置')); }
    return;
  }
  if (d.labdel) {
    if (!(await confirmBox(tr('删除这个片段？'), tr('文件也会一起删掉。'), tr('删除')))) return;
    const r = await act(() => api(`${P()}/lab_delete`, { id: d.labdel }));
    if (r) { S.lab = r; render(); }
    return;
  }
  if (d.prefadd != null) { const li = t.closest('[data-pday]'); return pickPlanRef(S.planEdit.days[+li.dataset.pday].shots[+li.dataset.pidx].shot_id); }
  if (d.prefdel != null) {
    const li = t.closest('[data-pday]'); const sh = S.planEdit.days[+li.dataset.pday].shots[+li.dataset.pidx];
    if (!(await confirmBox(tr('删除这张参考图？'), tr('图片文件也会一起删掉。'), tr('删除')))) return;
    if (S.planDirty && !(await savePlan(true))) return;
    const r = await act(() => api(`${P()}/plan_ref_del`, { shot_id: sh.shot_id, name: d.prefdel }));
    if (r) { S.plan = r; planCopy(); render(); }
    return;
  }
  if (d.pmust != null || d.pdel != null) {
    const li = t.closest('[data-pday]'); const di = +li.dataset.pday, si = +li.dataset.pidx;
    const shots = S.planEdit.days[di].shots;
    if (d.pdel != null) shots.splice(si, 1); else shots[si].must_have = !shots[si].must_have;
    planChanged(); return render();
  }
  if (d.bmove || d.bdel != null) {
    const i = +t.closest('[data-beat]').dataset.beat;
    if (d.bmove) { const j = i + +d.bmove; [S.outline.beats[i], S.outline.beats[j]] = [S.outline.beats[j], S.outline.beats[i]]; }
    else S.outline.beats.splice(i, 1);
    outlineChanged(); return render();
  }

  switch (d.act) {
    case 'new-project': return newProject();
    case 'new-plan-project': return newPlanProject('');
    case 'make-plan': {
      const brief = $('#pBrief').value.trim();
      if (!brief) { toast(tr('先写一下这趟旅行：去哪、和谁、想要什么感觉'), true); return; }
      if (S.plan && !(await confirmBox(tr('重新生成拍摄脚本？'), tr('现在的脚本、勾选记录和参考图都会被替换。'), tr('重新生成')))) return;
      S.planDirty = false;
      return startJob(`${P()}/plan`, { brief, days: +$('#pDays').value || 3, gear: $('#pGear').value.trim(), minutes: +(S.p.settings.minutes || 3), mock: mockOn() });
    }
    case 'plan-add-shot': {
      const day = S.planEdit.days[S.planDay];
      day.shots = day.shots || [];
      day.shots.push({ shot_id: `d${day.day || S.planDay + 1}x${String(Date.now()).slice(-5)}`, beat: '', what: '', framing: 'medium', hold: '5-8s', why: '', must_have: false });
      planChanged(); render();
      { const tas = document.querySelectorAll('.pshot textarea'); const last = tas[tas.length - 1]; if (last) last.focus(); }
      return;
    }
    case 'plan-save': return savePlan();
    case 'plan-discard': planCopy(); return render();
    case 'plan-to-story': {
      if (S.outline && S.outline.beats && S.outline.beats.length && !(await confirmBox(tr('用拍摄脚本替换现在的故事线？'), tr('现在的分幕会被替换（还没按“按这个结构剪”的修改也会丢掉）。'), tr('替换')))) return;
      const beats = (S.plan.beats || []).map((b, i, arr) => ({ id: b.id || 'b' + (i + 1), title: b.title || '', intent: b.intent || '',
        evidence: tr('来自拍摄脚本'), seconds: Math.max(5, Math.round((+(S.p.settings.minutes || 3) * 60) / arr.length)), shot_ids: [] }));
      S.outline = { title: S.plan.title || '', logline: S.plan.logline || '', beats, missing: [], notes: '' };
      outlineChanged();
      if (location.hash === '#story') render(); else location.hash = '#story';
      toast(tr('已把拍摄脚本的分幕放进故事线，修改后按“按这个结构剪”'));
      return;
    }
    case 'show-protected': S.filters.prot = true; return;
    case 'lab-choose': {
      const id = await pickShot(labState().shot);
      if (id) labPick(id);
      return;
    }
    case 'lab-make': return labMake();
    case 'crop-track': return cropTrack();
    case 'crop-play': {
      const v = $('#lbVideo'), L = labState(); if (!v) return;
      if (v.paused) { if (v.currentTime >= L.b - 0.05 || v.currentTime < L.a) v.currentTime = L.a; v.play(); } else v.pause();
      return;
    }
    case 'lab-prompt': {
      const L = labState(); L.busy = 'prompt'; const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml();
      const r = await act(() => api(`${P()}/lab_prompt`, { gap: (L.note || L.gap || '').trim(), shot_id: L.shot, mock: mockOn() }));
      L.busy = ''; if (r) L.prompt = r.prompt;
      if (f) $('#lbForm').innerHTML = labFormHtml();
      return;
    }
    case 'lab-faces': {
      const L = labState(); L.busy = 'faces'; $('#lbForm').innerHTML = labFormHtml();
      const vv = $('#lbVideo');
      const r = await act(() => api(`${P()}/lab_faces`, { shot_id: L.shot, t: vv ? vv.currentTime : L.t, mock: mockOn() }));
      L.busy = '';
      if (r) {
        L.faces = r.faces; L.faceMethod = r.method;
        const s = S.byId[L.shot], img = s.size && s.size[0] ? s.size : [16, 9];
        if (r.faces.length) cropSetEditable(labFaceBox(r.faces[0], cropImgAR()));
        if (r.method === 'none') toast(tr('这台电脑的 OpenCV 不支持找脸，填了 API Key 后会改用 AI 找；也可以手动框'), true);
      }
      labMediaRender(); $('#lbForm').innerHTML = labFormHtml();
      return;
    }
    case 'lab-set-a': case 'lab-set-b': {
      const v = $('#lbVideo'); if (!v) return;
      const L = labState(); L[d.act === 'lab-set-a' ? 'a' : 'b'] = +v.currentTime.toFixed(2);
      $('#lbForm').innerHTML = labFormHtml(); return;
    }
    case 'lab-play-range': {
      const v = $('#lbVideo'), L = labState(); if (!v) return;
      v.currentTime = L.a; v.play();
      const stop = () => { if (v.currentTime >= L.b) { v.pause(); v.removeEventListener('timeupdate', stop); } };
      v.addEventListener('timeupdate', stop); return;
    }
    case 'toggle-key': S.showKey = !S.showKey; return render();
    case 'test-key': {
      S.keyTest = 'testing'; render();
      const r = await act(() => api('/api/settings/test', { api_key: S.newKey || undefined }));
      S.keyTest = r || { ok: false, reason: tr('请求失败') }; return render();
    }
    case 'font-auto': setDraft('font', ''); return render();
    case 'open-projects': await act(() => api('/api/settings/open_projects', {})); return;
    case 'cfg-save': return saveSettingsPage();
    case 'logs-refresh': return refreshLogs();
    case 'logs-open': await act(() => api('/api/logs/open', {})); return;
    case 'cfg-retry': S.cfgError = null; await loadSettings(); return render();
    case 'cfg-discard': S.draft = { ...S.cfg.settings }; S.cfgDirty = false; S.newKey = ''; S.newFalKey = ''; S.keyTest = null; return render();
    case 'cfg-reset': {
      if (!(await confirmBox(tr('恢复默认设置？'), tr('API Key 不受影响，其他设置都回到默认值。'), tr('恢复默认')))) return;
      const r = await act(() => api('/api/settings', { reset: true }));
      if (r) { S.cfg = r; S.draft = { ...r.settings }; S.cfgDirty = false; render(); toast(tr('已恢复默认设置')); }
      return;
    }
    case 'rescan': {
      const r = await browse('video', S.p.footage);
      if (!r) return;
      return startJob('/api/projects', { footage: r.path, name: S.pid }, () => startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper }));
    }
    case 'analyze':
      if (!mockOn() && !(await confirmBox(tr('开始 AI 看片？'), tr('AI 会逐个镜头看一遍，按镜头数计费；已经看过的镜头不会重复计费。'), tr('开始')))) return;
      return startJob(`${P()}/analyze`, { mock: mockOn(), whisper: S.whisper, subject: (($('#fSubject') || {}).value || '').trim() });
    case 'pick-music': {
      const r = await browse('audio', (S.p.settings || {}).music || '');
      if (r && r.path) { await act(() => saveSettings({ music: r.path })); render(); }
      return;
    }
    case 'clear-music': await act(() => saveSettings({ music: '' })); return render();
    case 'outline': {
      if (S.outline && S.outline.beats.length && !(await confirmBox(tr('重新生成故事线？'), tr('现在的分幕和你的修改会被替换。'), tr('重新生成')))) return;
      const idea = $('#sBrief').value;
      S.outlineDirty = false; store('outline.' + S.pid, null);
      return startJob(`${P()}/outline`, { idea, minutes: +(S.p.settings.minutes || 3), mock: mockOn() });
    }
    case 'add-beat':
      S.outline.beats.push({ id: 'b' + Date.now(), title: tr('新的一幕'), intent: '', seconds: 20, shot_ids: [] });
      outlineChanged(); render();
      { const items = document.querySelectorAll('.beat .title'); const last = items[items.length - 1]; if (last) { last.focus(); last.select(); } }
      return;
    case 'make-story': {
      await act(() => saveSettings({ brief: $('#sBrief').value }));
      if (S.story && !(await confirmBox(tr('按这个结构重新剪一版？'), tr('之前的版本都保留在版本历史里。'), tr('开始剪')))) return;
      const ok = await startJob(`${P()}/story`, { beats: S.outline.beats, minutes: +(S.p.settings.minutes || 3), mock: mockOn() }, () => { location.hash = '#timeline'; });
      if (ok) { S.outlineDirty = false; store('outline.' + S.pid, null); }
      return;
    }
    case 'render': return startRender();
    case 'export-xml': return startJob(`${P()}/export`, {});
    case 'recheck': return startJob(`${P()}/recheck`, {});
    case 'open-folder': await act(() => api(`${P()}/open_folder`, {})); return;
    case 'del-clip': {
      const sh = S.byId[S.tl[S.tsel].shot_id];
      if (sh && sh.protected && !sh.excluded && !(await confirmBox(tr('删除受保护的镜头？'),
        tr('这个镜头受保护（{0}）。手动删除没问题，但下次让 AI 修改时它会被补回来。想彻底不用，请在“素材”里点“不保护这个镜头”。', tr(sh.protected)), tr('删除')))) return;
      S.tl.splice(S.tsel, 1); S.tsel = clamp(S.tsel, 0, S.tl.length - 1);
      return tlChanged(true);
    }
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
  if (t.id === 'lbA' || t.id === 'lbB') { labState()[t.id === 'lbA' ? 'a' : 'b'] = +t.value; return; }
  if (t.id === 'lbNote') { labState().note = t.value; return; }
  if (t.id === 'lbPrompt') { labState().prompt = t.value; return; }
  if (t.id === 'lbT') { labState().t = +t.value; const v = $('#lbTVal'); if (v) v.textContent = `${(+t.value).toFixed(1)}s`; return; }
  if (t.dataset.pf) {
    const li = t.closest('[data-pday]');
    const sh = S.planEdit.days[+li.dataset.pday].shots[+li.dataset.pidx];
    sh[t.dataset.pf] = t.value;
    if (t.dataset.pf === 'framing') {             // 换了景别，原来的草图就不对了：保存时按新景别重画
      sh.sketch = null; sh.sketch_svg = ''; sh.how = '';
      const box = li.querySelector('.psketch'); if (box) box.innerHTML = `<span class="note">${tr('保存后更新草图')}</span>`;
    }
    return planChanged();
  }
  if (t.id === 'cfgKey') { S.newKey = t.value.trim(); S.keyTest = null; settingsLive(); return; }
  if (t.id === 'cfgFal') { S.newFalKey = t.value.trim(); settingsLive(); return; }
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
    if (S.tlDirty && !(await confirmBox(tr('放弃未保存的时间线修改？'), tr('切换项目会丢掉还没保存的修改。'), tr('放弃并切换')))) { t.value = S.pid; return; }
    return switchProject(t.value);
  }
  if (t.id === 'mockToggle') { store('mock', t.checked); return render(); }
  if (t.id === 'uiLang') return setUiLang(t.value);
  if (t.dataset.cfg && t.type === 'checkbox') { setDraft(t.dataset.cfg, t.checked); if (t.dataset.cfg === 'use_proxy') return render(); settingsLive(); return; }
  if (t.dataset.cfg && t.type === 'number') { setDraft(t.dataset.cfg, t.value); return render(); }   // 失焦时刷新“默认 x”按钮
  if (t.id === 'fWhisper') { S.whisper = t.checked; return; }
  if (t.id === 'fSubject') { await act(() => saveSettings({ subject: t.value })); return; }
  if (t.id === 'pBrief' || t.id === 'pGear' || t.id === 'pDays') {
    const key = { pBrief: 'plan_brief', pGear: 'plan_gear', pDays: 'plan_days' }[t.id];
    await act(() => saveSettings({ [key]: t.value })); return;
  }
  if (t.id === 'lbT') { labState().faces = null; return labLoadMedia(); }
  if (t.id === 'lbA' || t.id === 'lbB') {         // 只更新时长提示：整块重画会把正要点的另一个输入框换掉
    const L = labState(), el = $('#lbDur'); if (el) el.textContent = tr('做出来约 {0} 秒，不带原声。', ((L.b - L.a) / L.speed).toFixed(1));
    if (L.tool === 'crop') {
      if (L.cr.mode === 'track') { L.cr.tracked = null; L.cr.keys = null; }
      cropEnsure();
      const r = $('#cropTime'); if (r) { r.min = L.a; r.max = L.b; }
      const v = $('#lbVideo'); if (v) { v.pause(); v.currentTime = L.a; }
    }
    return;
  }
  if (t.id === 'lbGap') { const L = labState(); L.gap = t.value; L.note = t.value; L.prompt = ''; const f = $('#lbForm'); if (f) f.innerHTML = labFormHtml(); return; }
  if (t.id === 'lbInsert') { labState().insert = t.value; return; }
  if (t.dataset.pcheck) {
    const r = await act(() => api(`${P()}/plan_check`, { shot_id: t.dataset.pcheck, done: t.checked }));
    if (r) {
      S.plan.done = r.done; S.planEdit.done = r.done;
      t.closest('.pshot').classList.toggle('done', t.checked);
      const pg = $('#planProgress'); if (pg) pg.innerHTML = planProgressHtml();
    } else t.checked = !t.checked;
    return;
  }
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
    if (!(await confirmBox(tr('先放弃手动修改吗？'), tr('AI 会在已保存的版本上修改，没保存的手动修改会丢掉。想留着的话先点“保存”。'), tr('放弃并继续')))) return;
    resetTimeline();
  }
  startJob(`${P()}/revise`, { instruction: v, minutes: +(S.p.settings.minutes || 3), mock: mockOn() });
});

/* 拖动排序：分幕和时间线镜头 */
let drag = null;
let planTarget = null;                    // 最近点过 / 指过的镜头，粘贴图片就加到它上面
document.addEventListener('pointerdown', (e) => { const li = e.target.closest && e.target.closest('.pshot'); if (li) planTarget = li; });
document.addEventListener('paste', (e) => {
  if (S.page !== 'plan' || !S.planEdit) return;
  const files = [...(e.clipboardData ? e.clipboardData.files : [])].filter((f) => f.type.startsWith('image/'));
  if (!files.length) return;
  e.preventDefault();
  const li = (document.activeElement && document.activeElement.closest('.pshot')) || (planTarget && planTarget.isConnected ? planTarget : null);
  if (!li) { toast(tr('先点一下要加图的镜头，再粘贴'), true); return; }
  addPlanRefs(S.planEdit.days[+li.dataset.pday].shots[+li.dataset.pidx].shot_id, files);
});
document.addEventListener('dragover', (e) => {
  if (drag || S.page !== 'plan' || !e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
  const li = e.target.closest('.pshot');
  document.querySelectorAll('.pshot.drop').forEach((x) => x !== li && x.classList.remove('drop'));
  if (li) { e.preventDefault(); li.classList.add('drop'); }
});
document.addEventListener('dragleave', (e) => { if (e.target.classList && e.target.classList.contains('pshot') && !e.target.contains(e.relatedTarget)) e.target.classList.remove('drop'); });
document.addEventListener('drop', (e) => {
  if (drag || S.page !== 'plan' || !e.dataTransfer || !e.dataTransfer.files.length) return;
  const li = e.target.closest('.pshot');
  document.querySelectorAll('.pshot.drop').forEach((x) => x.classList.remove('drop'));
  if (!li) return;
  e.preventDefault();
  addPlanRefs(S.planEdit.days[+li.dataset.pday].shots[+li.dataset.pidx].shot_id, e.dataTransfer.files);
});
document.addEventListener('toggle', (e) => {     // “更多参数”展开状态记在浏览器里
  const el = e.target; if (el.dataset && el.dataset.more) store('cfgMore.' + el.dataset.more, el.open);
}, true);
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
    if (!location.hash && S.p) location.hash = S.p.has_story ? '#timeline' : S.p.has_outline ? '#story' : (S.p.logged || S.p.footage) ? '#footage' : '#plan';
    render();
    const j = await getJob().catch(() => null);
    if (j && j.status === 'running') { S.job = j; S.jobSince = 0; pollJob(); }
    else if (j && j.status === 'error' && store('seenJobError') !== j.started) {
      // 刷新页面前任务失败了、还没看到原因：显示一次
      S.job = j; S.jobLines = (j.lines || []).slice(-400); S.jobSince = j.line_count || 0; renderJob();
    }
  } catch (e) {
    $('#app').innerHTML = e.offline
      ? `<div class="page"><div class="content"><div class="empty"><h2>${tr('连不上 vlogai 后端')}</h2>
      <p style="margin:0">${tr('先在终端运行 {0}，再刷新这个页面。', '<span class="mono">python -m vlogai.studio</span>')}</p><p class="err">${esc(e.message)}</p></div></div></div>`
      : `<div class="page"><div class="content"><div class="empty"><h2>${tr('页面加载出错')}</h2>
      <p style="margin:0">${tr('后端在运行，但读取数据时出错了。刷新试试；还不行就看看运行 studio 的终端里的报错。')}</p><p class="err">${esc(e.message)}</p>
      <div class="toolbar" style="justify-content:center"><button type="button" class="btn" onclick="location.reload()">${tr('刷新')}</button></div></div></div></div>`;
    console.error(e);
  }
})();
