"""Harness 的零依赖隔离测试：仅写临时清单与临时 state，不读真实运行数据。"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

# 即使调用者忘记 python -B，也不让测试在仓库生成 __pycache__。
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402


def make_feature(
    feature_id: str = "f1",
    *,
    priority: int = 1,
    status: str = "not_started",
    depends_on: list[str] | None = None,
    commands: list[list[str]] | None = None,
    evidence: list[Any] | None = None,
    notes: str | list[str] = "",
) -> dict[str, Any]:
    """构造一份字段完整的独立功能项。"""
    if commands is None:
        commands = [["{python}", "-c", "print('verification ok')"]]
    return {
        "id": feature_id,
        "priority": priority,
        "area": "测试区域",
        "title": f"标题 {feature_id}",
        "user_visible_behavior": "用户可以看到状态按门控变化",
        "status": status,
        "verification": ["命令退出码为 0"],
        "verification_commands": commands,
        "evidence": [] if evidence is None else evidence,
        "notes": notes,
        "depends_on": [] if depends_on is None else depends_on,
    }


class HarnessTests(unittest.TestCase):
    """每个用例都有独立仓库根、feature_list.json 与 trace 目录。"""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wid-harness-tests-")
        self.root = Path(self.temporary.name) / "repo"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        self.state = self.root / "state" / "harness"
        self.document: dict[str, Any] = {
            "project": "Harness 隔离测试",
            "last_updated": "2026-10-18",
            "rules": ["passing 必须有证据"],
            "status_legend": {status: status for status in harness.STATUSES},
            "features": [make_feature()],
        }
        self.write_document()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_document(self) -> None:
        """写临时清单，而非仓库真实 feature_list.json。"""
        (self.root / "feature_list.json").write_text(
            json.dumps(self.document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_document(self) -> dict[str, Any]:
        return json.loads((self.root / "feature_list.json").read_text(encoding="utf-8"))

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = harness.main(list(arguments), root=self.root, state_dir=self.state)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_01_valid_sample(self) -> None:
        self.assertEqual(harness.validate_document(self.document), [])
        code, output, error = self.invoke("status")
        self.assertEqual(code, 0, error)
        self.assertIn("not_started: 1", output)
        self.assertIn("f1", output)

    def test_02_missing_required_field(self) -> None:
        del self.document["features"][0]["title"]
        errors = harness.validate_document(self.document)
        self.assertTrue(any("缺少字段 title" in item for item in errors), errors)

    def test_03_duplicate_id(self) -> None:
        self.document["features"].append(copy.deepcopy(self.document["features"][0]))
        errors = harness.validate_document(self.document)
        self.assertTrue(any("id 重复" in item for item in errors), errors)

    def test_04_invalid_status(self) -> None:
        self.document["features"][0]["status"] = "done"
        errors = harness.validate_document(self.document)
        self.assertTrue(any("status 非法" in item for item in errors), errors)

    def test_05_passing_requires_evidence(self) -> None:
        self.document["features"][0]["status"] = "passing"
        errors = harness.validate_document(self.document)
        self.assertTrue(any("evidence 为空" in item for item in errors), errors)

    def test_06_non_passing_rejects_pass_evidence(self) -> None:
        self.document["features"][0]["evidence"] = [{"summary": "伪造通过"}]
        errors = harness.validate_document(self.document)
        self.assertTrue(any("不应包含通过证据" in item for item in errors), errors)

    def test_07_multiple_active_features_rejected(self) -> None:
        self.document["features"] = [
            make_feature("f1", status="in_progress"),
            make_feature("f2", status="in_progress"),
        ]
        errors = harness.validate_document(self.document)
        self.assertTrue(any("最多只能有一个 in_progress" in item for item in errors), errors)

    def test_08_missing_dependency_reference(self) -> None:
        self.document["features"][0]["depends_on"] = ["missing"]
        errors = harness.validate_document(self.document)
        self.assertTrue(any("不存在的 id：missing" in item for item in errors), errors)

    def test_09_dependency_cycle(self) -> None:
        self.document["features"] = [
            make_feature("f1", depends_on=["f2"]),
            make_feature("f2", depends_on=["f1"]),
        ]
        errors = harness.validate_document(self.document)
        self.assertTrue(any("依赖环" in item for item in errors), errors)

    def test_10_priority_must_be_integer_not_bool(self) -> None:
        self.document["features"][0]["priority"] = True
        errors = harness.validate_document(self.document)
        self.assertTrue(any("priority 必须是整数" in item for item in errors), errors)

    def test_11_wip_gate(self) -> None:
        self.document["features"] = [
            make_feature("f1"),
            make_feature("f2", status="in_progress"),
        ]
        self.write_document()
        code, _, error = self.invoke("start", "f1")
        self.assertEqual(code, 1)
        self.assertIn("WIP 门控", error)
        self.assertEqual(self.read_document()["features"][0]["status"], "not_started")

    def test_12_dependency_gate(self) -> None:
        self.document["features"] = [
            make_feature("f1", depends_on=["f2"]),
            make_feature("f2", status="blocked"),
        ]
        self.write_document()
        code, _, error = self.invoke("start", "f1")
        self.assertEqual(code, 1)
        self.assertIn("依赖门控", error)
        self.assertEqual(self.read_document()["features"][0]["status"], "not_started")

    def test_13_start_success_and_atomic_json_format(self) -> None:
        self.document["features"] = [
            make_feature("f1", depends_on=["f2"]),
            make_feature("f2", status="passing", evidence=[{"summary": "已验证"}]),
        ]
        self.write_document()
        code, _, error = self.invoke("start", "f1")
        self.assertEqual(code, 0, error)
        saved = self.read_document()
        self.assertEqual(saved["features"][0]["status"], "in_progress")
        self.assertEqual(saved["last_updated"], date.today().isoformat())
        raw = (self.root / "feature_list.json").read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertIn("标题 f1", raw)
        self.assertNotIn("\\u6807", raw)
        self.assertFalse(list(self.root.glob(".feature_list.json.*.tmp")))

    def test_14_failed_verification_does_not_pass(self) -> None:
        self.document["features"] = [make_feature(
            "f1",
            status="in_progress",
            commands=[["{python}", "-c", "import sys; print('failure output'); sys.exit(7)"]],
        )]
        self.write_document()
        code, _, error = self.invoke("verify", "f1")
        self.assertEqual(code, 1)
        self.assertIn("退出码 7", error)
        feature = self.read_document()["features"][0]
        self.assertEqual(feature["status"], "in_progress")
        self.assertEqual(feature["evidence"], [])
        logs = list(self.state.glob("*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("failure output", logs[0].read_text(encoding="utf-8"))

    def test_15_only_all_successful_commands_pass(self) -> None:
        self.document["features"] = [make_feature(
            "f1",
            status="in_progress",
            commands=[
                ["{python}", "-c", "print('first ok')"],
                [
                    "{python}", "-c",
                    "from pathlib import Path; import sys; assert Path(sys.argv[1]).is_dir()",
                    "{root}",
                ],
            ],
        )]
        self.write_document()
        code, _, error = self.invoke("verify", "f1")
        self.assertEqual(code, 0, error)
        feature = self.read_document()["features"][0]
        self.assertEqual(feature["status"], "passing")
        self.assertEqual(len(feature["evidence"]), 1)
        evidence = feature["evidence"][0]
        self.assertEqual(len(evidence["commands"]), 2)
        self.assertTrue(all(item["exit_code"] == 0 for item in evidence["commands"]))
        self.assertTrue(evidence["recorded_at"].endswith("Z"))
        self.assertIn("2/2", evidence["summary"])
        self.assertTrue((self.root / evidence["trace_path"]).is_file())
        for item in evidence["commands"]:
            self.assertTrue((self.root / item["log_path"]).is_file())

    def test_16_passing_is_irreversible(self) -> None:
        self.document["features"] = [make_feature(
            "f1", status="passing", evidence=[{"summary": "历史通过证据"}]
        )]
        self.write_document()
        for arguments in (
            ("start", "f1"),
            ("verify", "f1"),
            ("block", "f1", "--reason", "试图回退"),
            ("resume", "f1", "--reason", "试图回退"),
        ):
            code, _, error = self.invoke(*arguments)
            self.assertEqual(code, 1)
            self.assertIn("passing 不可逆", error)
        feature = self.read_document()["features"][0]
        self.assertEqual(feature["status"], "passing")
        self.assertEqual(feature["evidence"], [{"summary": "历史通过证据"}])

    def test_17_block_appends_reason(self) -> None:
        self.document["features"] = [
            make_feature("f1", status="in_progress", notes="旧备注"),
            make_feature("f2", notes=[]),
        ]
        self.write_document()
        code, _, error = self.invoke("block", "f1", "--reason", "等待外部依赖")
        self.assertEqual(code, 0, error)
        first = self.read_document()["features"][0]
        self.assertEqual(first["status"], "blocked")
        self.assertIn("旧备注", first["notes"])
        self.assertIn("等待外部依赖", first["notes"])
        code, _, error = self.invoke("block", "f2", "--reason", "尚未具备条件")
        self.assertEqual(code, 0, error)
        second = self.read_document()["features"][1]
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(len(second["notes"]), 1)
        self.assertIn("尚未具备条件", second["notes"][0])

    def test_18_trace_jsonl_is_parseable_and_hides_fake_secret(self) -> None:
        # 命令输出含伪造秘密，但 argv 与 JSONL 事件中不得出现完整秘密。
        self.document["features"] = [make_feature(
            "f1",
            status="in_progress",
            commands=[["{python}", "-c", "print('FAKE' + '_SECRET')"]],
        )]
        self.write_document()
        code, _, error = self.invoke("verify", "f1")
        self.assertEqual(code, 0, error)
        traces = list(self.state.glob("*.jsonl"))
        self.assertEqual(len(traces), 1)
        raw = traces[0].read_text(encoding="utf-8")
        self.assertNotIn("FAKE_SECRET", raw)
        events = [json.loads(line) for line in raw.splitlines() if line]
        self.assertGreaterEqual(len(events), 3)
        for event in events:
            for field in ("ts", "trace_id", "span_id", "event", "status", "duration_ms"):
                self.assertIn(field, event)
        commands = [event for event in events if event["event"] == "command"]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command["exit_code"], 0)
        self.assertIn("parent_span_id", command)
        self.assertIn("log_path", command)
        log = self.root / command["log_path"]
        self.assertIn("FAKE_SECRET", log.read_text(encoding="utf-8"))
        feature_events = [event for event in events if event["event"] == "verify"]
        self.assertTrue(all(event["feature_id"] == "f1" for event in feature_events))
        self.assertEqual(len(traces[0].stem.rsplit("-", 1)[1]), 8)

    def test_19_status_orders_schedulable_features(self) -> None:
        self.document["features"] = [
            make_feature("later", priority=10),
            make_feature("first", priority=1, depends_on=["done"]),
            make_feature("waiting", priority=0, depends_on=["blocked"]),
            make_feature("done", priority=2, status="passing", evidence=[{"summary": "ok"}]),
            make_feature("blocked", priority=2, status="blocked"),
        ]
        self.write_document()
        code, output, error = self.invoke("status")
        self.assertEqual(code, 0, error)
        self.assertLess(output.index("first"), output.index("later"))
        self.assertNotIn("waiting -", output)
        self.assertIn("passing: 1", output)
        self.assertIn("blocked: 1", output)

    def test_20_e2e_uses_temporary_wid_home_and_cleans_it(self) -> None:
        (self.scripts / "dev_fixture.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "home = Path(os.environ['WID_HOME'])\n"
            "home.mkdir(parents=True, exist_ok=True)\n"
            "(home / 'fixture.ok').write_text('ok', encoding='utf-8')\n"
            "root = Path(__file__).resolve().parent.parent\n"
            "(root / 'observed-home.txt').write_text(str(home), encoding='utf-8')\n"
            "print('fixture ok')\n",
            encoding="utf-8",
        )
        (self.scripts / "check_boundaries.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "home = Path(os.environ['WID_HOME'])\n"
            "assert (home / 'fixture.ok').is_file()\n"
            "print('boundaries ok')\n",
            encoding="utf-8",
        )
        code, output, error = self.invoke("e2e")
        self.assertEqual(code, 0, error)
        self.assertIn("fixture ok", output)
        self.assertIn("boundaries ok", output)
        observed_home = Path((self.root / "observed-home.txt").read_text(encoding="utf-8"))
        self.assertFalse(observed_home.exists())
        raw_trace = next(self.state.glob("*.jsonl")).read_text(encoding="utf-8")
        events = [json.loads(line) for line in raw_trace.splitlines()]
        self.assertEqual(len([event for event in events if event["event"] == "command"]), 2)
        self.assertFalse(list(self.root.glob("data/**/*")))

    def test_21_empty_verification_commands_cannot_pass(self) -> None:
        self.document["features"] = [make_feature(
            "f1", status="in_progress", commands=[]
        )]
        self.write_document()
        code, _, error = self.invoke("verify", "f1")
        self.assertEqual(code, 1)
        self.assertIn("没有可执行的验证命令", error)
        self.assertEqual(self.read_document()["features"][0]["status"], "in_progress")

    def test_22_resume_only_accepts_blocked_with_reason(self) -> None:
        self.document["features"] = [
            make_feature("idle"),
            make_feature("halted", status="blocked", notes=["等待外部依赖"]),
        ]
        self.write_document()
        code, _, error = self.invoke("resume", "idle", "--reason", "还没阻塞过")
        self.assertEqual(code, 1)
        self.assertIn("当前状态不能 resume", error)
        code, _, error = self.invoke("resume", "halted", "--reason", "   ")
        self.assertEqual(code, 1)
        self.assertIn("解除原因不能为空", error)
        self.assertEqual(self.read_document()["features"][1]["status"], "blocked")

    def test_23_resume_records_evidence_and_reapplies_gates(self) -> None:
        self.document["features"] = [
            make_feature("f1", status="blocked", notes="旧阻塞"),
            make_feature("f2", status="in_progress"),
        ]
        self.write_document()
        # WIP 门控同样约束恢复：已有进行中功能时不能把 blocked 项拉起来。
        code, _, error = self.invoke("resume", "f1", "--reason", "外部依赖已就绪")
        self.assertEqual(code, 1)
        self.assertIn("WIP 门控", error)
        self.document["features"][1]["status"] = "not_started"
        self.document["features"][0]["depends_on"] = ["f2"]
        self.write_document()
        # 依赖门控：依赖尚未 passing 时恢复同样被拒绝。
        code, _, error = self.invoke("resume", "f1", "--reason", "外部依赖已就绪")
        self.assertEqual(code, 1)
        self.assertIn("依赖门控", error)
        self.document["features"][1]["status"] = "passing"
        self.document["features"][1]["evidence"] = [{"summary": "依赖已验证"}]
        self.write_document()
        code, _, error = self.invoke("resume", "f1", "--reason", "外部依赖已就绪")
        self.assertEqual(code, 0, error)
        feature = self.read_document()["features"][0]
        self.assertEqual(feature["status"], "in_progress")
        self.assertEqual(feature["evidence"], [])
        self.assertIn("旧阻塞", feature["notes"])
        self.assertIn("解除阻塞：外部依赖已就绪", feature["notes"])

    def test_24_verification_command_must_not_carry_credentials(self) -> None:
        # 清单会提交、命令参数会进 trace，所以在静态校验阶段就拒绝硬编码凭据。
        for leaked in (
            ["{python}", "-c", "print(1)", "--api_key=abc123"],
            ["{python}", "-c", "print(1)", "sk-" + "a" * 24],
        ):
            self.document["features"][0]["verification_commands"] = [leaked]
            errors = harness.validate_document(self.document)
            self.assertTrue(any("疑似含硬编码凭据" in item for item in errors), errors)
        self.document["features"][0]["verification_commands"] = [
            ["{python}", "-c", "print('api_key 由环境变量提供，不写进命令')"]
        ]
        self.assertEqual(harness.validate_document(self.document), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
