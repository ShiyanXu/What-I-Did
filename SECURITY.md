# Security policy

> [简体中文](SECURITY.zh-CN.md) · **English**

## Reporting a problem

If you have found a **privacy or security defect** (data sent somewhere it should not be, a redaction rule that can be bypassed, a local HTTP endpoint reachable from outside), please **do not** open a public issue. Report it privately through the repository's **Security → Advisories → Report a vulnerability**, or contact the maintainer directly.

Ordinary bugs and feature requests are fine as public issues.

When reporting, please include:

- the version (the `v` number shown in the bottom-left of the UI, or `git rev-parse --short HEAD`)
- reproduction steps, plus **redacted** log excerpts that show the problem
- your Windows version

**Please do not attach real `data\`, `reports\` or screenshots** — they contain sensitive content by definition. Samples built with `scripts\dev_fixture.py` reproduce almost every issue.

## Defined security boundaries

These are not vulnerabilities; they are behaviours the design explicitly accepts:

| Behaviour | Explanation |
| --- | --- |
| Screenshots may contain sensitive content inside a window | The program crops by window rectangle and does no pixel-level detection or masking; control it with the blocklists and pause |
| Full-screen mode includes secondary displays and background windows | `all_screens` must be enabled explicitly |
| Regex redaction may miss things | The rules do not understand meaning and only cover known shapes |
| Sent content cannot be recalled | Local deletion does not affect data already sent to a model provider |
| The shareable export is not a redaction guarantee | Review it yourself after exporting |
| The build manifest is not a signature | It guards against accidental deletion, not tampering |

## Recommendations

- Keep the model key in the `WID_LLM_API_KEY` environment variable or `config\api_key.txt`, never in a file you might share.
- Do not commit `data\`, `reports\`, `web\`, `state\`, `logs\`, `config.yaml` or `config\api_key.txt` to a public repository. The repository `.gitignore` already covers these paths, but run `git status` before committing anyway.
- Put apps involving passwords, payments or private keys in `privacy.blocked_apps`; put chat tools in `privacy.skip_ocr_apps`.
- When you do not want anything sent out, `privacy.allow_llm: false` is the only complete switch — `report.open_browser: false` merely stops the browser from opening.

## Support scope

Only the latest version on the main branch is maintained. Platforms other than Windows 10/11 x64 are outside the support scope.
