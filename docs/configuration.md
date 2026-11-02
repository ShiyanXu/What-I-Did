# Configuration reference

> [简体中文](configuration.zh-CN.md) · **English**

There is exactly one config file: `config.yaml` in the data directory, copied from `config.default.yaml` on first run.

## Location and precedence

```
<data dir>\
  config.yaml            main config
  config\api_key.txt     model key
  config\bindings.yaml   manual document bindings (optional)
```

The data directory is resolved in the order `WID_HOME` → the program directory (used when writable) → `%LOCALAPPDATA%\What-IDid`. The model key is read in this order of precedence: the `WID_LLM_API_KEY` environment variable > `config\api_key.txt` > the value saved in the UI.

## When changes take effect

| Category | When it applies |
| --- | --- |
| Privacy policy, outbound switch | Re-read before every capture and every request |
| Sampling, dedupe, OCR engine parameters | Requires restarting the capture process (saving in the UI does that automatically) |
| Report hour `report.trigger_hour` | Requires **re-registering the scheduled task** — the trigger time is written into the system at registration |
| Model endpoint, model name, budget | The next report generation |
| UI language `ui.language` | The window is rebuilt when you save (capture restarts as it does for any save) |

## General

| Key | Default | Description |
| --- | --- | --- |
| `app.day_cutoff_hour` | `4` | Where a day starts. 00:00–04:00 counts towards the previous day, so a late night does not split a day in two |
| `app.timezone` | `Asia/Shanghai` | One timezone for the whole project; every timestamp in a report uses it |

## User interface `ui`

```yaml
ui:
  language: en          # en (default) / zh-CN / auto (follow the system)
```

| Key | Default | Description |
| --- | --- | --- |
| `ui.language` | `en` | The UI language, and the headings and labels of the daily report |

- `en` is the default; `zh-CN` switches to Simplified Chinese; `auto` follows the Windows UI language. Anything unrecognized falls back to `en`.
- English is the source language, so a sentence with no translation yet degrades to English rather than showing a key or a blank.
- Changing it in **Settings → Language → Display language** rebuilds the whole window when you save — tkinter computes its layout from the label text, so the labels cannot be swapped in place.
- The `WID_LANG` environment variable overrides `ui.language` and has the highest priority. It is meant for scripts and self-tests that need one run in another language without touching the config.
- The language of the report body is decided by the model output and is not affected by this setting.

## Capture `capture`

| Key | Default | Description |
| --- | --- | --- |
| `interval_sec` | `20` | Sampling interval |
| `jitter_sec` | `3` | Random jitter, so the rhythm does not look robotic |
| `max_width` | `1920` | Maximum width written to disk |
| `jpeg_quality` | `62` | The balance between legibility and file size |
| `thumb_width` | `480` | Thumbnail width (used by the report page) |
| `keyframe_sec` | `300` | Keep one frame every 5 minutes even when the picture never changes, so the timeline has no gap |
| `idle_pause_sec` | `300` | Stop writing frames after 5 minutes without keyboard or mouse input |
| `pause_on_locked` | `true` | Legacy field. Lock screen now always stops capture; setting `false` does not bypass it |
| `min_free_gb` | `3` | Stop writing frames below this much free disk; the heartbeat is marked `low_disk` |
| `min_save_gap_sec` | `90` | Minimum gap between writes for minor changes |

## Dedupe `dedupe`

| Key | Default | Description |
| --- | --- | --- |
| `hamming_threshold` | `4` | dHash Hamming distance ≤ 4 counts as similar |
| `cell_grid` | `[12, 8]` | Granularity of the grid mean-difference comparison |
| `cell_diff_dup` | `0.02` | Grid difference < 2% is judged a complete duplicate |
| `cell_diff_major` | `0.22` | Grid difference > 22% is judged a major change |

## OCR `ocr`

| Key | Default | Description |
| --- | --- | --- |
| `engine` | `auto` | `auto` / `umi_http` / `umi_cli` / `rapidocr` / `none` |
| `umi_exe` | `""` | If Umi-OCR is installed, point this at it for a significant speed-up (enable its HTTP server first) |
| `rapidocr.max_side` | `1600` | Detection-side length cap; larger is more accurate and slower |
| `rapidocr.min_score` | `0.5` | Lines below this confidence are dropped |
| `warmup_on_start` | `true` | Warm up the engine at startup so the first image does not wait |
| `crop_active_window` | `true` | Crop the active window at native resolution before recognizing; small text comes out better |
| `min_interval_same_window_sec` | `45` | Minimum interval between two recognitions of the same window |
| `periodic_sample_sec` | `180` | Sample once every 3 minutes even when you stay in one window for a long time |
| `max_per_minute` | `6` | Recognition cap per minute |
| `max_per_day` | `1200` | Recognition cap per day |
| `max_chars_per_shot` | `6000` | How much text to take from a single image |
| `timeout_sec` | `90` | Timeout for one recognition |
| `recover_probe_sec` | `600` | After falling back to a backup engine, re-probe the preferred engines this often |
| `http_url` | `http://127.0.0.1:1224/api/ocr` | Loopback addresses only; no system proxy, no redirects |

`auto` means "use Umi-OCR if it is there, otherwise the built-in RapidOCR"; when neither is available it degrades to recording window titles only.

## Privacy `privacy`

| Key | Default | Description |
| --- | --- | --- |
| `store_screenshots` | `true` | When `false`, no images are written and normal capture does no OCR either; only redacted titles remain |
| `capture_scope` | `active_window` | `active_window` / `all_screens`. Skipped when the window is unavailable, **never falling back to the full screen** |
| `allow_llm` | `true` | `false` blocks every model request (including the connection test); local recording and timeline reports carry on |
| `blocked_apps` | a set of password managers | Exact process names, case-insensitive. Only an anonymous state is kept: no app, title, image or OCR |
| `skip_ocr_apps` | WeChat/QQ and similar | Record only the app and observed duration: no title, no screenshot, no OCR; the same filter applies when reprocessing old data |
| `blocked_title_patterns` | passwords/payments/banking and similar | A title matching the regex is anonymized; **an invalid regex invalidates the config**, it is never silently ignored |
| `redact_patterns` | `[]` | Custom redaction regexes, applied on write, on history re-read and at the model boundary |

`sk-`, GitHub tokens (including `github_pat_`), national ID numbers, phone numbers and emails have built-in rules that cannot be turned off. Regular expressions cannot recognize everything sensitive, and they do not blur pixels inside a screenshot — block or pause sensitive apps instead.

## Retention and cleanup `retention`

| Key | Default | Description |
| --- | --- | --- |
| `shots_days` | `7` | Days to keep screenshots and thumbnails |
| `events_days` | `30` | Days to keep event streams and redacted OCR |
| `report_excerpt_days` | `null` | OCR excerpts in report appendices: `null` follows `events_days`; `0` means no excerpts are written and old copies are removed during cleanup |
| `keyframes_days` | `30` | Legacy field; keyframes are not retained separately yet |
| `max_total_gb` | `20` | Cleanup deletes the oldest screenshots first; this is **not a hard cap on all data** |
| `clean_hour` | `4` | The capture process cleans up after this hour each day; with capture not running, nothing is cleaned up automatically |

Cleanup runs inside the capture process, not as a separate scheduled task. The preview writes no files; cleanup is mutually exclusive with report generation, and a busy or partially failed run tells you to retry.

## Model `llm`

| Key | Default | Description |
| --- | --- | --- |
| `base_url` | `https://api.deepseek.com/v1` | Any OpenAI-compatible endpoint |
| `model` | `deepseek-chat` | Model name |
| `api_key_file` | `config/api_key.txt` | Relative to the data directory |
| `api_key_env` | `WID_LLM_API_KEY` | Takes precedence over the file |
| `timeout_sec` | `180` | Timeout for a single request |
| `max_retries` | `3` | Maximum attempts per round (the first one included) |
| `input_char_budget` | `24000` | **Hard cap** on input characters; even fixed sections are trimmed step by step and finally hard-truncated |
| `max_calls_per_report` | `12` | Call cap for one report (repair rounds included); going over degrades immediately |
| `temperature` | `0.2` | Low temperature, to keep the narrative from drifting |

To switch to Qwen, Zhipu, SiliconFlow or a local Ollama, change only `base_url` and `model`.

## Daily report `report`

| Key | Default | Description |
| --- | --- | --- |
| `trigger_hour` | `21` | What hour to generate the report. Re-register the scheduled task after changing it |
| `catchup_days` | `7` | How many missed days to catch up on after boot |
| `min_active_minutes` | `20` | With observations but too little activity, generate an empty-day note; with no observations at all, skip |
| `open_browser` | `true` | Whether to open the dashboard after generating. Automatic generation opens it **only for the final version** (the 21:00 first version and evening incremental updates do not, and a catch-up covering several days opens only the most recent); the manual `report` command opens it on every run, `--no-open` disables that. **This is not outbound approval** — to forbid outbound calls use `privacy.allow_llm` |
| `review_mode` | `false` | With `true`, the pending content is written to disk first and only sent after you confirm |
| `max_upgrade_attempts` | `3` | How many times a degraded report may be upgraded automatically, to avoid repeated calls while offline |
| `degraded_retry_hours` | `12` | A degraded report may only be retried automatically within this window |
| `gap_threshold_min` | `10` | A gap longer than 10 minutes breaks the block |
| `max_block_min` | `30` | Maximum length of a single block: 30 minutes |
| `max_session_min` | `60` | Maximum length of a behavior segment: 60 minutes |
| `refresh_gap_min` | `90` | After a report exists, at least this long must pass before an incremental update is allowed |

## Document references `refs` and note sync `obsidian`

| Key | Default | Description |
| --- | --- | --- |
| `refs.enabled` | `true` | Whether to resolve document references |
| `refs.providers` | `[local_file, obsidian, url]` | Enabled resolvers; `kb` is registered but off by default |
| `refs.allow_unresolved_links` | `false` | No link when the file does not exist, to avoid dead links |
| `refs.cache_ttl_days` | `30` | Days to cache resolution results |
| `refs.resolve_timeout_sec` | `15` | Timeout for a single resolution |
| `refs.search_roots` | `[]` | Directories searched during automatic resolution; when empty only absolute paths are recognized |
| `refs.vault_dir` / `vault_name` | `""` | Obsidian vault location and display name |
| `refs.kb.*` | off | Reserved knowledge-base settings, currently an empty implementation |
| `obsidian.enabled` / `vault_dir` | `false` / `""` | Sync daily reports into the note vault |

## Invalid configuration

Invalid YAML, a wrong type on a known field (for example a boolean written as the string `"false"`), or an invalid privacy regex makes the program **stop content capture and outbound calls** rather than silently falling back to defaults:

- the UI and `doctor` show the specific reason, and `status` carries `privacy_error` in its JSON;
- your broken config is never overwritten;
- when runtime data exists but the config file is missing, it refuses to recreate defaults — so it never "looks fine" while actually running on a different configuration.

Saving validates first and then atomically replaces the single file, so it never ends up half-written.

## Common tweaks

```yaml
# switch model provider
llm:
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-plus"

# generate at 22:00 instead (re-register the scheduled task after changing it)
report:
  trigger_hour: 22

# see the active window only, never capture the full screen (the default; writing it out is reassuring)
privacy:
  capture_scope: active_window

# no outbound calls at all, local timeline only
privacy:
  allow_llm: false

# review before sending
report:
  review_mode: true

# keep screenshots for 3 days, events for 14
retention:
  shots_days: 3
  events_days: 14
```

Re-register after changing the report hour, because the scheduled task's trigger time is written into the system at registration:

```bat
What-IDid.exe autostart --remove
What-IDid.exe autostart --install
```
