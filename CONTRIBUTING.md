# Working on vlogai

Hackathon repo. Short cycles, no ceremony — but read the two "please don't" items at the bottom
before you change anything in `render.py` or `export_xml.py`.

## Get it running (5 minutes)

**macOS / Linux**
```bash
git clone <this repo> && cd vlog-ai
bash setup_mac.sh
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...
```

**Windows**
```bat
git clone <this repo>
cd vlog-ai
setup_windows.bat
.venv\Scripts\activate
set ANTHROPIC_API_KEY=sk-ant-...
```

Neither script needs admin rights and neither installs anything system-wide: `static-ffmpeg`
downloads a private copy of ffmpeg/ffprobe into the virtualenv. If you already have ffmpeg on your
PATH, it's used instead.

**Check it works without spending a cent or needing a key:**
```bash
python make_test_footage.py test_footage
python -m vlogai run test_footage -w projects/test --mock --minutes 1 --music test_music.mp3
```
`--mock` replaces the AI with deterministic rules, so the whole pipeline runs offline. Use it for
every UI and plumbing change — only turn it off when you're specifically working on prompt quality.

Then the app:
```bash
python -m vlogai.studio --port 8900
```

## How the pieces fit

```
probe.py     ffprobe → clips.json          (duration, fps, rotation, capture time, GPS, day grouping)
frames.py    PySceneDetect + ffmpeg + OpenCV → shots.json + frames/*.jpg  (shots, keyframes, quality)
transcribe.py faster-whisper → transcripts/*.json                          (optional, local)
describe.py  keyframes + metadata → logs/<shot>.json   ← AI call #1, one per shot, cached on disk
outline.py   all logs + the user's idea → outline.json ← AI call #2, the beat structure
story.py     approved beats + all logs → story.json    ← AI call #3, the timeline
beats.py     librosa beat detection, used to nudge cut points
render.py    story.json → preview.mp4 (ffmpeg)
export_xml.py story.json → timeline.xml (FCP7 XML for Resolve/Premiere)
studio/      local HTTP server (stdlib only) + web UI that drives all of the above
```

Everything lives in `projects/<name>/` and every step is cached by file existence, so re-running is
cheap and safe. Deleting `logs/` forces a re-analysis; deleting `story.json` keeps the expensive
per-shot work and only re-cuts.

**`in` / `out` convention:** in `story.json` they are seconds *relative to the shot's start*.
`src_in` / `src_out` are absolute seconds in the source file and are derived, never authored by the
model. If you touch timeline code, keep that distinction — it's where bugs hide.

## Where the interesting work is

The prompts are the product. They're plain strings at the top of three files:

| File | Prompt | What it decides |
|---|---|---|
| `describe.py` | `SYSTEM` / `PROMPT` | how well the AI understands each shot |
| `outline.py` | `BEATS_SYSTEM` / `BEATS_PROMPT` | whether the proposed structure feels like a real editor's |
| `story.py` | `SYSTEM` / `PROMPT` / `REVISE_PROMPT` | pacing, rhythm, whether the cut has an arc |

Iterate like this: `python -m vlogai story -w projects/<name> --minutes 4`, open
`projects/<name>/storyboard.html`, read the "why" on each shot. Every run is versioned in
`story_versions/`, so you can compare two prompt variants on the same footage.

## Open tasks, roughly in order of impact

1. **Proxy playback in the timeline view** — generate 480p proxies per shot so the editor can press
   play on a segment instead of guessing from a thumbnail. Biggest felt gap in the UI.
2. **Thumbnails on beat cards** — step 2 currently describes its evidence in words only.
3. **Draggable trim handles** — step 3 only has −1s / +1s buttons.
4. **FCPXML export** for Final Cut (we only emit FCP7 XML today; the structure in `export_xml.py`
   maps over cleanly).
5. **Near-duplicate detection** — CLIP/SigLIP embeddings over keyframes to collapse the six nearly
   identical sunset takes everyone shoots.
6. **Place names from GPS** — reverse-geocode so beats can say "Nassau" instead of "Day 3".
7. **Photos as well as video**, held as stills with a slow push-in.

## Please don't

- **Don't make the browser do video work.** No ffmpeg.wasm, no uploading originals. The whole design
  rests on heavy files staying local and only 512px stills reaching the API. It's also what makes
  the cost story work.
- **Don't let model output reach ffmpeg unvalidated.** `story._validate()` drops invented shot IDs,
  clamps in/out to real bounds, widens sub-second fragments, and converts to absolute source
  timecode. Models do hallucinate shot IDs — we've seen it. Keep that funnel.

## Style

Plain Python 3.9+, standard library first — the only hard dependencies are `anthropic`,
`scenedetect`, `numpy`, plus optional `librosa` and `faster-whisper`. The app server deliberately
uses `http.server` rather than FastAPI so a teammate can clone and run it without a dependency
fight. Comments in Chinese or English, whichever you think in; user-facing strings in Chinese.
