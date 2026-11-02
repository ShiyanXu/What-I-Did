# FAQ

> [简体中文](faq.zh-CN.md) · **English**

## Install and startup

**Double-clicking shows "Windows protected your PC"**
The exe is not code-signed, and that is SmartScreen's default reaction. Click "More info" → "Run anyway". If corporate antivirus flags it (which happens occasionally with PyInstaller builds), whitelist the whole folder.

**Double-clicking does nothing, or the window flashes and disappears**
First check whether a black window flashed — that usually means it crashed on startup. Run `What-IDid.exe doctor` from the command line and read the output; check the logs under `logs\` if you need to.

**Does double-clicking again open a pile of windows**
No. It brings back the window that is already sitting in the tray.

**The program is still running after I click ×**
That is deliberate: closing the window only hides the UI in the tray and capture continues. To really quit, right-click the tray icon → "Quit (stop capture)".

**I cannot find the icon in the notification area**
Windows 10/11 puts new icons in the "^" overflow area by default; click once to expand it. Drag the icon onto the resident area and it stays out there.

## Capture

**The Overview page keeps saying "capture is not running"**
Click "Start capture". You can also run `What-IDid.exe start` and then `What-IDid.exe status` to check the heartbeat.

**Screenshots are all black / all white**
The scheduled task was set to "run whether or not the user is logged on", so the process lands in Session 0, which has no desktop. Delete the task and reinstall it with `What-IDid.exe autostart --install`, making sure it is "run only when the user is logged on".

**Does it keep recording while the screen is locked**
No. The lock screen always stops capture; `capture.pause_on_locked` in the config is only a legacy field, and setting `false` does not get around it.

**I want it to stop recording for a while (meeting, screen sharing)**
`What-IDid.exe pause --minutes 60`, or "Pause 30 minutes" in the UI. Resume with `resume` or the button. While paused only anonymous time ticks are kept.

**There are apps I do not want recorded**
Use the privacy lists on the Settings page, or edit the config: `privacy.blocked_apps` blocks them completely (only an anonymous state remains), `privacy.skip_ocr_apps` records only the app and duration. Restart capture to apply.

**Memory or disk usage is high**
Raise `capture.interval_sec`, lower `capture.max_width`, shorten `retention.shots_days` — all take effect immediately. Screenshots are the bulk of the size, and `shots_days: 3` saves a lot.

## Reports

**The report did not appear at the scheduled time**
Check three things in order:

1. `What-IDid.exe status` — the last line says whether it is "due but not generated" or "generated";
2. `What-IDid.exe doctor` — "report schedule" shows whether the scheduled task is registered, "report status" lists every date still waiting to be generated;
3. If both are fine, the Overview page shows a red banner "the report for X has not been generated → generate now".

The task runs every 20 minutes from 21:00; if the machine was off in the evening, a catch-up runs within 3 minutes of login.

**I want to change the report time**
Change `report.trigger_hour`, then **you must re-register the scheduled task** — the trigger time is written into the system at registration:

```bat
What-IDid.exe autostart --remove
What-IDid.exe autostart --install
```

**The log keeps repeating "awaiting review"**
`report.review_mode` is on. The content is ready, it is just not confirmed yet: click "Review and send" on the report page, or run `What-IDid.exe review approve`.

**The report is a "plain timeline version" with no AI narrative**
That means the model was not called, or the call failed. Check: is the key filled in, is `privacy.allow_llm` set to `true`, can `config.llm.base_url` be reached (there is a "Test connection" button on the Settings page). A degraded report is upgraded automatically within `report.degraded_retry_hours`, or you can re-run it by hand with `report --force`.

**The report says "degraded" even though the network is clearly back**
Upgrading has two gates: the window `report.degraded_retry_hours` (default 12 hours) and the count `report.max_upgrade_attempts` (default 3). Past the gates, only a manual `report --force` will do it.

**The report has no concrete project names**
Most likely OCR never ran (quota exhausted, or the window was skipped by a list). Check whether `status` is `ok` in `data\<date>\ocr.jsonl`. Lowering `capture.interval_sec` or raising `ocr.max_per_day` gets you more detail at the cost of more resources.

**A whole day has no report at all**
A day with no observations produces no empty report — this is to stop a fresh install from filling the page with blank days. Check whether `data\<date>\events.jsonl` exists for that day.

**The conclusions look right but the evidence does not match**
The program only guarantees that `block_id` really exists, not that "this block therefore this conclusion". That is the model's inference. If a segment is clearly misrecorded, relabel it with `manual override`, or "Forget this day" and re-run.

## Recognition

**OCR keeps failing**
Start with `What-IDid.exe ocr-test --screen` and read the output. The built-in RapidOCR has no external dependencies; if you want it faster, install Umi-OCR, fill in `ocr.umi_exe`, and enable the HTTP server in Umi's settings (default port 1224).

**Recognition is very slow**
The built-in engine runs on the CPU and large images take a lot of time. Lower `ocr.rapidocr.max_side` to 1280, or switch to Umi-OCR.

**The recognized text is garbled**
First raise `ocr.rapidocr.min_score` above 0.6 — low-confidence lines should not reach the report anyway. With very small fonts or unusual scaling, check that `ocr.crop_active_window` is `true` (it crops at native resolution).

## Data and privacy

**Where is the data actually stored**
`status` prints the data directory. It is the program directory by default; `%LOCALAPPDATA%\What-IDid` when that is not writable; wherever `WID_HOME` points if it is set.

**How do I confirm it does not upload my images**
Images never being uploaded is a design premise of the program: OCR is entirely local and model requests contain text only. To cut outbound calls entirely, set `privacy.allow_llm` to `false`, or stay in review mode without confirming. You can also read the report logs under `logs\` and `meta` in `reports\<date>.json` yourself.

**It recorded something it should not have**
`What-IDid.exe forget --minutes 30` deletes the last half hour, `forget --day` deletes a whole day. Deletion is irreversible, and it cannot recall what has already been sent to a model provider.

**The disk keeps filling up**
Run `clean --dry-run` to see what would be deleted, then drop `--dry-run` to do it. Note that cleanup is the capture process's job: with capture not running, nothing is cleaned up automatically.

## Migration and uninstall

**New machine**
Copy the whole folder over and double-click; the wizard re-registers start at login. Copy `data\`, `reports\` and `web\` together and the history comes along.

**I want the data somewhere else**
Set the `WID_HOME=D:\MyWorkLog` environment variable. Note that it does not migrate existing data — copy that first, then start.

**Full uninstall**

```bat
What-IDid.exe stop
What-IDid.exe autostart --remove
```

Then delete the whole folder. If the data lives in `%LOCALAPPDATA%\What-IDid`, delete that as well.

**What is the 2026-10-18 data in the repository**
Demo data produced by `scripts\dev_fixture.py`, marked as such on the page. To delete it: `python scripts\dev_fixture.py --day 2026-10-18 --clean`.

## Limits

- Observed duration is not working time: activities without input such as reading, watching lectures or meetings count as low activity.
- Reference validation is not truth: a real `block_id` does not mean the causal claim holds.
- Full-screen mode includes secondary displays and background windows; the app lists only judge the foreground app.
- Only Windows 10/11 x64 is supported; 32-bit and Windows 7 are not.
- Multi-monitor setups with mixed scaling have not been thoroughly validated; when coordinate spaces disagree the program skips that frame, and the worst case is a few missing records rather than misaligned images.
