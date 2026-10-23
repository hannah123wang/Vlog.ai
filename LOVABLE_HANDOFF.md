# vlogai — 交接文档（给 Lovable 用）

> 一句话：**Lovable 做界面，本地 Python 做视频引擎。**
> Lovable 生成的是浏览器里的 React 应用，它没有 ffmpeg、也读不到你硬盘上的 52GB 素材，
> 所以所有和视频有关的活（扫描、切镜头、抽帧、渲染、导 XML）必须留在本地后端。

```
┌─────────────────────────┐        HTTP (CORS 已开)        ┌──────────────────────────┐
│  Lovable 前端 (React)   │  ───────────────────────────▶  │ 本地 Python 后端         │
│  跑在浏览器             │  ◀───────────────────────────  │ http://127.0.0.1:8900    │
│  负责：界面 / 交互      │        JSON + 图片 + 视频       │ 负责：ffmpeg / Claude API │
└─────────────────────────┘                                 └──────────────────────────┘
                                                              素材和原片始终不离开本机
```

后端代码已经写好并跑通了，在 `~/Downloads/vlog-ai/vlogai/`。启动：

```bash
cd ~/Downloads/vlog-ai && source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...        # 真的 key，不是这行占位符
python -m vlogai.studio --port 8900
```

---

## 第一部分：直接粘进 Lovable 的提示词

> 下面整段复制给 Lovable。它需要的 API 契约和数据结构在第二、三部分，
> 如果 Lovable 问起来，把对应段落再贴给它。

```
Build a desktop-first web app called "vlogai studio" — an AI assistant editor for travel videos.

CONTEXT
The app is a front-end only. All video processing happens in a local Python backend the user
runs on their own machine at http://127.0.0.1:8900 (configurable in a settings field, stored in
localStorage, default http://127.0.0.1:8900). The backend allows requests from Lovable domains and
localhost only, and every POST must send the header Content-Type: application/json. Never try to process
video in the browser. If the backend is unreachable, show a calm "本地引擎没有连上" state with the
command to start it, and offer a DEMO MODE that loads bundled sample JSON instead.

THE PRODUCT
A traveller comes home with hundreds of clips and never edits them. This app: imports a folder,
lets them describe the story they want in their own words, has AI watch every shot and propose a
beat structure grounded in what the footage actually contains, cuts a rough edit to that
structure, and exports an editable timeline to DaVinci Resolve / Premiere. The human keeps
creative control at every step — the AI never hands back a locked video.

SCREENS

1. HOME
   - Hero: "把一堆素材，变成一个有故事的片子" with the 5-step flow as a subtitle.
   - Left card "新建项目": a server-side folder browser (GET /api/ls?path=). Show current path,
     an "up" button, list of subfolders each with a video count badge. Selecting a folder with
     videos enables a name field + "开始扫描" button (POST /api/projects).
   - Right card "已有项目": list from GET /api/projects, each showing clip count, total minutes,
     number of days, and whether a rough cut exists.

2. PROJECT — a 4-step wizard with a persistent step nav (1 素材 / 2 故事线 / 3 时间线 / 4 导出)
   and a project stats line under the title.

   STEP 1 — 素材
   - Card explaining the analysis and a "开始分析" button (POST .../analyze). Checkbox for
     speech transcription. Copy must reassure: only 512px stills are sent to the AI,原片不离开本机.
   - After analysis: a responsive thumbnail grid of every shot (GET .../shots) with the AI's
     one-line summary, day/time, duration, a highlight score badge (★1-10), shot type, and a
     "废片" badge when quality < 0.25. Filters: by day, and a "隐藏废片" toggle.

   STEP 2 — 故事线  ← THE HEART OF THE PRODUCT, make it feel like a conversation
   - A large textarea: "你想讲一个什么样的故事？" with a target-length input in minutes.
   - "让 AI 提出分幕" (POST .../outline) returns beats. Render each beat as an editable card:
     title (inline editable), seconds (number), intent (textarea), and a muted line showing the
     footage evidence + candidate shot count. Beats can be reordered (up/down), deleted, added.
   - If the response has a non-empty "missing" array, show a prominent amber callout:
     "你的想法里这些内容，素材中没有找到" and list them. This honesty is a feature — make it
     look intentional and friendly, not like an error.
   - A primary button "按这个结构剪 →" (POST .../story with the edited beats).

   STEP 3 — 时间线
   - Grouped by chapter (beat). Each segment row: thumbnail, AI summary, the editor's note
     ("✂️ why"), start timecode + duration, and controls: −1s / +1s, move up, move down, delete.
   - Edits are local until "保存我的修改" (POST .../timeline) — show that button only when dirty.
   - A text input for "一句话改" + button (POST .../revise), e.g. "开头更有冲击力，少一点空镜".

   STEP 4 — 导出
   - Render preview: optional music file path, 竖屏 / 1080p toggles, "渲染 mp4" (POST .../render).
   - "导出时间线 XML" (POST .../export) with a short explanation of how to import into
     Resolve (File > Import > Timeline) and Premiere (File > Import).
   - If a preview exists, an HTML5 <video> pointing at the media URL.

3. GLOBAL JOB BAR
   Every long action starts a background job. Poll GET /api/job?since=<lines already shown> every
   ~900ms. Show a fixed bottom bar with the job name, a progress bar (determinate when the
   response has pct, indeterminate otherwise), done/total, and an expandable log console. On
   error, auto-expand the log. On success, refresh the relevant data and fade out after ~2s.
   Only one job can run at a time; the backend returns 409 if another is running.

DESIGN DIRECTION
Dark, cinematic, editorial — like a colour-grading suite, not a SaaS dashboard.
Near-black background (#0c0d10), slightly lifted panels (#15171c), hairline borders (#272b34),
one warm accent (#ff8a5b, a sunset orange) used sparingly for primary actions, the active step
and chapter headings, plus a cool secondary (#5bc8ff) for data/counts. Generous whitespace,
tabular numerals for timecodes, thumbnails always 16:9 with object-fit: cover on black.
Typography: system stack with "PingFang SC" for Chinese. All UI copy in Simplified Chinese.
No gradients-as-decoration, no drop shadows on cards, no rounded-everything — restraint reads
as professional. The storyboard rows and beat cards are the signature screens; make those sing.

STATES TO HANDLE
- backend unreachable → calm empty state + start command + demo-mode button
- no API key (GET /api/env returns has_key:false) → a pill in the header saying 只能离线模拟,
  and send mock:true on every AI call
- project with no analysis yet → step 2 and 3 disabled with a hint
- job already running (409) → toast "已经有任务在跑"
```

---

## 第二部分：API 契约

Base URL：`http://127.0.0.1:8900`（可配置）。所有响应都是 JSON；出错时是 `{"error": "..."}` 加非 200 状态码。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/env` | `{has_key, home, cwd}` — 有没有 API key、用户 home 路径 |
| GET | `/api/ls?path=` | 文件夹浏览器（浏览器拿不到真实路径，必须由后端提供） |
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | `{footage, name}` → 新建并开始扫描 |
| GET | `/api/projects/{id}` | 单个项目的汇总状态 |
| GET | `/api/projects/{id}/shots` | 全部镜头 + 缩略图 URL + AI 日志 |
| GET | `/api/projects/{id}/outline` | 当前分幕 |
| GET | `/api/projects/{id}/story` | 当前时间线 |
| POST | `/api/projects/{id}/analyze` | `{mock, whisper, workers}` → 后台任务 |
| POST | `/api/projects/{id}/outline` | `{idea, minutes, mock}` → 后台任务 |
| POST | `/api/projects/{id}/story` | `{beats, minutes, mock}` → 后台任务 |
| POST | `/api/projects/{id}/revise` | `{instruction, minutes, mock}` → 后台任务 |
| POST | `/api/projects/{id}/timeline` | `{timeline:[{shot_id,in,out}]}` → 立即返回新时间线（非任务） |
| POST | `/api/projects/{id}/render` | `{music, vertical, hd}` → 后台任务 |
| POST | `/api/projects/{id}/export` | → 后台任务，产出 timeline.xml |
| GET | `/api/job?since=N` | 当前任务状态，`since` 是已经显示过的日志行数 |
| GET | `/media/{id}/...` | 缩略图 jpg / preview.mp4（支持 Range，可以拖进度条） |

**所有 POST 任务类接口返回 `{"started":"<action>"}` 就结束了**，真正的进度要轮询 `/api/job`。
同一时间只能跑一个任务，冲突时返回 409。

`sample-data/` 文件夹里是每个 GET 接口的真实返回样本，直接喂给 Lovable 最省事。

---

## 第三部分：数据结构

**GET /api/projects/{id}**
```json
{"id":"drone-apr","name":"drone-apr","clips":248,"shots":314,"logged":314,
 "duration_min":63.6,"days":6,"unusable":41,
 "has_outline":true,"beats":5,"has_story":true,"story_title":"...",
 "story_seconds":2100,"story_shots":230,"has_preview":false,"has_xml":true,
 "settings":{"footage":"/Users/hannah/Downloads/footage-drone-apr","minutes":35,"brief":"..."}}
```

**GET /api/projects/{id}/shots** → `{"shots":[...]}`
```json
{"id":"c0012_s02","day":2,"time":"14:03","duration":8.4,"quality":0.87,
 "thumb":"/media/drone-apr/frames/c0012/c0012_s02_1.jpg","file":"DJI_xxx.MP4",
 "summary":"邮轮甲板上的黄昏，海面反光","place":"邮轮甲板","shot_type":"wide",
 "highlight":8,"mood":"开阔 平静","people":["me"],"speech":""}
```

**GET /api/projects/{id}/outline**（分幕；前端可编辑后原样回传）
```json
{"title":"...","logline":"...",
 "beats":[{"id":"b1","title":"出发","intent":"建立期待感……","evidence":"Day1 机场和登船的 6 个镜头",
           "shot_ids":["c0003_s01"],"seconds":120}],
 "missing":["你提到的星空，素材里没有找到清晰的夜空镜头"],
 "notes":"最强的是 CocoCay 的水下素材，建议给它一整幕",
 "idea":"用户原话","target_seconds":2100}
```

**GET /api/projects/{id}/story** → 时间线
```json
{"title":"...","logline":"...","chapters":[{"name":"出发","purpose":"..."}],
 "total_seconds":2098.4,
 "timeline":[{"i":0,"shot_id":"c0003_s01","in":1.2,"out":5.0,"at":0.0,"dur":3.8,
              "chapter":"出发","role":"hook","why":"最有冲击力的起飞镜头","keep_audio":false,
              "text":"","thumb":"/media/.../xxx.jpg","summary":"...","shot_dur":9.1,
              "quality":0.9,"highlight":9}]}
```
`in`/`out` 是**相对该镜头起点**的秒数；`at` 是在成片中的位置；`shot_dur` 是这个镜头的总长度（前端拉伸时长时的上限）。

**GET /api/job**
```json
{"name":"看素材","project":"drone-apr","status":"running","lines":["[vlogai] ..."],
 "line_count":42,"done":120,"total":314,"pct":38,"error":"","started":"2026-10-17T19:02:46"}
```
`status` 是 `running | done | error | idle`。

---

## 第四部分：AI 提示词（全部四个，照抄）

模型分工：**逐镜头看素材用便宜快的模型，编故事用最强的模型。**

| 用途 | 默认模型 | 环境变量 | max_tokens |
|---|---|---|---|
| 镜头日志（量大，几百次调用） | `claude-haiku-4-5-20251001` | `VLOGAI_DESCRIBE_MODEL` | 900 |
| 分幕 / 铺时间线 / 修改 | `claude-sonnet-5` | `VLOGAI_STORY_MODEL` | 8000 ~ 48000（随片长） |

长片的 max_tokens 必须随目标时长放大，否则 35 分钟的时间线会被截断：
`max(8000, min(48000, 4000 + 140 × 预计片段数))`，预计片段数 = 目标秒数 ÷ 3.5（短片）或 ÷ 9（长片）。

### 4.1 镜头日志（每个镜头一次，带 1–4 张 512px 关键帧）

System：
```
You are an assistant editor logging raw travel-vlog footage.
You see a few keyframes from ONE shot (in time order, timestamps relative to the shot start)
plus metadata and any transcribed speech. Describe what is actually visible/audible — do not invent.
Reply with ONLY a JSON object, no prose.
```

User（图片在前，文字在后）：
```
<meta>{"file":"DJI_xxx.MP4","day":2,"recorded":"2026-04-04T14:03:00+00:00","gps":[25.77,-80.19],"duration":8.4,"quality":0.87,"speech":false}</meta>
Keyframe times (s, relative to shot start): [1.0, 4.2, 7.1]
Transcribed speech in this shot: (none)

Return JSON with exactly these keys:
{
 "summary": "one vivid sentence of what happens",
 "place": "best guess of the kind of place / landmark if recognisable, else ''",
 "people": ["who appears, e.g. 'me (selfie)', 'friend', 'street vendor'"],
 "activity": "what is happening",
 "mood": "emotional tone, 1-3 words",
 "shot_type": "one of: wide, medium, close-up, detail, selfie, pov, drone, timelapse, screen",
 "story_roles": ["any of: opener, establishing, travel-transition, arrival, food, moment, reaction, reflection, climax, ending, b-roll"],
 "highlight": 1-10 integer (how much a viewer would want to see this; blurry/shaky/nothing happening = low),
 "best_moment": {"start": seconds, "end": seconds}  (best continuous 2-8s part, relative to shot start, within 0..{dur}),
 "keep_audio": true if the original sound/speech is worth hearing in the final vlog,
 "tags": ["3-6 short search tags"]
}
Write all free-text values in {lang}.
```

### 4.2 分幕共创（整个产品最核心的一次调用）

System：
```
You are a documentary editor in the story-planning conversation with a creator.
They tell you roughly what the piece should be. You have already watched every shot (you are given
the log). Your job is to turn their rough idea into a concrete beat structure that THIS footage can
actually support — never propose a beat the footage can't carry, and tell them honestly what is
missing. Reply with ONLY JSON.
```

User：
```
The creator's rough idea for the piece:
<idea>{用户原话}</idea>

Target length: {秒} seconds. Pacing: {见下方节奏说明}
Write all text in {lang}.

Shot log (id, day, time, dur, q=technical quality 0-1, hl=highlight 1-10, type, roles, place, mood,
sum=what happens, best=best in/out, speech):
<shots_json>{全部镜头的精简 JSON}</shots_json>

Return JSON:
{
 "title": "a title for the piece",
 "logline": "one sentence: the story this tells",
 "beats": [
   {"id": "b1",
    "title": "short name of this beat",
    "intent": "what this beat does for the viewer emotionally, 1-2 sentences",
    "evidence": "the actual footage that supports it — name real moments from the log",
    "shot_ids": ["c0012_s01", "..."],
    "seconds": 45}
 ],
 "missing": ["anything the creator's idea asks for that the footage does NOT contain — be specific and honest, [] if nothing"],
 "notes": "one or two sentences of editorial advice: what is strongest here, what you'd protect"
}
The beats' seconds should add up to roughly the target length. Order them as they will appear.
```

**节奏说明**（按目标时长自动切换，这一条很关键，短片和长片的剪法完全不同）：
- 目标 ≤ 360 秒：`tight vlog cutting: b-roll shots 1.5-4 s, speech/reaction moments as long as the sentence needs. Cut anything that repeats.`
- 目标 > 360 秒：`long-form edit: let shots breathe (6-15 s each, the best ones longer), keep most of the trip rather than only the highlights, but still drop dead air, repeats and soft/shaky material, and keep a varied rhythm so it never feels like a slideshow.`

### 4.3 按确认的分幕铺时间线

System：
```
You are a senior documentary/travel editor. The creator has APPROVED a beat structure. Build the
actual cut that realises it, using only the logged shots. The structure is theirs — follow the beats
in order, honour each beat's intent and length, and do not invent new beats. Within a beat you
decide shot order, in/out points and rhythm: establish, then the moments, then a human beat; vary
shot sizes; never two near-identical shots back to back; prefer high 'hl' and 'q'; use shots with
worthwhile speech as anchors. Reply with ONLY JSON.
```

User：
```
Approved structure (follow it in this order):
<beats_json>{用户确认后的分幕}</beats_json>

The creator's own words about the piece: {用户原话}
Total target: <target_seconds>{秒}</target_seconds> seconds. Pacing: {节奏说明}
Aim for roughly {n} segments overall. Write text in {lang}.

Shot log:
<shots_json>{全部镜头}</shots_json>

Return JSON:
{
 "title": "...", "logline": "...",
 "chapters": [{"name": "beat title", "purpose": "..."}],
 "timeline": [
   {"shot_id": "c0001_s01", "in": 0.0, "out": 3.2, "chapter": "beat title",
    "role": "hook|establishing|moment|reaction|transition|ending",
    "why": "short editor note", "keep_audio": false, "text": ""}
 ]
}
'in'/'out' are seconds RELATIVE TO THE SHOT START, within 0..dur of that shot.
'chapter' must be one of the approved beat titles. A shot may be used at most twice.
```

### 4.4 一句话修改

System 用下面这段（也是不走分幕、直接出片时用的那个）：
```
You are a senior documentary/travel-vlog editor. From a log of raw shots you build a rough cut that
feels like a story, not a slideshow. Craft rules:
- COLD OPEN: 5-12 s hook built from the most striking or emotional moments (may be out of order).
- Then a title beat, then the trip in broadly chronological order, grouped into chapters
  (by day/place/theme). Each chapter: establish the place (wide) -> the moments (medium/close)
  -> a reaction or small human beat. Give the trip an emotional arc (anticipation, discovery,
  a peak, reflection) and a real ending (quiet, reflective, or a callback to the opening).
- Rhythm: follow the pacing note in the request. Vary shot sizes; never put two near-identical
  shots back to back. Prefer high 'hl' and 'q'. Shots with q < 0.25 are technically bad — use only
  if irreplaceable.
- Use shots with worthwhile speech as anchors (keep_audio=true) — they carry the narrative.
- Hit the target length within ±10%.
Reply with ONLY JSON, no prose.
```

User：
```
Here is the current cut (story.json) and the shot log it was built from.
Revise the cut according to the creator's instruction, keeping everything else that works.
Instruction: {用户那句话}

Target length: <target_seconds>{秒}</target_seconds> seconds (unless the instruction changes it).
Pacing: {节奏说明}
Write text in {lang}.

<current_story>{当前 story 的精简版}</current_story>
<shots_json>{全部镜头}</shots_json>

Return the full revised story in the same JSON format (title, logline, chapters, timeline).
```

### 返回值必须校验（后端已经做了，Lovable 不用管，但如果你重写后端要记得）
1. 丢掉不存在的 `shot_id`（模型偶尔会编）
2. `in`/`out` 夹到 `0..该镜头时长` 之间
3. 短于 0.8 秒的片段自动补到 1.5 秒，补不了就丢弃
4. 用 `shot.start + in` 换算成源文件里的绝对时间码，渲染和导 XML 都用这个

---

## 第五部分：本地引擎都做了什么（Lovable 不需要实现，了解即可）

| 步骤 | 技术 | 说明 |
|---|---|---|
| 扫描 | `ffprobe` | 时长、分辨率、帧率、旋转、拍摄时间、GPS，按天自动分组 |
| 切镜头 | PySceneDetect | 只对超过 20 秒的素材做，短素材整条当一个镜头 |
| 抽关键帧 | `ffmpeg` | 每个镜头 1–4 张 512px jpg，**只有这些小图会上传给 AI** |
| 质量打分 | OpenCV | 拉普拉斯方差（4×4 分块取 80 分位，避免把天空误判成虚焦）+ 曝光 |
| 语音 | faster-whisper | 可选，本地跑，中英文自动识别 |
| 渲染 | `ffmpeg` | 逐段裁切 → concat；竖屏素材配模糊背景；音乐自动卡点（librosa 测节拍）；说话时音乐侧链压缩自动压低 |
| 导出 | 手写 FCP7 XML | V1 画面 / A1 原声（AI 没选中的轨道静音但保留）/ A2 音乐，章节和剪辑理由写成 marker |

成本量级：每个镜头约 1–1.5k input tokens。314 个镜头的"看素材"按 Haiku 价格大约 1 美元；
编故事是一次调用，输入几万 tokens。

---

## 第六部分：两个一定会踩的坑

**1. Safari 不让 https 页面调 http://127.0.0.1。** Chrome 允许（loopback 被当作可信来源），Safari 不允许。
所以 **demo 一定要用 Chrome**。如果非要用 Safari，就得给本地后端配 https 证书，不值得在 hackathon 上折腾。

**2. 一定要做 DEMO 模式。** Demo Day 现场网络、端口、权限任何一个出问题，你的 pitch 就废了。
让 Lovable 把 `sample-data/` 里的 JSON 打包进前端，加一个"演示数据"开关：没有后端时自动切过去，
界面照样能走完四步、能看到真实的分幕和时间线。缩略图可以用你自己素材里导出的几十张小图。
这个保险成本很低，但能保证你在台上一定有东西可展示。

---

## 第七部分：建议的 Demo 流程（3 分钟）

1. **痛点**：打开 Finder，展示那 250 条 4K 素材、52GB、躺了半年没动过。（10 秒，这是最有说服力的开场）
2. **导入**：拖进 app，几秒后看到 248 条、63.6 分钟、6 天、41 条废片已标出。
3. **故事线**：敲一句"我和男朋友四月在迈阿密和邮轮上的旅行，夏日明媚，结尾落在迈阿密的雨天"，
   AI 返回五幕，每一幕都写着用哪些真实素材支撑——**并且诚实地告诉你"星空那段素材里没有"**。
   这是全场最该停留的一屏。
4. **改结构**：当场把第三幕删掉、第二幕拉长，点"按这个结构剪"。
5. **结果**：时间线出来，每个片段都有 AI 的理由；导出 XML，切到 Resolve，时间线真的打开了。
   强调：**我们给的是可以继续剪的时间线，不是一个锁死的 mp4。**

一句话收尾："别的工具帮你做一个卡点混剪。我们做的是助理剪辑师——它看完所有素材，和你一起定结构，
最后把决定权交还给你。"
