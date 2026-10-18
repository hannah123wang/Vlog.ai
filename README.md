# vlogai — an AI assistant editor for travel footage

Turn a folder of raw clips into a **story-driven rough cut**, then keep editing it — in the app, or
in DaVinci Resolve / Premiere, linked to your original full-quality files.

Most AI video tools hand you a beat-synced highlight reel you can't change. vlogai does the part
that actually takes the time — watching every clip and finding the story in it — and then gives the
decisions back to you: you write the story you want, the AI grounds it in what your footage really
contains, and the output is an **editable timeline**, never a locked MP4.

```
your footage folder
  │ 1  scan       read metadata: shot time, GPS, duration, orientation → auto-group into Day 1 / Day 2 …
  │ 2  frames     split long clips into shots, pull 1–4 keyframes (512px), score focus + exposure
  │ 3  transcribe (optional) local Whisper, auto-detects Chinese / English
  │ 4  describe   the AI watches every shot → a log entry (content, mood, shot type, highlight, best moment)
  │ 5  outline    you describe the story you want → the AI proposes beats grounded in the real footage
  │ 6  story      the approved beats become a full timeline, shot by shot
  │ 7  render     ffmpeg preview: captions, beat-synced cuts, music ducked under speech
  ▼ 8  export     FCP7 XML for Resolve / Premiere, relinked to your original files
```

**Your originals never leave your machine.** Only 512px stills and text are sent to the API, so
hundreds of gigabytes of 4K stay local, fast and cheap to work with. Every step is cached — if a run
stops halfway, re-running only does what's left and you never pay twice.

---

## Install

**macOS / Linux**
```bash
bash setup_mac.sh
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...        # create one at https://console.anthropic.com
```

**Windows**
```bat
setup_windows.bat
.venv\Scripts\activate
set ANTHROPIC_API_KEY=sk-ant-...
```

Neither script needs admin rights or installs anything system-wide — `static-ffmpeg` fetches a
private copy of ffmpeg into the virtualenv. If ffmpeg is already on your PATH it's used instead.
Python 3.9+ required. Optional: `pip install faster-whisper` to transcribe speech in your clips.

Teammates: see [CONTRIBUTING.md](CONTRIBUTING.md). API reference: [docs/API.md](docs/API.md).

<details>
<summary>Manual install, if you'd rather not run the script</summary>

## Install by hand (macOS)

You need ffmpeg. Either is fine:

```bash
brew install ffmpeg                      # A) if you have Homebrew
```
```bash
# B) no Homebrew (zsh: command not found: brew): skip the line above — the static-ffmpeg entry in
#    requirements.txt downloads its own ffmpeg/ffprobe into this project's virtualenv.
#    Nothing is installed system-wide and no password is needed.
```

Then:

```bash
cd vlog-ai
python3 -m venv .venv
source .venv/bin/activate                # run this in every new terminal
pip install -r requirements.txt
pip install faster-whisper               # optional: transcribe speech in your clips
```

API key — create one at https://console.anthropic.com

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # add it to ~/.zshrc to make it permanent
```

</details>

---

## Quick start: the app

```bash
python -m vlogai.studio --port 8900
```

Open http://127.0.0.1:8900 (it opens by itself). If the port is taken, the server picks the next
free one and prints the address it chose.

Four steps, left to right:

1. **素材 / Footage** — pick a folder, let the AI watch it, browse every shot with its log entry
2. **故事线 / Storyline** — write what you want in your own words; get back a beat structure you can
   edit, reorder and delete, with an honest list of anything your footage can't support
3. **时间线 / Timeline** — the cut, grouped by beat. Trim, reorder, delete, or ask for changes in a
   sentence ("open with something stronger, fewer empty landscapes")
4. **导出 / Export** — render an MP4 preview, or export the timeline to Resolve / Premiere

The backend has CORS enabled, so a separately hosted front end (Lovable, Vercel, anything) can talk
to it at `http://127.0.0.1:8900`. Use Chrome for that: Safari refuses HTTPS pages calling
`http://127.0.0.1`.

## Quick start: the command line

The same pipeline without the UI.

```bash
# dry run on synthetic clips — no API key, no cost
python make_test_footage.py test_footage
python -m vlogai run test_footage -w projects/test --mock --minutes 1 --music test_music.mp3

# the real thing
python -m vlogai run ~/Movies/Miami -w projects/miami \
  --brief "Me and James in Miami and on the cruise, bright summer, ends on a rainy day" \
  --minutes 4 --music ~/Music/bgm.mp3
```

`--brief` is the line worth writing carefully: who you were with, what the trip meant, what feeling
you want, where it should end. Everything else has a sensible default.

Other flags: `--vertical` (9:16), `--hd` (1080p preview), `--lang en`, `--no-snap` (don't align cuts
to the music). A project remembers the brief, length and music you last used.

Step by step, when you want to iterate on just one part:

```bash
python -m vlogai scan ~/Movies/Miami -w projects/miami
python -m vlogai frames      -w projects/miami
python -m vlogai transcribe  -w projects/miami
python -m vlogai describe    -w projects/miami
python -m vlogai story       -w projects/miami --brief "..." --minutes 4
python -m vlogai board       -w projects/miami      # storyboard.html
python -m vlogai render      -w projects/miami --music bgm.mp3
python -m vlogai export      -w projects/miami
```

The first four only ever run once. Iterating means `story` → look at the storyboard → `revise` →
`render`.

---

## What you get (inside `projects/<name>/`)

| File | What it's for |
|---|---|
| `storyboard.html` | **Look at this first** — every shot with its thumbnail, why the AI picked it, where it lands |
| `preview.mp4` | The rough cut |
| `timeline.xml` | Import into Resolve / Premiere |
| `story.json` | The edit decision list — plain JSON, safe to hand-edit |
| `outline.json` | The approved beat structure |
| `story_versions/` | Every version, so you can always go back |
| `logs/` | The AI's per-shot log — also a searchable index of your footage |

### Three ways to change the cut

**Ask for it in a sentence** (re-renders automatically):
```bash
python -m vlogai revise "stronger opening; drop the repeated sunsets; speed up day two" -w projects/miami
python -m vlogai revise "cut a 90-second vertical version" -w projects/miami --minutes 1.5 --vertical
```

**Edit `story.json` by hand** — delete lines, reorder, change `in`/`out` (seconds, relative to the
shot start), set `text` captions, toggle `keep_audio`. Then:
```bash
python -m vlogai recheck -w projects/miami      # re-validates, re-renders, re-exports
```

**Finish in a real NLE** (what you'll want for anything long):
- **DaVinci Resolve** (free): File → Import → Timeline → `timeline.xml`
- **Premiere Pro**: File → Import → `timeline.xml`
- Tracks: V1 picture, A1 original sound (muted where the AI chose not to keep it — enable it to get
  it back), A2 music. Beat names and the AI's reasoning come through as markers.
- Final Cut Pro isn't supported yet (it needs FCPXML rather than FCP7 XML).

---

## Models and cost

| Job | Default model | Env var |
|---|---|---|
| Per-shot logging (hundreds of calls) | `claude-haiku-4-5-20251001` | `VLOGAI_DESCRIBE_MODEL` |
| Storyline, timeline, revisions (one big call each) | `claude-sonnet-5` | `VLOGAI_STORY_MODEL` |

```bash
export VLOGAI_STORY_MODEL=claude-opus-5     # when the story matters more than the bill
```

Roughly 1–1.5k input tokens per shot. Logging 500 shots lands around $1–2 at Haiku's rate; the
storyline is a single call with tens of thousands of input tokens. Every run prints its token usage.
Check current pricing at anthropic.com.

---

## How it works under the hood

| Step | Tooling | Notes |
|---|---|---|
| Metadata | `ffprobe` | duration, fps, rotation, capture time, GPS; unreadable files are skipped, not fatal |
| Shot detection | PySceneDetect | only on clips longer than 20s; short clips are one shot |
| Keyframes | `ffmpeg` | 1–4 stills per shot at 512px — the only pixels that ever leave the machine |
| Quality score | OpenCV | Laplacian variance over a 4×4 grid (80th percentile, so a sharp horizon against smooth sky isn't mistaken for soft focus) plus an exposure term |
| Speech | faster-whisper | local, optional |
| Pacing | — | switches automatically: tight vlog cutting under 6 minutes, long-form breathing room above it |
| Beat sync | librosa | cut points slide ±40% to land on the beat, never on segments carrying speech |
| Render | `ffmpeg` | centre-crop to fill, blurred background for mismatched orientations, sidechain ducking under dialogue |
| Export | hand-written FCP7 XML | per-clip frame rates, source timecodes, markers |

Model output is validated before it ever reaches ffmpeg: invented shot IDs are dropped, in/out points
are clamped to the real shot bounds, sub-second fragments are widened or discarded, and everything is
converted to absolute source timecode so the preview and the XML always agree.

---

## Known limits, and what's worth building next

- **Story quality lives in the prompts** — `vlogai/story.py` and `vlogai/outline.py`. That's the heart
  of this project and the part most worth iterating on.
- Footage with no speech gives the AI less to work with. Recording a five-minute voice memo about the
  trip and dropping it in the folder (with transcription on) measurably improves the structure.
- Previews are hard cuts — no transitions or colour grading. That's deliberate; those belong in Resolve.
- iPhone HDR (Dolby Vision) clips can look washed out in the preview but import correctly.
- The `frames` step is the slow one on large 4K libraries. Raise `--workers`.
- Next up: proxy playback inside the timeline view, thumbnails on beat cards, draggable trim handles,
  FCPXML export for Final Cut, photos as well as video, place names from GPS, CLIP embeddings for
  near-duplicate removal.

## Layout

```
vlogai/
  cli.py          command line
  probe.py        1  scan + metadata
  frames.py       2  shot detection, keyframes, quality
  transcribe.py   3  Whisper
  describe.py     4  per-shot AI log  (prompt lives here)
  outline.py      5  storyline co-writing  (prompt lives here)
  story.py        6  timeline + revisions  (prompt lives here)
  beats.py           music beat detection
  render.py       7  ffmpeg rendering
  export_xml.py   8  FCP7 XML export
  storyboard.py      storyboard.html
  llm.py             Anthropic client + offline mock
  studio/            the local app: stdlib HTTP server + web UI, no extra dependencies
make_test_footage.py  synthetic clips for testing
```
