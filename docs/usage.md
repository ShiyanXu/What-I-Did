# Usage guide

> [简体中文](usage.zh-CN.md) · **English**

Installation and first-time setup are covered by the [README quick start](../README.md#quick-start). This page is about day-to-day use: the UI, the commands, how reports get generated, and where the limits are on manual entries, review, forget and search.

## Interface

Double-click `What-IDid.exe` to open it. Four pages:

### Overview

- Four figures along the top: capture status, today's effective activity, today's record count, free disk space
- Timeline: today's behavior strips; hover for details. Once the report has run, the model's reading of each segment replaces them
- Capture controls: start / stop / pause 30 minutes / resume / generate the report now / open the full dashboard
- Activity over the last 14 days: click a bar to jump to that day's report
- A banner at the top says what to do next: capture is not running, start at login is not registered, yesterday's report has not been generated, content is waiting for your confirmation

### Reports

Dates on the left, report body on the right. Actions in the top right:

| Action | What it does |
| --- | --- |
| Regenerate | Re-run this day with the current data and configuration |
| Full dashboard | Open this day's HTML page in the browser |
| Copy Markdown | Copy the body to the clipboard, ready to paste into Obsidian |
| Add entry | Add offline activity the screen cannot see |
| Forget this day | Delete raw and derived data after a second confirmation |

With pre-send review enabled, a "pending review" badge shows up here together with "Review and send / Discard".

### Settings

Model endpoint and key, outbound switch, capture scope, sampling interval, OCR engine, report hour and pre-send review, retention, privacy lists, start at login.

Saving rewrites only the matching line in `config.yaml`; comments, alignment and block lists are left exactly as they were — it is not a full `yaml.dump` rewrite. Saving restarts capture so the new settings apply, but **changing the report hour still requires re-registering the scheduled task**.

### Diagnostics

One-click self-check, live OCR test, cleanup preview, open logs and data directory. The self-check verdict matches the `doctor` command exactly, because both call the same set of checks.

## Tray

| Action | Effect |
| --- | --- |
| Left click | Bring the main window back |
| Right click | Open main window / start · pause capture / generate today's report now / diagnostics and cleanup / quit (stop capture) |
| Click the window's × | Hides the UI, **capture keeps running**, the icon stays in the tray |

Windows 10/11 files new icons into the "^" overflow area by default, so the first time you have to click once to expand it; drag it onto the resident area and it stays out there. No API can force this.

## Command line

Use the exe as a command (inside the folder, hold Shift and right-click → "Open PowerShell here"). It is the same program: add a subcommand and it runs as a CLI.

### Everyday

| Command | Purpose |
| --- | --- |
| `What-IDid.exe` | Open the graphical interface (same as double-clicking) |
| `What-IDid.exe setup` | First-run wizard: generate the config and list what is left to do |
| `What-IDid.exe doctor` | Environment self-check; `--full` also runs a real OCR pass; `--json` emits machine-readable output |
| `What-IDid.exe status` | Heartbeat, today's event count, screenshot count, disk, latest report, report status |
| `What-IDid.exe start` / `stop` | Start / stop background capture |
| `What-IDid.exe pause --minutes 60` / `resume` | Temporarily pause / resume (meetings, screen sharing) |
| `What-IDid.exe open [--date 2026-10-19]` | Open the dashboard |

### Reports

| Command | Purpose |
| --- | --- |
| `What-IDid.exe report --date 2026-10-19` | Generate a given day's report |
| `... report --force` | Ignore "already up to date" and re-run |
| `... report --no-open` | Do not open the browser afterwards |
| `What-IDid.exe tick` | Scheduled-task entry point: work out which days are due and generate them one by one |

In review mode `report` exits with code 0: the content is ready, it is just waiting for your confirmation.

### Review

| Command | Purpose |
| --- | --- |
| `What-IDid.exe review show --date 2026-10-19 --chars 3000` | Print the exact text that would be sent to the model |
| `What-IDid.exe review approve --date 2026-10-19` | Confirm and actually send it |
| `What-IDid.exe review discard --date 2026-10-19` | Discard: nothing is sent, the existing report is untouched |

### Manual revisions

| Command | Purpose |
| --- | --- |
| `manual add --date 2026-10-19 --start 10:00 --end 11:00 --label "offline meeting"` | Add offline activity |
| `manual override --date 2026-10-19 --seg seg-03 --verb meeting --label "retro"` | Relabel one behavior segment |
| `manual notes --date 2026-10-19 --text "waiting on the API today"` | Add a note for the day |
| `manual show --date 2026-10-19` | Show the current revisions |

`--verb` accepts: `coding` `debugging` `testing` `designing` `reading` `writing` `meeting` `communicating` `operating` `admin`.

### Search and summaries

| Command | Purpose |
| --- | --- |
| `search 日报 校验 --days 30` | Cross-day search over titles, screen text and reports; multiple terms must all match |
| `... --kind ocr` | Search one kind only (`title` / `ocr` / `report`, combinable) |
| `... --limit 200 --json` | Cap the total, emit machine-readable output |
| `weekly` | This week's summary |
| `weekly --anchor 2026-10-19` | The calendar week (Monday to Sunday) containing the given date |
| `weekly --start 2026-10-01 --end 2026-10-31` | Custom range, 62 days maximum |
| `export --date 2026-10-19` | Export a report you can share |
| `... export --no-evidence` | Strip the evidence fields as well |

### Maintenance

| Command | Purpose |
| --- | --- |
| `What-IDid.exe clean --dry-run` | Preview what would be deleted; drop `--dry-run` to actually delete |
| `What-IDid.exe forget --minutes 30` | Forget the last 30 minutes (irreversible) |
| `What-IDid.exe forget --day 2026-10-19` | Forget a whole day |
| `... forget --yes` | Skip the interactive confirmation, for scripts |
| `What-IDid.exe ocr-test --screen` | Run OCR under the current scope and privacy rules; pause, lock screen and blocklists cannot be bypassed; the temporary image is deleted at the end |
| `What-IDid.exe autostart --status` / `--install` / `--remove` | Inspect / register / remove start at login |
| `What-IDid.exe init` | Create only the directories and config placeholders |
| `What-IDid.exe collect --once` / `--duration 60` | Foreground capture, for debugging |
| `What-IDid.exe daemon` | Resident capture process (normally started by `start` or the scheduled task) |

> A side effect of console-less programs in cmd: they "return the prompt first, then print". The output is not lost, it just may interleave with the next command. To pipe it, use `What-IDid.exe status | more`, or `start /wait What-IDid.exe doctor`.

## How reports get generated

A day runs from 04:00 to 04:00 the next morning (`app.day_cutoff_hour`). By default it tries at 21:00, and then:

| When | What happens |
| --- | --- |
| Every 20 minutes from 21:00 | Re-check, for 2 hours; skip when there is no new coverage |
| New coverage reaches 90 minutes | An incremental update is allowed |
| 3 minutes after login | Catch-up run (the machine was off last night, so yesterday's report is produced today) |
| 04:05 the next day | Final version, folding in work done after 21:00 |

Automatic generation opens the browser dashboard only after the **final version** (`report.open_browser` controls this): neither the 21:00 first version nor the evening incremental updates open anything, and a catch-up run covering several days opens only the most recent one — no need to have several tabs pop up in one evening. The manual `report` command opens the browser on every run; `--no-open` turns that off.

Report status is recorded along four separate dimensions: **how far it covers**, **whether the model succeeded**, **whether the input changed**, **which revision it is**. So "a report exists" is not the same as "the AI analysis succeeded": a degraded report is retried and upgraded automatically while it is within `report.degraded_retry_hours` (default 12 hours) and under `report.max_upgrade_attempts` (default 3).

A day with no data at all produces no empty report, so a fresh install does not fill the page with blank days.

### Pre-send review

With `report.review_mode=true`, the scheduled time no longer calls the model directly:

1. the "text about to be sent to the model" is written to `state/day/<date>-review.json`;
2. it stops in a pending state, and `report` returns `awaiting_review`;
3. the request only goes out after you confirm; confirmation reuses the content from the preview (input unchanged, under 1 hour, same endpoint and model) rather than quietly rebuilding it.

Three time windows, one job each:

| Window | Value | Effect |
| --- | --- | --- |
| Preview reuse | 30 minutes | Repeated triggers on the same input do not rebuild the preview |
| Confirmation reuse | 1 hour | Confirming sends what the preview held; past that, it rebuilds from the current input |
| Pending validity | 12 hours | After that the review is considered abandoned and can be prepared again |

Pending content is never counted as a "missed report" and re-triggered.

## Manual entries and relabeling

The screen cannot tell that you were in a meeting, drawing on a whiteboard or on a call. Manual entries tell the program about those stretches:

```bat
What-IDid.exe manual add --start 10:00 --end 11:00 --label "offline customer call" --note "conference call"
```

- Times accept only `HH:MM` within the current day; an out-of-range hour or minute is an error;
- Re-adding the same label over the same stretch only updates the note, it does not stack a second entry;
- Manual entries also feed the model input, so the model knows "this stretch was a meeting, not slacking off";
- They are kept completely separate from automatic output: re-runs and cleanup never touch them, and they are re-applied on every generation — **your revisions always win**;
- The template has a dedicated "manual entries" section; they are not mixed into the body.

## Forget

| Command | What gets deleted |
| --- | --- |
| `forget --minutes N` | Events, screenshots, thumbnails and OCR for the last N minutes; the affected day's report, pages, day status, review preview and shareable export, plus any weekly summary covering that day |
| `forget --day D` | The above plus the whole day's data directory (manual entries included) |

Destructive operations confirm interactively by default; **only an explicit `y` counts as consent**, Enter means cancel. Scripts need `--yes`. Every report in the UI also has a "Forget this day" action.

What gets deleted is the data this tool manages. Copies **cannot be recalled** once they have been sent to a model provider, or saved and synced elsewhere by you. Redaction and pre-send review are the first line of defence.

## Cross-day search

```bat
What-IDid.exe search 幂等 校验 --days 30
```

It scans only local files: `data/<date>/events.jsonl` (window titles and apps), `ocr.jsonl` (redacted text) and `reports/<date>.json` (report narrative). Multiple keywords are combined with **AND** and matching is case-insensitive; a day is capped at 30 hits, the total is controlled by `--limit`, and the result says whether it was truncated. With no hits the exit code is 1 and it explains the likely cause (range too small, keywords too strict).

An empty keyword, `--days` out of range or an unknown `--kind` are all explicit errors — never a silent empty result.

## Weekly summary

```bat
What-IDid.exe weekly --anchor 2026-10-19
```

It aggregates **already generated** reports only and never calls the model again — every sentence in a weekly summary can be traced back to some daily report. Missing days are marked "missing" and listed separately (that is not "no work"); corrupt ones are marked "corrupt"; degraded ones are marked "timeline" with a note that confidence is low. Items are deduplicated by their first 40 characters, up to 40 per category. Output goes to `reports/weekly/<start>_<end>.md`.

## Shareable export

```bat
What-IDid.exe export --date 2026-10-19
```

Use this one when showing a colleague or a manager. By default it removes: screenshots and thumbnails, raw OCR text, `block_ids` / `seg_id`, internal notes, data-gap details, and local paths (drive-letter paths, `/Users/...`, `/home/...`, `data/<date>/...`, Git Bash `/e/...`, UNC). Only external `http(s)` links survive, and the whole document is run through the redaction rules one last time.

Output goes to `reports/share/<date>-share.md`. The file name does not follow the UI language, so switching languages never leaves a second copy behind. If the report is missing or the JSON is corrupt it fails loudly instead of writing half a file.

> The shareable export is still human-written text. Read it once yourself before sending it. It is not a redaction guarantee.

## Moving machines, several machines, USB sticks

- **New machine**: copy the whole folder over, double-click, and the wizard re-registers start at login (that registration is per machine and does not travel). Copy `data\`, `reports\` and `web\` together and the history comes along.
- **Data somewhere else**: set `WID_HOME=D:\MyWorkLog` and everything is written there, leaving the program directory clean. Setting it does **not** migrate existing data; copy that manually first.
- **USB stick**: pointing `WID_HOME` at a USB stick slows IO down; keeping data local and copying only `reports\` is the better trade.
- **Several machines, each on its own**: they do not interfere. To read them as one dashboard, collect each machine's `reports\*.json` into a single `reports\` and re-render.

## Uninstall

```bat
What-IDid.exe stop
What-IDid.exe autostart --remove
```

Then delete the whole folder (your data is in there too). If the data lives in `%LOCALAPPDATA%\What-IDid`, delete that as well.
