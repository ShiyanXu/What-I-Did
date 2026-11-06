# Architecture

> [简体中文](architecture.zh-CN.md) · **English**

This page explains how What-IDid is put together, how data moves between modules, and the handful of design decisions that are hard to change later. If you only want to use it, the [README](../README.md) and the [usage guide](usage.md) are enough.

## Design principles

| Principle | How it is done | What it costs |
| --- | --- | --- |
| Images never leave the machine | Screenshots and OCR run entirely locally; only redacted text enters a model request | OCR quality is bounded by the local engine; a new machine means reinstalling the model |
| The model produces content, not layout | The model returns fixed-schema JSON; layout is decided by local Jinja2 templates | Template changes are independent of model capability, but you maintain them yourself |
| Time is decided locally | Activity block and behavior segment boundaries are all computed locally; the model can only attach labels | The model cannot "correct" a time it thinks is wrong |
| One source of truth | "Should a report run" is implemented once, in `tick.pending()`; the UI, the scheduled task and diagnostics all call it | Any new trigger must reuse it, never reimplement it |
| Additive compatibility | The report `schema_version` stays at 1 and fields are only added; missing fields in old reports are treated as empty | Old data is never recomputed automatically, so a corrected definition does not rewrite history |
| No extra dependencies | The runtime needs only the standard library + Pillow/numpy/requests/Jinja2/PyYAML/psutil; the tray is hand-written with ctypes | Some features have to be built by hand (tray, dedupe, scheduling) |

## Runtime shape

The whole program is one exe; command-line arguments split it into three roles:

| Role | How it starts | Characteristics |
| --- | --- | --- |
| GUI | Double-click, or `What-IDid.exe gui` | No console window; closing the window only hides it to the tray |
| CLI | `What-IDid.exe <subcommand>` | Linked without a console; at runtime `AttachConsole` reattaches to the caller's console |
| Resident capture | The program starts `What-IDid.exe daemon` itself | No window, single instance, with a watchdog |

A few constraints:

- **Single instance**: the capture process uses a named mutex to guarantee there is only one; on a crash the OS releases it, so there is no "zombie lock". The UI process does not hold this lock, so it can be opened repeatedly — double-clicking again brings back the window already in the tray.
- **The UI does not capture**: it only reads and writes state and issues commands; killing the process does not stop recording. To stop capture you must explicitly `stop` or use quit in the tray menu.
- **The tray gets its own thread**: the tray window runs its message loop in a thread of its own. Putting it on the Tk main thread crashes in `PyEval_RestoreThread`, because tkinter releases the GIL inside the event loop. That one has been hit for real.

At most two scheduled tasks are registered:

| Task | Trigger | Purpose |
| --- | --- | --- |
| `WhatIDid-Recorder` | At login | Resident capture; no execution time limit, restarted every minute if killed |
| `WhatIDid-Report` | Every 20 minutes from 21:00 for 2 hours; 3 minutes after login; 04:05 the next day | Runs `tick`, idempotent; **does not depend on the capture process** |

Both tasks are registered as "run only when the user is logged on". Switching to "run whether or not the user is logged on" lands the process in Session 0, which has no desktop — every screenshot comes out black.

## Data flow

| Step | Input | Output | Main module |
| --- | --- | --- | --- |
| Sample | Timer (20±3 s) | One event + optional screenshot | `capture.py` |
| Dedupe | Screenshot fingerprint | Keep / discard | `vision.py` |
| Recognize | Active window screenshot at native resolution | Redacted text | `ocr.py` |
| Block | Event stream | Activity block `b-YYYYMMDD-HHMM-xxxx` | `blocks.py` |
| Enrich | Activity blocks + local browser history | Each page block gets `urls[]` | `browse.py` |
| Segment | Activity blocks | Behavior segment `seg-NN` | `behavior.py` |
| Reference | Titles and OCR text | `ref_id` and clickable links | `refs/` |
| Compress | Blocks, segments, stats, references, manual entries | Payload constrained by a character budget | `digest.py` |
| Infer | Payload + prompt | Strict JSON | `llm.py` |
| Validate | Model JSON | Structured report, or a list of issues | `validate.py` |
| Persist | Report data | `reports/<date>.json/.md`, `web/` | `render.py`, `report.py` |

Four of these are worth expanding.

**Sampling and dedupe.** Every sample reads the foreground window's handle, title, process name and rectangle, plus idle seconds and lock-screen state. By default only the active window is captured, and dHash Hamming distance together with the mean difference over a 12×8 grid decides whether anything "changed". Changed frames go to disk; when the picture stays static for a long time, keyframes are kept every `keyframe_sec` so the timeline never has a hole. If the window rectangle is unavailable or cropping fails, **the frame is skipped; it does not fall back to the full screen** — better to record less than to record it wrong.

**Blocks and segments.** Adjacent events further apart than `gap_threshold_min` break the block; a single block is capped at `max_block_min` (30 minutes by default). Intervals inside a block covered by pause, lock screen or the blocklists are subtracted from `active_seconds` and recorded separately as `idle_minutes`. Behavior segments are split further whenever the primary category changes, capped at `max_session_min`, and carry `start_iso` / `end_iso` so a segment crossing midnight does not end up misplaced.

A block's ID is `b-YYYYMMDD-HHMM-<hash4(app+title)>`, decided only by the day, the block's starting minute and the app/title. **Re-runs, backfills and re-blocking never change it**, so it works as an HTML anchor and can be back-referenced from external documents.

**Source links.** `browse.py` runs after blocking and before any compression. It copies the browser's `History` file into `state/tmp`, opens it read-only, and pulls the visits of that day; the copy is deleted afterwards. Matching has two tiers, because a single `visits` row only records a navigation: reading one article for half an hour leaves a single row at the start. Visits inside the block (plus `ahead_sec`) may contribute several links, keeping only those that score close to the best one; if the block has none, only the most recent visit *before* it is used within `lookback_sec`. Scoring is `0.6 × title similarity + 0.4 × closeness`, so a title that matches trumps a visit that merely happens to be near. When history yields nothing, the address-bar strip is tried (only for captures that stored line positions), then the host alone, tagged `source: domain`. Everything is written to `data/<day>/urls.jsonl` with one `os.replace`, so a failed run never leaves a half-written file.

**Compression and validation.** `digest.py` squeezes a day into `llm.input_char_budget` (24,000 characters by default): stats, block list, behavior segments and references each get an allowance, trimmed step by step, and finally hard-truncated with a note of what was cut. After the model answers, `validate.py` checks that every `block_id` / `ref_id` / `seg_id` actually exists; missing or invented references go into `issues`. If the JSON structure is broken, the error is fed back and retried once; if that fails too, it degrades.

## How time is defined

- **A workday is not a calendar day**: a day starts at `app.day_cutoff_hour` (04:00 by default) and ends at 04:00 the next morning. Work done at 2 a.m. counts towards the previous day, so a late night does not split a day in two. Every "today" and "yesterday" goes through the same time utilities.
- **Observed duration ≠ working hours**: `active_seconds` is the wall-clock span minus pauses and lock screen. It is still only a measure of "there was screen activity"; actions without input such as reading or watching a lecture are underestimated.
- **Behavior segments use wall-clock time**: "how long before the flow was interrupted" is measured against the wall clock, idle time included.
- **Rhythm metrics are computed from behavior segments**: activity blocks are cut at the 30-minute cap, so computing "longest focus" from them could never exceed 30 minutes.

## Report scheduling and state

Report state is split into four independent dimensions, stored in `state/day/<date>.json`:

| Field | Meaning |
| --- | --- |
| `coverage_until_iso` | The moment this report's data covers up to |
| `status` | `ok` / `degraded` / `empty` — whether the model actually succeeded |
| `input_hash` | Fingerprint of the event and OCR streams; late-arriving data changes it |
| `revision` | Which version this is |

The reason for splitting them: with a single "a report exists" boolean, a degraded report that covers the day completely but failed at the model stage would never be upgraded. Now the four dimensions are judged separately, and a degraded report is retried and upgraded to the AI version automatically while it is within `report.degraded_retry_hours` and has not exceeded `report.max_upgrade_attempts`.

The trigger chain:

```
WhatIDid-Report task → tick.run() → tick.pending(cfg, now, days) → report.generate() per day
UI hint / doctor                   ─┘        (the only "should a report run" decision)
Capture watchdog                   ─┘
```

`tick.pending()` handles all of these cases: skip when coverage already reaches the end of the window; allow an incremental update only when there is new coverage and more than `refresh_gap_min` has passed since the last generation; return "awaiting review" instead of "missed" when fresh content is waiting for confirmation; generate nothing for a day with no data at all.

## Module responsibilities

| Layer | Module | Responsibility |
| --- | --- | --- |
| Capture | `capture.py` | Sampling, privacy decisions, screenshots, dedupe, persistence, handing off to OCR |
| | `winapi.py` | DPI awareness, foreground window, process name, idle, lock screen, monitor geometry |
| | `vision.py` | dHash, grid fingerprint, classification, downscaling, atomic writes |
| | `ocr.py` | Engine selection and fallback, queue, quota, watchdog, recovery probing |
| Processing | `blocks.py` | Events → activity blocks, stable IDs, statistics |
| | `browse.py` | Reads local Chrome/Edge history at report time and matches visits to page blocks (history → address bar → domain only) |
| | `behavior.py` | Activity blocks → behavior segments; the verb vocabulary is the only source |
| | `digest.py` | Compression into the budget; prompts |
| | `refs/` | Reference extraction, resolution, dedupe, caching |
| Reporting | `llm.py` | OpenAI-compatible client, call budget, outbound redaction |
| | `validate.py` | Structural validation and the issue list |
| | `report.py` | Orchestration, state, review, degradation and upgrade |
| | `tick.py` | Scheduled-task entry point and the single decision |
| Presentation | `render.py` | Markdown and static dashboard rendering |
| | `templates/` | Jinja2 templates |
| | `gui.py` / `tray.py` | GUI and tray |
| Data | `paths.py` | The single source of paths; joining paths anywhere else is forbidden |
| | `config.py` | Config loading, validation, atomic saving, handling of invalid config |
| | `privacy.py` | Capture permission, anonymous events, redaction, loopback address checks |
| | `retention.py` | Rolling cleanup |
| | `forget.py` | Forget |
| | `manual.py` | Manual entries and relabeling |
| | `search.py` / `weekly.py` / `export_safe.py` | Search, weekly summary, shareable export |
| Infrastructure | `daemon.py` | Resident capture, single instance, watchdog, daily cleanup |
| | `control.py` / `status.py` / `doctor.py` | Start/stop, status snapshot, self-check (shared by CLI and UI) |
| | `autostart.py` | Scheduled tasks and the registry fallback |
| | `locking.py` / `logging_setup.py` / `timeutil.py` | Mutex, logging, time |

## Data model

**Event** (`data/<date>/events.jsonl`, one per line):

```json
{"v": 1, "ts": "2026-10-19T09:12:00+08:00", "day": "2026-10-19",
 "app": "code.exe", "title": "report.py - What-IDid", "pid": 1001,
 "rect": [-8, -8, 1928, 1048], "idle": 12.0, "state": "active",
 "seq": 1, "cls": "tick", "diff": 0.01, "hash": "f2a74de452e6b438", "kind": "tick"}
```

`state` is one of `active` / `idle` / `paused` / `locked`; in the paused and blocked states only an anonymous tick is written, without app name or title.

**Activity block** (`blocks[]` in `reports/<date>.json`):

```json
{"id": "b-20261019-0912-0d56",
 "start": "2026-10-19T09:12:00+08:00", "end": "2026-10-19T09:41:40+08:00",
 "app": "code.exe", "title": "report.py - What-IDid", "category": "dev",
 "minutes": 30, "content_policy": "allowed", "shots": [], "text": "", "switch_count": 0}
```

`content_policy` has three levels: `allowed` (full), `metadata_only` (app and duration only), `none` (anonymous). It is worked out when the block is created and recomputed against the **current** privacy configuration on every re-generation.

**Report** (`reports/<date>.json`) top level: `schema_version`, `day`, `weekday`, `generated_at`, `revision`, `status`, `reason`, `coverage`, `report`, `stats`, `gaps`, `blocks`, `refs`, `manual`, `meta`.

`report` holds the structured content the model produced (`headline`, `opening_md`, `summary_md`, `behaviors`, `highlights`, `followups`, `friction`, `insight`, `timeline`, `accomplishments` and so on); `meta` records the model, call count, tokens, payload size and validation issues.

The reading order is derived by the render layer, never hard-coded in the model output. `render.reading_view()` is the only derivation entry point:

- Each `highlights` entry carries a `status` (`done|advanced|waiting|blocked|unresolved`) and a `next`, and renders as a first-screen outcome card; for older reports missing both fields the status is inferred from the text, and the deciding function exists only as `behavior.outcome_status()`.
- `followups` are the things that need action; for older reports they are synthesized from `friction` (unresolved) and `tomorrow`.
- The main timeline comes from `behavior.group_tasks()` merging `behaviors` into 4–6 segments (same verb, gap ≤25 minutes, segment ≤150 minutes), with lock-screen and away segments excluded.
- Activity blocks, the original strips, all documents, OCR, data gaps and generation info all go into a collapsed appendix at the bottom of the page; the body keeps only documents attached to outcome cards and at most 3 screenshots.

**Manual entries** (`data/<date>/manual.json`):

```json
{"offline": [{"start": "10:00", "end": "11:00", "label": "线下客户沟通", "note": ""}],
 "behavior_overrides": [{"seg_id": "seg-03", "verb": "meeting", "label": "复盘会"}],
 "notes": "今天在等接口"}
```

These are kept completely separate from automatic output: re-runs and cleanup never touch them, they are re-applied on every generation, and **user revisions always win**.

## Storage layout

```
<data dir>\
  config.yaml              config source
  config\api_key.txt       model key (or the WID_LLM_API_KEY environment variable)
  config\bindings.yaml     pin a project to a document by hand
  data\<date>\
    events.jsonl           event stream
    ocr.jsonl              redacted recognition results
    urls.jsonl             source links matched for this day (only when browser history is on)
    manual.json            manual entries and relabeling
    shots\ thumbs\         changed frames and thumbnails
  reports\<date>.md/.json  daily reports
  reports\share\           shareable exports
  reports\weekly\          weekly summaries
  web\                     static dashboard (index.html and day\*.html)
  state\                   heartbeat, day state, review previews, locks, pause marker
  logs\                    capture and report logs (5 MB rolling)
```

The data directory is resolved in this order: `WID_HOME` → the program directory (used when writable, portable) → `%LOCALAPPDATA%\What-IDid`. Program resources (templates, default config, built-in models) come from the read-only install directory; all runtime data goes to the directory above. The two are never mixed.

## Extension points

| If you want to | Change |
| --- | --- |
| Add a document source | Write a `BaseProvider` subclass in `src/refs/providers.py`, implement `probe()` / `resolve()`, and add the name to `refs.providers`. The data schema, pages and report logic stay untouched |
| Hook up a knowledge base | Implement `KBProvider.resolve()` to return `kb://<collection>/<doc_id>#<anchor>`, then fill in `refs.kb.url_template` |
| Bind a document by hand | Write a `title_contains` rule in `config/bindings.yaml`; it takes priority over automatic resolution |
| Add a capture method | The sampling entry point in `capture.py` is the only one; shape the new source into the same event structure and downstream modules will not care where it came from |
| Change the layout | Only touch `src/templates/`. Avoid newlines made invisible by `trim_blocks`; leave blank-line layout to post-processing after rendering |
| Add a subcommand | Register it in `wid.py` and add the new module name to `MODULES` in `build.spec` — that list is hand-written, and static analysis cannot see lazy imports |
