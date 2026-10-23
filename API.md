# Local backend API

Base URL `http://127.0.0.1:8900` (set with `--port`). CORS is open, so a front end hosted anywhere —
Lovable, Vercel, a local Vite dev server — can drive it. Use **Chrome**: Safari blocks HTTPS pages
calling `http://127.0.0.1`.

Every response is JSON. Errors are `{"error": "..."}` with a non-200 status. Real response samples
for every GET are in [`sample-data/`](./sample-data).

## Reads

| Endpoint | Returns |
|---|---|
| `GET /api/env` | `{has_key, home, cwd}` — whether `ANTHROPIC_API_KEY` is set, plus the user's home dir |
| `GET /api/ls?path=` | folder browser: `{path, parent, dirs:[{name, path, videos}], videos_here}` |
| `GET /api/projects` | `{projects:[summary]}` |
| `GET /api/projects/{id}` | one project summary (counts, duration, what's been generated) |
| `GET /api/projects/{id}/shots` | `{shots:[…]}` — every shot with thumbnail URL and AI log |
| `GET /api/projects/{id}/outline` | the beat structure |
| `GET /api/projects/{id}/story` | the timeline, with thumbnails and running timecodes |
| `GET /api/projects/{id}/versions` | `{versions:[{n, note, created, title, seconds, shots}]}` — newest first |
| `GET /api/job?since=N` | current job; `since` = how many log lines you've already shown |
| `GET /media/{id}/…` | thumbnails and `preview.mp4`; supports HTTP Range, so video scrubbing works |

The browser can't read real filesystem paths, which is why `/api/ls` exists — the folder picker is
server-side by necessity.

## Writes

All of these start a background job and return `{"started": "<action>"}` immediately. Poll
`/api/job` for progress. Only one job runs at a time; a second one returns **409**.

| Endpoint | Body |
|---|---|
| `POST /api/projects` | `{footage, name}` — creates the project and scans the folder |
| `POST /api/projects/{id}/analyze` | `{mock, whisper, workers}` — shots, keyframes, quality, AI logs |
| `POST /api/projects/{id}/outline` | `{idea, minutes, mock}` — propose beats from the user's idea |
| `POST /api/projects/{id}/story` | `{beats, minutes, mock}` — cut a timeline to the approved beats |
| `POST /api/projects/{id}/revise` | `{instruction, minutes, mock}` — "stronger opening, fewer landscapes" |
| `POST /api/projects/{id}/render` | `{music, vertical, hd}` — render preview.mp4 and export XML |
| `POST /api/projects/{id}/export` | `{}` — export timeline.xml only |
| `POST /api/projects/{id}/recheck` | `{}` — after hand-editing story.json: re-validate, rebuild storyboard + XML |

One exception, synchronous because the UI needs the result immediately:

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/projects/{id}/timeline` | `{timeline:[{shot_id, in, out}]}` | the re-validated timeline |

It replaces the timeline with exactly the segments you send, in that order — that's how the UI
persists deletes, reorders and trims. Dropped segments are simply absent from the array.
Send each segment's `i` (its index in the last `GET …/story`) so a trimmed `in` still finds its
original; optional `text`, `keep_audio`, `chapter` are applied too. Every save becomes a new version.

Other synchronous calls (return their result immediately):

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/projects/{id}/restore` | `{n}` | restores version n as a new version; returns the story |
| `POST /api/projects/{id}/flag` | `{shot, flag: "must"\|"exclude", on}` | `{flags:{must:[], exclude:[]}}` — excluded shots are hidden from every AI story step |
| `POST /api/projects/{id}/settings` | any of `{brief, minutes, music, vertical, lang}` | `{settings}` |
| `POST /api/projects/{id}/open_folder` | `{}` | opens the project folder in Explorer / Finder |

`GET /api/projects/{id}/shots` also returns `frames` (all keyframe URLs), `created`, `gps`,
`has_audio`, `vertical`, `tags`, `activity`, `best_moment`, `must`, `excluded`.
`GET /api/projects/{id}` also returns `path`, `has_storyboard`, `mtimes`, `versions`, `flags`.
`GET /api/env` also returns `drives` (Windows drive letters, for the folder picker).

`mock: true` runs the whole flow with deterministic stand-ins instead of API calls. Use it for UI
work; it costs nothing and needs no key.

## Job objects

```json
{"name":"看素材","project":"drone-apr","status":"running",
 "lines":["[vlogai] …"],"line_count":42,
 "done":120,"total":314,"pct":38,"error":"","started":"2026-10-17T19:02:46"}
```

`status` is `running | done | error | idle`. `lines` contains only the lines after `since`, so a
poller appends rather than redraws. `pct` is null when the step can't report progress — show an
indeterminate bar. On `error`, `error` holds the summary and the tail of the traceback is in `lines`.

Poll every ~900ms. Jobs survive page reloads: on load, `GET /api/job` and resume if something is
running.

## Data shapes

See [`sample-data/`](./sample-data) for real payloads, and the "Data structures" section of
[`LOVABLE_HANDOFF.md`](./LOVABLE_HANDOFF.md) for annotated examples.

The one field worth calling out: in `story.timeline`, `in`/`out` are seconds **relative to the start
of that shot**, `at` is the position in the finished cut, and `shot_dur` is the shot's full length —
the ceiling when a user stretches a segment.
