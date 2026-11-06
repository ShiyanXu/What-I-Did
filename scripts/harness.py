"""零依赖功能 Harness：校验功能清单、驱动状态流转并保留可审计轨迹。

所有默认路径均相对于本文件所在的仓库根目录。测试可通过 ``main`` 的
``root`` 与 ``state_dir`` 参数注入临时目录，不会触碰真实清单或状态。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
FEATURE_FILE = "feature_list.json"
STATUSES = ("not_started", "in_progress", "blocked", "passing")
TOP_LEVEL_FIELDS = ("project", "last_updated", "rules", "status_legend", "features")
FEATURE_FIELDS = (
    "id",
    "priority",
    "area",
    "title",
    "user_visible_behavior",
    "status",
    "verification",
    "verification_commands",
    "evidence",
    "notes",
    "depends_on",
)

# 清单会随仓库提交，命令又会原样进 trace；先在入口挡住凭据，而不是靠事后擦除。
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*=\s*\S"),
)


def _looks_like_secret(part: str) -> bool:
    """判断一个 argv 片段是否像硬编码凭据。"""
    return any(pattern.search(part) for pattern in SECRET_PATTERNS)


class HarnessError(Exception):
    """面向命令行用户的可修复错误。"""

    def __init__(self, what: str, why: str, how: str) -> None:
        super().__init__(what)
        self.what = what
        self.why = why
        self.how = how

    def format(self) -> str:
        """返回“什么 / 为什么 / 怎么修”的完整诊断。"""
        return f"什么：{self.what}\n为什么：{self.why}\n怎么修：{self.how}"


@dataclass
class CommandResult:
    """一条子进程命令的完整结果；输出本体保存在独立日志中。"""

    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    log_path: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_span_id() -> str:
    return uuid.uuid4().hex[:12]


def _relative_path(path: Path, root: Path) -> str:
    """尽量生成相对于仓库根的 POSIX 路径，测试注入目录也同样适用。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        try:
            return Path(os.path.relpath(path, root)).as_posix()
        except ValueError as exc:
            raise HarnessError(
                "无法生成相对日志路径",
                f"{path} 与仓库根 {root} 不在同一磁盘",
                "将 STATE_DIR 放到仓库所在磁盘，或改用默认 state/harness 目录",
            ) from exc


class Trace:
    """单次 CLI 会话的 JSONL 轨迹，任何写入失败都会显式报错。"""

    def __init__(self, root: Path, state_dir: Path) -> None:
        self.root = root
        self.state_dir = state_dir
        self.trace_id = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = state_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HarnessError(
                "无法创建 Harness trace 目录",
                str(exc),
                f"检查 {state_dir} 的权限与磁盘空间后重试",
            ) from exc

    @property
    def relative_path(self) -> str:
        """返回相对于仓库根的 trace 路径。"""
        return _relative_path(self.path, self.root)

    def log_path(self, span_id: str) -> Path:
        """为一个命令 span 分配同目录日志路径。"""
        return self.state_dir / f"{self.path.stem}-{span_id}.log"

    def emit(
        self,
        event: str,
        status: str,
        duration_ms: float = 0.0,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        feature_id: str | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> str:
        """追加一条 JSONL 事件并立即落盘，返回该事件的 span_id。"""
        span = span_id or _new_span_id()
        payload: dict[str, Any] = {
            "ts": _utc_now(),
            "trace_id": self.trace_id,
            "span_id": span,
            "event": event,
            "status": status,
            "duration_ms": round(float(duration_ms), 3),
        }
        if parent_span_id is not None:
            payload["parent_span_id"] = parent_span_id
        if feature_id is not None:
            payload["feature_id"] = feature_id
        if fields:
            payload.update(fields)
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
        except (OSError, TypeError, ValueError) as exc:
            raise HarnessError(
                "无法写入 Harness trace",
                str(exc),
                f"检查 {self.path} 的权限与磁盘空间；修复前不要把本次操作视为已审计",
            ) from exc
        return span


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_document(document: object) -> list[str]:
    """静态校验功能清单，返回全部人类可读错误；空列表表示合法。"""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["顶层必须是 JSON 对象"]

    for field in TOP_LEVEL_FIELDS:
        if field not in document:
            errors.append(f"缺少顶层字段 {field}")
    if "project" in document and not isinstance(document["project"], str):
        errors.append("project 必须是字符串")
    if "last_updated" in document and not isinstance(document["last_updated"], str):
        errors.append("last_updated 必须是字符串")

    features = document.get("features")
    if not isinstance(features, list):
        errors.append("features 必须是数组")
        return errors

    seen_ids: dict[str, int] = {}
    valid_features: dict[str, dict[str, Any]] = {}
    active_ids: list[str] = []

    for index, feature in enumerate(features):
        prefix = f"features[{index}]"
        if not isinstance(feature, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        for field in FEATURE_FIELDS:
            if field not in feature:
                errors.append(f"{prefix} 缺少字段 {field}")

        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id.strip():
            errors.append(f"{prefix}.id 必须是非空字符串")
        else:
            if feature_id in seen_ids:
                errors.append(f"功能 id 重复：{feature_id}（位置 {seen_ids[feature_id]} 与 {index}）")
            else:
                seen_ids[feature_id] = index
                valid_features[feature_id] = feature

        priority = feature.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            errors.append(f"{prefix}.priority 必须是整数")

        for field in ("area", "title", "user_visible_behavior"):
            if field in feature and not isinstance(feature[field], str):
                errors.append(f"{prefix}.{field} 必须是字符串")

        status = feature.get("status")
        if status not in STATUSES:
            errors.append(f"{prefix}.status 非法：{status!r}；允许值为 {', '.join(STATUSES)}")
        elif status == "in_progress":
            active_ids.append(str(feature_id))

        verification = feature.get("verification")
        if not _is_string_list(verification):
            errors.append(f"{prefix}.verification 必须是字符串数组")

        commands = feature.get("verification_commands")
        if not isinstance(commands, list):
            errors.append(f"{prefix}.verification_commands 必须是命令数组")
        else:
            for command_index, command in enumerate(commands):
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(part, str) for part in command)
                    or not command[0]
                ):
                    errors.append(
                        f"{prefix}.verification_commands[{command_index}] 必须是非空 argv 字符串数组"
                    )
                elif any(_looks_like_secret(part) for part in command):
                    errors.append(
                        f"{prefix}.verification_commands[{command_index}] 疑似含硬编码凭据；"
                        "凭据会随清单提交并进入 trace"
                    )

        evidence = feature.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"{prefix}.evidence 必须是数组")
        elif status == "passing" and not evidence:
            errors.append(f"{prefix} 状态为 passing，但 evidence 为空")
        elif status in ("not_started", "in_progress", "blocked") and evidence:
            errors.append(f"{prefix} 状态为 {status}，不应包含通过证据")

        notes = feature.get("notes")
        if not isinstance(notes, str) and not _is_string_list(notes):
            errors.append(f"{prefix}.notes 必须是字符串或字符串数组")

        depends_on = feature.get("depends_on")
        if not _is_string_list(depends_on) or any(not item for item in depends_on or []):
            errors.append(f"{prefix}.depends_on 必须是由非空字符串组成的数组")

    if len(active_ids) > 1:
        errors.append(f"最多只能有一个 in_progress，当前为：{', '.join(active_ids)}")

    for feature_id, feature in valid_features.items():
        depends_on = feature.get("depends_on")
        if isinstance(depends_on, list):
            for dependency in depends_on:
                if isinstance(dependency, str) and dependency not in valid_features:
                    errors.append(f"功能 {feature_id} 的 depends_on 引用了不存在的 id：{dependency}")

    # 依赖方向不影响无环判断；DFS 只遍历 id 与依赖均合法的节点。
    visit_state: dict[str, int] = {}
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(feature_id: str, path: list[str]) -> None:
        state = visit_state.get(feature_id, 0)
        if state == 2:
            return
        if state == 1:
            start = path.index(feature_id)
            cycle = path[start:] + [feature_id]
            key = tuple(cycle)
            if key not in reported_cycles:
                reported_cycles.add(key)
                errors.append(f"depends_on 存在依赖环：{' -> '.join(cycle)}")
            return
        visit_state[feature_id] = 1
        feature = valid_features[feature_id]
        dependencies = feature.get("depends_on")
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if dependency in valid_features:
                    visit(dependency, path + [feature_id])
        visit_state[feature_id] = 2

    for feature_id in valid_features:
        visit(feature_id, [])

    return errors


def load_document(root: Path) -> dict[str, Any]:
    """读取并静态校验仓库根下的 feature_list.json。"""
    path = root / FEATURE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HarnessError(
            "缺少 feature_list.json",
            f"未找到 {path}",
            "先在仓库根创建符合约定的功能清单，再运行 Harness",
        ) from exc
    except OSError as exc:
        raise HarnessError(
            "无法读取 feature_list.json",
            str(exc),
            "检查文件权限与路径后重试",
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HarnessError(
            "feature_list.json 不是合法 JSON",
            f"第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}",
            "修正 JSON 语法后重试",
        ) from exc
    errors = validate_document(document)
    if errors:
        raise HarnessError(
            "feature_list.json 静态校验失败",
            "\n".join(f"- {error}" for error in errors),
            "按上述字段、状态、证据与依赖规则修正清单后重试",
        )
    return document


def atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    """以 UTF-8、两格缩进和末尾换行原子写入 JSON。"""
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as exc:
        raise HarnessError(
            "无法原子写入 JSON",
            f"目标 {path}：{exc}",
            "检查目录权限与磁盘空间；原文件未被有意覆盖，修复后重试",
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                # 主异常已经明确报告；清理失败不覆盖原始诊断。
                pass


def _render_command(command: Sequence[str], root: Path) -> list[str]:
    return [
        part.replace("{python}", sys.executable).replace("{root}", str(root))
        for part in command
    ]


def _compact_command(command: Sequence[str], root: Path) -> list[str]:
    """把渲染后的 argv 折回占位符，用于写进会提交的证据。

    证据会随 `feature_list.json` 进仓库。绝对路径里带着用户名与解释器位置：
    换台机器读是噪声，公开仓库里是泄露，也让人误以为验证命令绑死在某一个解释器上。
    存占位符，回放时再由 `_render_command` 展开。
    """
    executable = sys.executable or ""
    root_text = str(root)
    compacted: list[str] = []
    for part in command:
        if executable and part == executable:
            compacted.append("{python}")
            continue
        if executable:
            part = part.replace(executable, "{python}")
        compacted.append(part.replace(root_text, "{root}") if root_text else part)
    return compacted


def _write_command_log(
    path: Path,
    command: Sequence[str],
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_ms: float,
) -> None:
    content = (
        f"command: {subprocess.list2cmdline(list(command))}\n"
        f"exit_code: {exit_code}\n"
        f"duration_ms: {duration_ms:.3f}\n"
        "===== stdout =====\n"
        f"{stdout}"
        f"{'' if not stdout or stdout.endswith(chr(10)) else chr(10)}"
        "===== stderr =====\n"
        f"{stderr}"
        f"{'' if not stderr or stderr.endswith(chr(10)) else chr(10)}"
    )
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise HarnessError(
            "无法写入命令完整日志",
            str(exc),
            f"检查 {path} 的权限与磁盘空间后重试",
        ) from exc


def run_command(
    command: Sequence[str],
    root: Path,
    trace: Trace,
    *,
    parent_span_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """以 argv、禁止 shell 的方式执行命令，记录输出、退出码、耗时与日志。"""
    argv = list(command)
    span_id = _new_span_id()
    log_path = trace.log_path(span_id)
    relative_log = _relative_path(log_path, root)
    child_env = dict(os.environ if env is None else env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    print(f"[命令] {subprocess.list2cmdline(argv)}", flush=True)
    started = __import__("time").monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            env=child_env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
    except OSError as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        exit_code = 127
    duration_ms = (__import__("time").monotonic() - started) * 1000
    _write_command_log(log_path, argv, stdout, stderr, exit_code, duration_ms)
    result = CommandResult(argv, stdout, stderr, exit_code, duration_ms, relative_log)

    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    print(
        f"[命令结果] exit_code={exit_code} duration_ms={duration_ms:.3f} log={relative_log}",
        flush=True,
    )
    trace.emit(
        "command",
        "success" if exit_code == 0 else "failed",
        duration_ms,
        span_id=span_id,
        parent_span_id=parent_span_id,
        fields={"command": argv, "exit_code": exit_code, "log_path": relative_log},
    )
    if exit_code != 0:
        raise HarnessError(
            "命令执行失败",
            f"argv={argv!r}，退出码 {exit_code}；完整输出见 {relative_log}",
            "根据日志修复命令或相关代码后重试；不要把失败结果登记为通过证据",
        )
    return result


def run_e2e(root: Path, trace: Trace, parent_span_id: str | None = None) -> None:
    """在临时 WID_HOME 中顺序运行离线夹具和边界测试，并自动清理。"""
    with tempfile.TemporaryDirectory(prefix="wid-harness-e2e-") as temporary:
        wid_home = Path(temporary) / "home"
        env = os.environ.copy()
        env["WID_HOME"] = str(wid_home)
        fixture = [sys.executable, str(root / "scripts" / "dev_fixture.py"), "--day", "2026-10-18", "--mock"]
        boundaries = [sys.executable, str(root / "scripts" / "check_boundaries.py")]
        try:
            run_command(fixture, root, trace, parent_span_id=parent_span_id, env=env)
            run_command(boundaries, root, trace, parent_span_id=parent_span_id, env=env)
        except HarnessError as exc:
            raise HarnessError(
                "隔离离线 E2E 失败",
                f"{exc.why}\n临时 WID_HOME={wid_home} 会在退出时自动清理，未使用生产数据目录",
                f"{exc.how}；修复后重新运行 python scripts/harness.py e2e",
            ) from exc


def _feature_map(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {feature["id"]: feature for feature in document["features"]}


def _get_feature(document: Mapping[str, Any], feature_id: str) -> dict[str, Any]:
    feature = _feature_map(document).get(feature_id)
    if feature is None:
        raise HarnessError(
            "找不到功能项",
            f"feature id 不存在：{feature_id}",
            "先运行 status 查看可用 id，并修正命令行参数",
        )
    return feature


def _save_feature_document(root: Path, document: dict[str, Any]) -> None:
    document["last_updated"] = date.today().isoformat()
    atomic_write_json(root / FEATURE_FILE, document)


def _assert_dispatchable(document: Mapping[str, Any], feature: Mapping[str, Any]) -> None:
    """WIP=1 与依赖门控；start 与 resume 共用同一套判定。"""
    active = [item["id"] for item in document["features"] if item["status"] == "in_progress"]
    if active:
        raise HarnessError(
            "WIP 门控阻止推进",
            f"已有 in_progress：{', '.join(active)}",
            "先 verify 或 block 当前功能，确保没有进行中的功能后再推进",
        )
    by_id = _feature_map(document)
    unfinished = [
        dependency for dependency in feature["depends_on"]
        if by_id[dependency]["status"] != "passing"
    ]
    if unfinished:
        details = "、".join(f"{item}({by_id[item]['status']})" for item in unfinished)
        raise HarnessError(
            "依赖门控阻止推进",
            f"功能 {feature['id']} 的依赖尚未全部 passing：{details}",
            "先完成并验证这些依赖，再重新推进",
        )


def _start(root: Path, feature_id: str) -> None:
    document = load_document(root)
    feature = _get_feature(document, feature_id)
    status = feature["status"]
    if status == "passing":
        raise HarnessError(
            "passing 不可逆",
            f"功能 {feature_id} 已通过，不能重新 start",
            "如需新工作，请新建功能项，不要改写已通过记录",
        )
    if status != "not_started":
        how = (
            "blocked 项先用 resume 记录解除证据再推进，passing 不可回退"
            if status == "blocked"
            else "先检查状态；in_progress 项应继续 verify，passing 不可回退"
        )
        raise HarnessError(
            "当前状态不能 start",
            f"功能 {feature_id} 当前为 {status}，仅 not_started 可开始",
            how,
        )
    _assert_dispatchable(document, feature)
    feature["status"] = "in_progress"
    _save_feature_document(root, document)
    print(f"已开始 {feature_id}：{feature['title']}")


def _append_note(feature: dict[str, Any], entry: str) -> None:
    """把一条带日期的记录追加到 notes，兼容字符串与字符串数组。"""
    notes = feature["notes"]
    if isinstance(notes, list):
        notes.append(entry)
    elif notes.strip():
        feature["notes"] = notes.rstrip() + "\n" + entry
    else:
        feature["notes"] = entry


def _resume(root: Path, feature_id: str, reason: str) -> None:
    """只有 blocked 项可恢复；必须带解除证据，并重新通过 WIP 与依赖门控。"""
    document = load_document(root)
    feature = _get_feature(document, feature_id)
    status = feature["status"]
    if status == "passing":
        raise HarnessError(
            "passing 不可逆",
            f"功能 {feature_id} 已通过，不能 resume",
            "保留通过记录；如有回归请新建功能项",
        )
    if status != "blocked":
        raise HarnessError(
            "当前状态不能 resume",
            f"功能 {feature_id} 当前为 {status}，仅 blocked 可恢复",
            "not_started 用 start，in_progress 用 verify，passing 不可回退",
        )
    clean_reason = reason.strip()
    if not clean_reason:
        raise HarnessError(
            "解除原因不能为空",
            "--reason 只包含空白字符",
            "写明阻塞已解除的具体证据，例如某个命令已通过",
        )
    _assert_dispatchable(document, feature)
    _append_note(feature, f"[{date.today().isoformat()}] 解除阻塞：{clean_reason}")
    feature["status"] = "in_progress"
    _save_feature_document(root, document)
    print(f"已恢复 {feature_id}，解除证据已追加到 notes")


def _verify(root: Path, feature_id: str, trace: Trace, parent_span_id: str) -> None:
    document = load_document(root)
    feature = _get_feature(document, feature_id)
    status = feature["status"]
    if status == "passing":
        raise HarnessError(
            "passing 不可逆",
            f"功能 {feature_id} 已通过，不能重复 verify 改写证据",
            "如需新验证，请新建功能项",
        )
    if status != "in_progress":
        raise HarnessError(
            "当前状态不能 verify",
            f"功能 {feature_id} 当前为 {status}，仅 in_progress 可验证",
            "先按门控执行 start，或检查是否选错功能 id",
        )
    commands = feature["verification_commands"]
    if not commands:
        raise HarnessError(
            "没有可执行的验证命令",
            f"功能 {feature_id} 的 verification_commands 为空，不能据此标记 passing",
            "补充至少一条非空 argv 验证命令后重试",
        )

    results: list[CommandResult] = []
    for command in commands:
        result = run_command(
            _render_command(command, root),
            root,
            trace,
            parent_span_id=parent_span_id,
        )
        results.append(result)

    evidence = {
        "recorded_at": _utc_now(),
        "commands": [
            {
                "command": _compact_command(result.command, root),
                "exit_code": result.exit_code,
                "duration_ms": round(result.duration_ms, 3),
                "log_path": result.log_path,
            }
            for result in results
        ],
        "trace_path": trace.relative_path,
        "summary": f"{len(results)}/{len(commands)} 条验证命令成功，退出码均为 0",
    }
    feature["status"] = "passing"
    feature["evidence"].append(evidence)
    _save_feature_document(root, document)
    print(f"已通过 {feature_id}：{evidence['summary']}；trace={trace.relative_path}")


def _block(root: Path, feature_id: str, reason: str) -> None:
    document = load_document(root)
    feature = _get_feature(document, feature_id)
    status = feature["status"]
    if status == "passing":
        raise HarnessError(
            "passing 不可逆",
            f"功能 {feature_id} 已通过，不能改为 blocked",
            "保留通过记录；如有回归，请新建功能项",
        )
    if status not in ("in_progress", "not_started"):
        raise HarnessError(
            "当前状态不能 block",
            f"功能 {feature_id} 当前为 {status}",
            "仅 in_progress 或 not_started 可标记 blocked",
        )
    clean_reason = reason.strip()
    if not clean_reason:
        raise HarnessError(
            "阻塞原因不能为空",
            "--reason 只包含空白字符",
            "提供具体阻塞原因以及需要的解除条件",
        )
    entry = f"[{date.today().isoformat()}] {clean_reason}"
    _append_note(feature, entry)
    feature["status"] = "blocked"
    _save_feature_document(root, document)
    print(f"已阻塞 {feature_id}；原因已追加到 notes")


def _schedulable(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_id = _feature_map(document)
    candidates = [
        feature for feature in document["features"]
        if feature["status"] == "not_started"
        and all(by_id[item]["status"] == "passing" for item in feature["depends_on"])
    ]
    # Python 排序稳定，同 priority 保持清单中的声明顺序。
    return sorted(candidates, key=lambda feature: feature["priority"])


def _status(root: Path) -> None:
    document = load_document(root)
    counts = {status: 0 for status in STATUSES}
    for feature in document["features"]:
        counts[feature["status"]] += 1
    print("状态计数：")
    for status in STATUSES:
        print(f"  {status}: {counts[status]}")
    active = [feature for feature in document["features"] if feature["status"] == "in_progress"]
    if active:
        print(f"当前 in_progress：{active[0]['id']} - {active[0]['title']}")
    else:
        print("当前 in_progress：无")
    candidates = _schedulable(document)
    print("下一可调度项（priority 升序，依赖全部 passing）：")
    if not candidates:
        print("  无")
    for feature in candidates:
        print(f"  [{feature['priority']}] {feature['id']} - {feature['title']}")


def _check(root: Path, trace: Trace, parent_span_id: str) -> None:
    load_document(root)
    run_command(
        [sys.executable, str(root / "scripts" / "check_harness.py")],
        root,
        trace,
        parent_span_id=parent_span_id,
    )
    run_command(
        [sys.executable, str(root / "scripts" / "check_privacy.py")],
        root,
        trace,
        parent_span_id=parent_span_id,
    )
    run_e2e(root, trace, parent_span_id)
    print("Harness check 全部通过：静态校验、自测、隐私检查、隔离 E2E。")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="What-I-Did 零依赖功能 Harness")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("check", help="静态校验并运行全部自测与隔离 E2E")
    subparsers.add_parser("e2e", help="仅运行隔离离线 E2E")
    subparsers.add_parser("status", help="显示状态计数、当前工作与下一可调度项")
    start = subparsers.add_parser("start", help="开始一个 not_started 功能")
    start.add_argument("feature_id")
    verify = subparsers.add_parser("verify", help="执行功能验证并在全部成功后登记证据")
    verify.add_argument("feature_id")
    block = subparsers.add_parser("block", help="阻塞功能并追加原因")
    block.add_argument("feature_id")
    block.add_argument("--reason", required=True)
    resume = subparsers.add_parser("resume", help="解除阻塞并记录解除证据")
    resume.add_argument("feature_id")
    resume.add_argument("--reason", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    state_dir: Path | None = None,
) -> int:
    """CLI 入口；root/state_dir 仅供隔离测试或嵌入调用注入。"""
    args = _parser().parse_args(argv)
    resolved_root = Path(
        root if root is not None else os.environ.get("WID_HARNESS_ROOT", ROOT)
    ).expanduser().resolve()
    if args.action == "status":
        try:
            _status(resolved_root)
            return 0
        except HarnessError as exc:
            print(f"[Harness 失败]\n{exc.format()}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            error = HarnessError(
                "status 发生未预期错误",
                f"{type(exc).__name__}: {exc}",
                "检查清单内容与文件权限；若仍失败，请保留诊断并修复 Harness",
            )
            print(f"[Harness 失败]\n{error.format()}", file=sys.stderr)
            return 1

    resolved_state = Path(
        state_dir
        if state_dir is not None
        else os.environ.get("WID_HARNESS_STATE_DIR", resolved_root / "state" / "harness")
    ).expanduser().resolve()
    try:
        trace = Trace(resolved_root, resolved_state)
    except HarnessError as exc:
        print(f"[Harness 失败]\n{exc.format()}", file=sys.stderr)
        return 1

    feature_id = getattr(args, "feature_id", None)
    started = __import__("time").monotonic()
    try:
        parent_span = trace.emit(args.action, "started", feature_id=feature_id)
    except HarnessError as exc:
        print(f"[Harness 失败]\n{exc.format()}", file=sys.stderr)
        return 1

    try:
        if args.action == "check":
            _check(resolved_root, trace, parent_span)
        elif args.action == "e2e":
            run_e2e(resolved_root, trace, parent_span)
        elif args.action == "start":
            _start(resolved_root, feature_id)
        elif args.action == "verify":
            _verify(resolved_root, feature_id, trace, parent_span)
        elif args.action == "block":
            _block(resolved_root, feature_id, args.reason)
        elif args.action == "resume":
            _resume(resolved_root, feature_id, args.reason)
        else:  # argparse 已限制取值，这里仅防止嵌入调用被意外扩展。
            raise HarnessError(
                "未知 Harness 操作",
                str(args.action),
                "使用 check/e2e/status/start/verify/block/resume",
            )
    except HarnessError as exc:
        duration = (__import__("time").monotonic() - started) * 1000
        try:
            trace.emit(
                args.action,
                "failed",
                duration,
                span_id=parent_span,
                feature_id=feature_id,
                fields={"error": exc.what},
            )
        except HarnessError as trace_exc:
            print(f"[Trace 写入失败]\n{trace_exc.format()}", file=sys.stderr)
        print(f"[Harness 失败]\n{exc.format()}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        duration = (__import__("time").monotonic() - started) * 1000
        error = HarnessError(
            f"{args.action} 发生未预期错误",
            f"{type(exc).__name__}: {exc}",
            "检查输入和文件权限；若仍失败，请根据诊断修复 Harness",
        )
        try:
            trace.emit(
                args.action,
                "failed",
                duration,
                span_id=parent_span,
                feature_id=feature_id,
                fields={"error": error.what},
            )
        except HarnessError as trace_exc:
            print(f"[Trace 写入失败]\n{trace_exc.format()}", file=sys.stderr)
        print(f"[Harness 失败]\n{error.format()}", file=sys.stderr)
        return 1

    duration = (__import__("time").monotonic() - started) * 1000
    try:
        trace.emit(
            args.action,
            "success",
            duration,
            span_id=parent_span,
            feature_id=feature_id,
        )
    except HarnessError as exc:
        print(f"[Harness 失败]\n{exc.format()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
