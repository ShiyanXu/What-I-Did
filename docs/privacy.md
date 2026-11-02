# Privacy

> [简体中文](privacy.zh-CN.md) · **English**

What-IDid records what is on your screen, and that deserves to be treated carefully. This page explains where the program draws the line, how strict the line is, and what it honestly cannot do.

## Three principles

1. **Images never leave the machine.** Screenshots and OCR all happen locally; only redacted text enters a model request.
2. **Minimal by default.** Only the active window is captured; when the window is unavailable the frame is skipped rather than falling back to the full screen.
3. **Refusals are visible.** A broken config or an invalid privacy rule stops processing and reports why, instead of silently falling back to "safe defaults" and pretending it still works.

## Four modes

| Situation | Local record | Images and OCR | Reaches the model |
| --- | --- | --- | --- |
| Paused, locked, blocklisted, config error | Anonymous time ticks only | Not collected | Nothing |
| App and duration only (`skip_ocr_apps`) | App name, anonymous title, duration | Not collected | App and observed duration only |
| Normal capture | Redacted titles and observation data | Collected within the explicit scope, recognized locally | Sent after redaction at the boundary |
| Outbound disabled (`allow_llm=false`) | Same as normal capture | Same as normal capture | Not sent, connection test included |

"Nothing" in the table means a rejected sample contributes no window, image or OCR. **It does not mean pausing automatically forbids generating reports from data recorded earlier.** To rule out outbound calls entirely, turn off `privacy.allow_llm` explicitly.

## Capture boundaries

| Mechanism | Behaviour |
| --- | --- |
| Screenshot scope | `active_window` by default; `all_screens` must be chosen explicitly. The frame is skipped when the window rectangle is unavailable or cropping fails |
| App blocklist `blocked_apps` | Exact process-name match, case-insensitive. Only an anonymous state is kept: no app name, title, position, image or OCR |
| App-only mode `skip_ocr_apps` | WeChat, QQ and similar record only the app and observed duration: no title, no image, no OCR. The same filter applies when reprocessing old data |
| Title blocklist `blocked_title_patterns` | A match anonymizes the title. A bad regex invalidates the config and stops processing; it is never silently ignored |
| Pause / lock screen / idle | Anonymous ticks only. Forced sampling can skip the idle check but **cannot bypass** an explicit pause, the lock screen, the blocklists or a config error |
| Manual OCR diagnostics | Shares the same privacy decisions as capture; the temporary image is deleted at the end. The behaviour of an external OCR program is outside this tool's control |

The window rectangle is not app-level isolation: transparent backgrounds, overlays and sensitive content embedded inside a window still get captured. That kind of content is controlled with the blocklists or by pausing — the program cannot detect it for you.

## The redaction chain

The same rules apply at four points, and missing any one of them counts as a failure:

1. **Before writing to disk** — titles and OCR text, before they land in `events.jsonl` / `ocr.jsonl`;
2. **On history re-read** — when regenerating a report, old data is filtered again against the **current** configuration;
3. **Before summarization** — before compression into the model input;
4. **At the HTTP boundary** — before the request actually goes out, the latest config and outbound switch are checked again, and the current model key is replaced too.

The built-in, non-disableable rules cover: common `sk-` keys, GitHub tokens (including `github_pat_`), national ID numbers, phone numbers and emails. `privacy.redact_patterns` adds your own regexes.

**Regexes are not a sensitive-data detection system**: they do not understand meaning, and they do not blur pixels in a screenshot.

## Limits of local OCR

- Over HTTP, **loopback addresses only**; no system proxy, no following redirects;
- Anything that is not `http(s)`, or that carries credentials or query parameters, is rejected;
- With Umi-OCR, images go to the local Umi process — a local program you chose to trust, outside this tool's control.

## Outbound control

| Switch | Effect |
| --- | --- |
| `privacy.allow_llm = false` | Blocks every model request, including "Test connection" and automatic retries; local recording and timeline reports carry on |
| `report.review_mode = true` | At the scheduled time only the pending text is written to disk; it is sent after you confirm, and confirmation reuses exactly the content you saw |
| `report.open_browser = false` | Only stops the browser opening; it is **not** outbound approval |

Pausing capture does **not** cancel the model analysis of a report already in progress — those are two different things.

## Retention and deletion

| Data | Kept by default | After expiry |
| --- | --- | --- |
| Screenshots and thumbnails | 7 days | Deleted; the report page re-renders automatically and the thumbnail slot stays empty, with no dead links |
| Event stream | 30 days | Deleted with the whole day directory |
| Redacted OCR text | 30 days | Deleted with the whole day directory |
| OCR excerpts in report appendices | Follows the event retention (30 days by default) | The JSON excerpt is emptied and the page re-rendered, regardless of whether the original directory still exists |
| Report narrative, titles, stats, references | Long term | Unaffected by excerpt cleanup; the narrative may still contain OCR-derived information |

Cleanup runs inside the capture process after `clean_hour` (04:00 by default); with capture not running nothing is cleaned up automatically and you need to run `clean` by hand. Cleanup never deletes report narratives, copies you saved elsewhere, your Obsidian archive or any external backup.

**Deleting on purpose**:

```bat
What-IDid.exe forget --minutes 30      :: the last half hour
What-IDid.exe forget --day 2026-10-19  :: a whole day
```

`forget` also cleans up the affected day's report, pages, day status, review preview and shareable export, plus any weekly summary covering that day. Every report in the UI also has a "Forget this day" action.

## Shareable export

The outward version produced by `export` contains no screenshots, thumbnails, raw OCR text, `block_ids`, `seg_id`, internal notes, data-gap details or local paths, and the whole document is run through redaction one last time. It is still human-written text — read it once yourself before sending it.

## What it cannot do

- **It cannot find every piece of sensitive information**: regexes do not understand meaning, screenshot pixels are never blurred, full-screen mode includes secondary displays and background windows, and the blocklists only judge the foreground app.
- **It cannot recall what has already been sent**: data sent to a model provider, or copies you made or synced elsewhere, are beyond the reach of local deletion.
- **It does not rewrite past reports automatically**: historical text only picks up the new policy when it is regenerated; existing excerpts are handled by retention during later cleanup.
- **It is not OS-level isolation**: this is a boundary check at the application level. If the program crashes or a file is locked, temporary images may be left behind (files older than an hour are swept up by later cleanup).
- **Cleanup is not a cross-file transaction**: JSON and HTML are each replaced atomically, so a mid-way failure may leave an old page around temporarily; retry it.

## How to check

```bat
What-IDid.exe doctor              :: read the "config/privacy" and "OCR" verdicts
What-IDid.exe status              :: a config problem shows up as privacy_error in the JSON
What-IDid.exe ocr-test --screen   :: run the real privacy decisions and then recognize; the temporary image is deleted at the end
```

If you find something recorded that should not have been, the most direct fix is `forget --minutes`; to verify "what exactly was recorded", look at `events.jsonl` and `ocr.jsonl` under `data\<date>\` — they are the complete raw record.
