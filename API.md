# Local backend API

Base URL `http://127.0.0.1:8765` (the port is set on the Settings page, or with `--port`). The
server only listens on 127.0.0.1. Use **Chrome**: Safari blocks HTTPS pages calling `http://127.0.0.1`.

Every response is JSON. Errors are `{"error": "..."}` with a non-200 status. Real response samples
for every GET are in [`sample-data/`](./sample-data).

## Who may call it

The backend can read your footage, spend your API credit and change your settings, so it no longer
answers every web page (it used to send `Access-Control-Allow-Origin: *`):

- **Origins.** Requests without an `Origin` header (the built-in UI, curl) are allowed. Browser
  requests are allowed from `http(s)://localhost`, `127.0.0.1` and `[::1]` on any port, from
  `*.lovable.app`, `*.lovableproject.com` and `lovable.dev`, plus anything listed in the
  `allowed_origins` setting (Settings → Advanced → “Allowed web pages”, comma-separated, exact
  match). Anything else gets **403** `origin not allowed: …`. The CORS headers echo the allowed origin.
- **Host.** The `Host` header must be `127.0.0.1`, `localhost` or `[::1]` (blocks DNS rebinding).
- **JSON only.** Every `POST` with a body must send `Content-Type: application/json`, otherwise
  **400**. (A plain HTML form or a `text/plain` fetch from another site can no longer trigger actions.)
- **Paths.** Project ids are sanitised; `/media/{id}/…` only serves files inside that project folder.

## Reads

| Endpoint | Returns |
|---|---|
| `GET /api/env` | `{has_key, key, version, home, cwd, drives}` — whether a key is set (with a masked hint), the app version, the user's home dir |
| `GET /api/settings` | app settings, defaults, `rules` (allowed range `[min, max]` or list of choices per key), the masked key and where it comes from |
| `GET /api/logs?level=&q=&limit=` | `{entries, counts, file, level}` — the run log, newest last |
| `GET /api/logs/summary` | `{counts}` — warning / error counts for the header badge |
| `GET /api/ls?path=` | folder browser: `{path, parent, dirs:[{name, path, videos}], videos_here}` |
| `GET /api/projects` | `{projects:[summary]}` |
| `GET /api/projects/{id}` | one project summary (counts, duration, what's been generated) |
| `GET /api/projects/{id}/shots` | `{shots:[…]}` — every shot with thumbnail URL and AI log |
| `GET /api/projects/{id}/outline` | the beat structure |
| `GET /api/projects/{id}/shootplan` | the shot plan (`{}` if none): `{title, logline, beats, days:[{day, where, golden_hours, shots:[{shot_id, beat, what, framing, hold, why, must_have, sketch, sketch_svg, how, refs, ref_urls}]}], must_haves, reminders, done:[shot_id], has_html, vertical}` — `sketch` is the composition `{subjects:[{k, x, y, s}], horizon, move, angle}`, `sketch_svg` the drawn sketch (uses `currentColor`), `how` the camera move / angle in words, `ref_urls` the user's reference images |
| `GET /api/projects/{id}/story` | the timeline, with thumbnails and running timecodes |
| `GET /api/projects/{id}/versions` | `{versions:[{n, note, created, title, seconds, shots}]}` — newest first |
| `GET /api/job?since=N` | current job; `since` = how many log lines you've already shown |
| `GET /media/{id}/…` | thumbnails, `preview.mp4`, `shootplan.html`; supports HTTP Range (incl. suffix ranges), so video scrubbing works |

The browser can't read real filesystem paths, which is why `/api/ls` exists — the folder picker is
server-side by necessity.

## Writes

All of these start a background job and return `{"started": "<action>"}` immediately. Poll
`/api/job` for progress. Only one job runs at a time; a second one returns **409**.

| Endpoint | Body |
|---|---|
| `POST /api/projects` | `{footage, name}` — creates the project and scans the folder (also used to re-scan / change the folder of an existing project) |
| `POST /api/projects/{id}/plan` | `{brief, days, minutes, gear, mock}` — write a shot plan before the trip, with a composition sketch per shot (also writes `shootplan.html`, a phone checklist); clears the checkmarks and reference images |
| `POST /api/projects/{id}/analyze` | `{mock, whisper, workers, subject}` — shots, keyframes, quality, AI logs. `subject` (who the film is about) is saved to the project and used to judge faces / people together |
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

`POST /api/projects` **without** `footage` is synchronous: it creates an empty project for planning
and returns `{"id", "started": null}` (400 if the name is empty or already taken).

Other synchronous calls (return their result immediately):

| Endpoint | Body | Returns |
|---|---|---|
| `POST /api/projects/{id}/restore` | `{n}` | restores version n as a new version; returns the story |
| `POST /api/projects/{id}/flag` | `{shot, flag: "must"\|"exclude"\|"unprotect", on}` | `{flags:{must:[], exclude:[], unprotect:[]}}` — the three are mutually exclusive. Excluded shots are hidden from every AI story step; `must` shots are always kept; `unprotect` turns off automatic key-shot protection for that shot |
| `POST /api/projects/{id}/settings` | any of `{brief, minutes, music, vertical, lang, subject, plan_brief, plan_days, plan_gear}` | `{settings}` |
| `POST /api/projects/{id}/plan_check` | `{shot_id, done}` | `{done:[shot_id]}` — tick a planned shot off |
| `POST /api/projects/{id}/plan_save` | `{days, title?, logline?}` | the shot plan, as `GET …/shootplan` — saves hand edits (shots with empty `what` are dropped) and rewrites `shootplan.html` |
| `POST /api/projects/{id}/plan_ref` | `{shot_id, data: "data:image/jpeg;base64,…"}` | the shot plan — attaches a reference image (JPG / PNG, ≤ 4 MB, ≤ 3 per shot; the UI shrinks it to 1600 px first). Stored in `plan_refs/`, embedded in `shootplan.html` |
| `POST /api/projects/{id}/plan_ref_del` | `{shot_id, name}` | the shot plan — removes that reference image and its file |
| `POST /api/projects/{id}/open_folder` | `{}` | opens the project folder in Explorer / Finder |
| `POST /api/settings` | `{settings?, api_key?, reset?}` | saves app settings (validated) and/or writes the key to `.env` |
| `POST /api/settings/test` | `{api_key?}` | `{ok, reason}` — checks a key without spending tokens |

`GET /api/projects/{id}/shots` also returns `frames` (all keyframe URLs), `created`, `gps`,
`has_audio`, `vertical`, `tags`, `activity`, `best_moment`, `must`, `excluded`, `unprotected`, and
the key-shot protection fields: `protected` (the reason, e.g. `主人公正脸`, `关键人物同框`,
`重要风景/地标`, `AI 评为极精彩`, `适合作结尾`, `你标为必用`; empty if not protected) and the AI
signals it is based on — `faces_clear`, `subject_present`, `people_together`, `scenery_value` (0–10).
Protected shots are put in the AI prompt as must-include, and any the AI leaves out are added back
to the timeline with `role: "protected"`. At most 65% of the target length is protected.
`GET /api/projects/{id}` also returns `path`, `has_storyboard`, `has_plan`, `mtimes`, `versions`, `flags`.
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
