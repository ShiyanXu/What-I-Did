# Building and testing

> [简体中文](building.zh-CN.md) · **English**

For people who want to change the code or build their own package. If you only use the program, the [README](../README.md) is enough.

## Environment

| Item | Requirement |
| --- | --- |
| OS | Windows 10 / 11 x64 (depends on Win32 queries for window, idle and lock screen state) |
| Python | 3.10+ (source mode) |
| Runtime dependencies | `Pillow` `numpy` `requests` `Jinja2` `PyYAML` `psutil` |
| Optional dependencies | `rapidocr` `onnxruntime` (built-in offline OCR, about 100 MB) |
| Build dependencies | `pyinstaller` |

No database, no web framework, no scheduling library — scheduling uses Windows scheduled tasks, and the tray is hand-written with ctypes.

## Running from source

```bat
python -m pip install Pillow numpy requests Jinja2 PyYAML psutil
python -m pip install rapidocr onnxruntime      :: only needed for the built-in OCR

python wid.py setup
python wid.py doctor
python wid.py collect --once                    :: take one sample to see it work
python wid.py start                             :: run in the background
python wid.py report --date 2026-10-19 --force
```

Use `python wid.py daemon` to debug the resident logic; it takes over the current console. `wid.py` exposes the same subcommands as the packaged exe.

**Always use a separate data directory while developing**, otherwise samples land in your real records:

```bat
set WID_HOME=%CD%\.tmp_wid_dev
```

## Test scripts

All of them live under `scripts/`, none of them touch the network or your real records, and none need a model key.

| Script | Coverage | Usage |
| --- | --- | --- |
| `dev_fixture.py` | Builds a synthetic day of events + a fake model and exercises the whole chain | `python scripts\dev_fixture.py --day 2026-10-18 --mock`; `--clean` deletes it |
| `check_privacy.py` | 73 privacy and invalid-config checks: forged credential injection, zero requests with outbound off, pause/lock screen/blocklists, re-blocking after narrowing the blocklist, OCR address validation, no capture when cropping fails, build protection | `python scripts\check_privacy.py` |
| `check_boundaries.py` | 27 boundary and error-path checks: empty keyword, out-of-range date, corrupt JSON, empty week, duplicate manual entry, invalid time | Needs samples first; `python scripts\check_boundaries.py` |
| `verify_dpi.py` | 14 DPI and multi-monitor checks: screenshot size = window rectangle, cross-monitor origin offset, full screen = virtual desktop, refuse to write when coordinate spaces disagree | `python scripts\verify_dpi.py` |
| `gui_regression.py` | 41 click-level UI checks: switching between the four pages, manual entry validation, forget confirmation, review send/discard | Prepare the environment with `seed_gui_home.py` first |
| `seed_gui_home.py` | Prepares the GUI regression environment in one step (may only write to `.tmp_wid*`) | `python scripts\seed_gui_home.py .tmp_wid_gui` |
| `fake_llm_server.py` | Local OpenAI-compatible fake model; the `block_id` / `seg_id` it returns are scraped live from the request body | `python scripts\fake_llm_server.py 8799` |

A typical round:

```bat
set WID_HOME=%CD%\.tmp_wid_test
python scripts\dev_fixture.py --day 2026-10-18 --mock
python scripts\check_boundaries.py
python scripts\check_privacy.py
```

To test against the fake model, point `config.llm.base_url` at `http://127.0.0.1:8799/v1` and you can walk the whole "review preview → confirm and send → validate → render" path **without hitting a real model**.

## Packaging

```bat
python scripts\build_dist.py            :: incremental build, reuses .build\venv
python scripts\build_dist.py --fresh    :: rebuild the virtualenv when dependencies changed
```

Output:

```
dist\What-IDid\
  What-IDid.exe              about 5.5 MB, the only entry point
  _internal\                 about 244 MB: Python runtime, Tk, built-in OCR model (30 MB), templates, icons
  .wid-build-manifest.json   sha256 manifest of every file
```

The VC++ runtime is bundled (`vcruntime140.dll` / `msvcp140.dll`), so target machines do not need the redistributable.

### Why one exe can be both GUI and CLI

The program is linked without a console, so double-clicking does not flash a black window. When run with a subcommand it calls `AttachConsole` to join the console that invoked it, and output prints as usual; only if that fails does it open one of its own.

The side effect: cmd does not wait for a console-less process, so it "returns the prompt first, then prints". Output is not lost, it may interleave with the next command; to pipe it use `| more`, or `start /wait`.

### Build protection

`build_dist.py` inspects the output directory before downloading dependencies and refuses outright when:

- the directory holds runtime data (`data\`, `reports\`, `config\` or other unknown top-level content);
- it contains directory links or junctions;
- the old artifacts lack a safety manifest, or the manifest no longer matches the content (meaning it was modified).

It only moves the old directory to `.build\trash` once it is sure this is a pure build artifact. `--keep-old` controls whether a backup is kept after success; it does not skip the backup. Packaging directly from `build.spec` also refuses to overwrite an existing target directory.

To update a portable directory that holds personal data, stop capture and the UI first, back up and move the whole old directory out by hand, then build into an empty one — do not remove these protections.

### What you must update together when adding code

`MODULES` in `build.spec` is a **hand-written list**. PyInstaller's static analysis cannot see lazy imports, and a missing entry means an immediate `ImportError` in the packaged build while source mode works perfectly. After adding `src/*.py`:

```bat
:: check that src modules and MODULES agree
python - <<"EOF"
import pathlib, re
src = {p.stem for p in pathlib.Path("src").glob("*.py")} - {"__init__"}
spec = pathlib.Path("build.spec").read_text(encoding="utf-8")
mods = set(re.findall(r'"([a-z_]+)"', spec.split("MODULES")[1].split("]")[0]))
print("缺:", sorted(src - mods), " 多余:", sorted(mods - src))
EOF
```

Adding a config key means changing two places at once: `config.default.yaml` (for humans) and `DEFAULTS` in `src/config.py` (which takes part in type validation and default merging). Changing only the former skips validation, and the string `"false"` gets treated as true.

## Distribution

Zip the whole `dist\What-IDid\` directory; unpack and double-click. Do not ship a `config\` or `data\` inside it — those belong to the user.

It is a good idea to publish a SHA256 alongside the package so users can verify it.

## Common pitfalls

| Symptom | Cause and fix |
| --- | --- |
| Screenshots are all black | The scheduled task was set to "run whether or not the user is logged on", so the process lands in Session 0. It must be "run only when the user is logged on" |
| The packaged build crashes on start with stdout being None | `sys.stdout` is `None` in a console-less program, and any `print` blows up. Check for None at the entry point and in the logging setup |
| A child process hangs the parent command | Background processes started with `subprocess.Popen` must redirect stdout/stderr to `DEVNULL`, otherwise the parent waits for the pipe to close |
| Local HTTP requests fail through a proxy | The system may set `HTTP_PROXY`. Use `requests.Session(trust_env=False)` for local requests |
| RapidOCR floods the log with INFO and buries your own | It reconfigures the logger back to INFO when constructed; suppress the level both before and after construction |
| The tray icon is not in the resident area | Windows 10/11 puts new icons in the "^" overflow area by default and no API can force it; just tell the user to drag it out once |
| The tray menu does not close when you click outside | Call `SetForegroundWindow` before showing the menu, and follow up with `WM_NULL` afterwards |
| DPI awareness "is set but has no effect" | `ctypes.windll` does not check HRESULT, so a failure raises nothing. Check the return value explicitly and keep a desktop-level fallback |
| Registering the scheduled task fails | This repo's `autostart.py` prefers a user-level scheduled task and falls back to `HKCU\...\Run`; when neither works it tells you to do it manually |
| `tar -a -cf x.zip` does not produce a zip | Git for Windows' tar does not create zips; use Python's `zipfile` or 7-Zip |
| The build directory is in use | A `What-IDid.exe` is still running. Run `What-IDid.exe stop` and quit the tray first |

## Directory conventions

| Directory | Contents | Committed |
| --- | --- | --- |
| `src/` `scripts/` `docs/` | Source, scripts, documentation | Yes |
| `config.default.yaml` | Config template | Yes |
| `config/` | Local keys and bindings | No |
| `data/` `reports/` `web/` `state/` `logs/` | Runtime data | No |
| `.build/` `dist/` | Build intermediates and output | No |
| `.tmp_wid*/` | Isolated data directories for tests | No |
