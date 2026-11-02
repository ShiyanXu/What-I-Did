# Contributing

> [简体中文](CONTRIBUTING.zh-CN.md) · **English**

## Environment

Windows 10/11 x64 + Python 3.10+:

```bat
python -m pip install Pillow numpy requests Jinja2 PyYAML psutil
python -m pip install rapidocr onnxruntime      :: only if you need the built-in OCR
```

**Always use a separate data directory while developing**, otherwise samples end up in your real records:

```bat
set WID_HOME=%CD%\.tmp_wid_dev
```

## Code conventions

- **No new dependencies.** The runtime dependency list is fixed: the standard library + `Pillow numpy requests Jinja2 PyYAML psutil`. The tray, dedupe and scheduling are all hand-built on the standard library or ctypes; a new dependency needs a strong justification.
- **Get paths from `src/paths.py` only**; do not join paths anywhere else.
- **One source per decision.** "Should a report run" is `tick.pending()`; start and stop of capture is `control.py`; the status snapshot is `status.py`; the self-check is `doctor.py`. The UI and the CLI both call these; do not write a second copy.
- **The model produces JSON only, layout stays local.** New layout goes in `src/templates/`; do not expect the model to output formatting.
- **Structural changes add fields only**, and `schema_version` stays at 1; missing fields in old data are treated as empty.
- **A new config key changes two places at once**: `config.default.yaml` (for humans) and `DEFAULTS` in `src/config.py` (which takes part in type validation and default merging). Change only the former and a string like `"false"` is treated as true.
- **A new `src/*.py` must be added to `MODULES` in `build.spec`.** That list is hand-written, static analysis cannot see lazy imports, and a missing entry gives you an `ImportError` in the packaged build while source mode works perfectly.

## Self-test before committing

```bat
python -m py_compile wid.py src\*.py src\refs\*.py scripts\*.py

set WID_HOME=%CD%\.tmp_wid_test
python scripts\dev_fixture.py --day 2026-10-18 --mock
python scripts\check_boundaries.py
python scripts\check_privacy.py
python scripts\verify_dpi.py
```

Changes to the UI also need the click-level regression:

```bat
python scripts\seed_gui_home.py .tmp_wid_gui
set WID_HOME=%CD%\.tmp_wid_gui
python scripts\gui_regression.py
```

None of these scripts touch the network, need a model key, or go near your real records. Details are in [building and testing](docs/building.md).

## Commit messages

One commit does one thing, and the message explains **why**, not just what changed. When behaviour changes, spell out in the body what it means for existing data and existing configuration.

## Documentation

User-visible behaviour changes come with documentation: features go in `docs/usage.md`, config keys in `docs/configuration.md`, privacy boundaries in `docs/privacy.md`, architecture or data model changes in `docs/architecture.md`.

## Privacy red lines

No change that lets images leave the machine will be accepted. Neither will any capture path that bypasses "pause / lock screen / blocklists / invalid config" — `scripts/check_privacy.py` blocks that class of change.
