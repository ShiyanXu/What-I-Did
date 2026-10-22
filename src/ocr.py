"""本地 OCR：Umi-OCR 常驻实例 + HTTP 接口为主，CLI 为备，最后降级为"只记标题"。

实测要点（2026-10 于本机验证）
- Umi-OCR v2.1.4 需要先 `--hide` 拉起常驻实例，HTTP 服务（默认 127.0.0.1:1224）才可用；
- CLI 的 `--path` 本质是通过 HTTP 把指令转发给常驻实例，实例不在时会失败，
  且失败信息会被写进 `--output` 文件——必须识别并当失败处理；
- `/api/ocr` 返回的 `data` 是 [{box, score, text, end}, ...]，不是纯字符串；
- 整屏 3840×1080 识别质量明显下降，必须裁活动窗口按原生分辨率识别。
"""
from __future__ import annotations

import base64
import copy
import json
import queue
import re
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import paths
import privacy

CREATE_NO_WINDOW = 0x08000000
ERROR_SIGNATURES = (
    "Umi-OCR 已在运行",
    "跨进程传输指令失败",
    "cross process transmission",
    "urlopen error",
    "Traceback (most recent call last)",
)


class OcrError(RuntimeError):
    pass


@dataclass
class OcrResult:
    id: str
    status: str = "ok"          # ok | empty | failed | skipped
    text: str = ""
    ms: int = 0
    engine: str = ""
    error: str = ""


def _parse_payload(payload) -> str:
    """兼容 Umi 三种返回：结构化列表 / 纯字符串 / 嵌套 dict。"""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("data", "text", "result"):
            if key in payload:
                return _parse_payload(payload[key])
        return ""
    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            if isinstance(item, dict):
                t = str(item.get("text", ""))
                end = str(item.get("end", "") or "")
                parts.append(t + ("\n" if end in ("\n", "\\n") else " " if end == " " else ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(payload).strip()


def _clean(text: str) -> str:
    if not text:
        return ""
    lines, seen = [], set()
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def _looks_like_error(text: str) -> bool:
    head = (text or "")[:400]
    return any(sig in head for sig in ERROR_SIGNATURES)


class BaseEngine:
    name = "base"
    scheme = "none"

    def available(self, timeout: float = 1.5) -> bool:
        return False

    def recognize(self, img: Path, timeout: float) -> str:
        raise NotImplementedError


class UmiHttpEngine(BaseEngine):
    name = "umi_http"

    def __init__(self, url: str = "http://127.0.0.1:1224/api/ocr", timeout: float = 60,
                 launcher: Callable[[], bool] | None = None):
        self.url = privacy.require_local_ocr_url(url)
        self.timeout = timeout
        self.launcher = launcher
        try:
            from urllib.parse import urlparse

            p = urlparse(self.url)
            self.host = p.hostname or "127.0.0.1"
            self.port = p.port if p.port is not None else (443 if p.scheme == "https" else 80)
        except Exception:
            self.host, self.port = "127.0.0.1", 1224

    def available(self, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _post(self, img: Path, timeout: float) -> str:
        import requests

        privacy.require_local_ocr_url(self.url)
        data = base64.b64encode(img.read_bytes()).decode("ascii")
        sess = requests.Session()
        sess.trust_env = False  # 绝不能被系统代理劫持
        try:
            r = sess.post(self.url, json={"base64": data}, timeout=timeout or self.timeout,
                          allow_redirects=False)
        finally:
            sess.close()
        if r.status_code != 200:
            raise OcrError(f"OCR HTTP {r.status_code}（不跟随重定向，响应正文未记录）")
        try:
            payload = r.json()
        except ValueError as exc:
            raise OcrError("OCR 响应不是 JSON（响应正文未记录）") from exc
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (100, 0, None):
                raise OcrError("Umi-OCR 返回失败状态（响应正文未记录）")
        return _clean(_parse_payload(payload))

    def recognize(self, img: Path, timeout: float = 0) -> str:
        import requests

        last: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._post(img, timeout)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, OSError) as exc:
                last = exc
                if attempt == 1 and self.launcher and self.launcher():
                    time.sleep(0.8)
                    continue
                break
        raise OcrError(f"OCR HTTP 不可用（{type(last).__name__}，请求细节未记录）")


class UmiCliEngine(BaseEngine):
    name = "umi_cli"

    def __init__(self, exe: str, tmp_dir: Path, timeout: float = 90,
                 launcher: Callable[[], bool] | None = None):
        self.exe = exe
        self.tmp_dir = tmp_dir
        self.timeout = timeout
        self.launcher = launcher

    def available(self, timeout: float = 1.5) -> bool:
        return bool(self.exe) and Path(self.exe).exists()

    def recognize(self, img: Path, timeout: float = 0) -> str:
        if not self.available():
            raise OcrError(f"umi_exe 不存在: {self.exe}")
        if self.launcher:
            self.launcher()  # 确保常驻实例在，否则 --path 必然失败
        timeout = timeout or self.timeout
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        out = self.tmp_dir / f"umi_{uuid.uuid4().hex}.txt"
        try:
            subprocess.run(
                [self.exe, "--path", str(img), "--output", str(out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            if not out.exists():
                raise OcrError("未生成输出文件")
            text = _clean(out.read_text(encoding="utf-8", errors="ignore"))
            if _looks_like_error(text):
                raise OcrError("Umi 返回错误文本（正文未记录）")
            return text
        except subprocess.TimeoutExpired as exc:
            raise OcrError(f"识别超时 {timeout}s") from exc
        finally:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass


class NullEngine(BaseEngine):
    name = "none"

    def available(self, timeout: float = 1.5) -> bool:
        return True

    def recognize(self, img: Path, timeout: float = 0) -> str:
        return ""


_NOISE_RE = re.compile(r"^[\x00-\x7F\W_]+$")


def _is_noise(line: str) -> bool:
    """滤掉 UI 图标误识别出来的纯符号行（× / ← / 口 之类）。

    中文按 \\w 处理，所以这个正则只吃纯 ASCII 符号；单字母/数字保留，
    因为它们可能是代码变量名，宁可留给模型判断。
    """
    s = line.strip()
    if not s:
        return True
    if len(s) <= 2 and _NOISE_RE.match(s):
        return True
    return False


def _quiet_rapidocr() -> None:
    """把 RapidOCR 自己的 INFO 日志压到 WARNING。

    它默认往 stderr 打模型路径、引擎名，在控制台会盖住我们自己的输出；
    而且构造 RapidOCR() 时会重新配回 INFO，所以调用前后各需要来一次。
    """
    import logging as _logging

    for name in ("RapidOCR", "rapidocr", "RapidOCR.utils", "rapidocr.utils"):
        _logging.getLogger(name).setLevel(_logging.WARNING)
    try:
        from rapidocr.utils.log import logger as _rlog

        _rlog.setLevel(_logging.WARNING)
    except Exception:
        pass


class RapidEngine(BaseEngine):
    """内置 OCR：RapidOCR（PaddleOCR 模型的 ONNX 版），模型随程序分发，离线可用。

    实测（Win10 / 1600×1000 屏图）：约 5.3s，250 行，URL、中文标题、代码片段均可读。
    比 Umi-OCR 慢，但不需要任何外部程序——这是"拷到任意机器就能跑"的保底引擎。
    """

    name = "rapidocr"

    def __init__(self, max_side: int = 1600, min_score: float = 0.5):
        self.max_side = max_side
        self.min_score = min_score
        self._engine = None
        self._lock = threading.Lock()
        self._busy = threading.Event()

    def available(self, timeout: float = 1.5) -> bool:
        try:
            import rapidocr  # noqa: F401
        except Exception:
            return False
        return True

    def _instance(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    _quiet_rapidocr()
                    from rapidocr import RapidOCR

                    params = {}
                    if self.max_side:
                        # 限制检测边长：屏图 1600 宽足够看清正文，再大会线性变慢
                        params = {"Det.limit_side_len": int(self.max_side),
                                  "Det.limit_type": "max"}
                    try:
                        self._engine = RapidOCR(params=params) if params else RapidOCR()
                    except TypeError:
                        self._engine = RapidOCR()
                    # 构造过程会把 logger 重新配回 INFO，所以要在之后再来一次
                    _quiet_rapidocr()
        return self._engine

    def recognize(self, img: Path, timeout: float = 0) -> str:
        """识别并执行超时上限：底层 ONNX 调用无法中断，改为后台线程 + 放弃等待。

        超时的线程仍在后台跑（daemon），用 _busy 标记挡住后续提交，
        防止卡死的调用越积越多。
        """
        eng = self._instance()
        timeout = float(timeout or 0)
        if self._busy.is_set():
            raise OcrError("上一次识别仍未返回，暂不接收新任务")
        if timeout <= 0:
            self._busy.set()
            try:
                return self._recognize_inner(eng, img)
            finally:
                self._busy.clear()
        holder: dict = {}

        def work() -> None:
            try:
                holder["out"] = self._recognize_inner(eng, img)
            except Exception as exc:  # noqa: BLE001 —— 原样交回调用方
                holder["err"] = exc
            finally:
                self._busy.clear()

        self._busy.set()
        t = threading.Thread(target=work, name="rapidocr-call", daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # 线程还在跑：不清 _busy，让后续任务直接失败，直到它自然返回
            raise OcrError(f"识别超时 {timeout:.0f}s，已放弃等待")
        if "err" in holder:
            raise holder["err"]
        return holder.get("out", "")

    def _recognize_inner(self, eng, img: Path) -> str:
        out = eng(str(img))
        txts = getattr(out, "txts", None)
        scores = getattr(out, "scores", None)
        if txts is None and isinstance(out, tuple):   # 兼容 v2 的 (rows, elapse)
            rows = out[0] or []
            txts = [r[1] for r in rows if len(r) > 1]
            scores = [r[2] for r in rows if len(r) > 2]
        kept: list[str] = []
        for i, t in enumerate(txts or []):
            text = str(t).strip()
            if not text or _is_noise(text):
                continue
            if scores is not None and i < len(scores):
                try:
                    if float(scores[i]) < self.min_score:
                        continue
                except (TypeError, ValueError):
                    pass
            kept.append(text)
        return _clean("\n".join(kept))


class InstanceManager:
    """负责拉起/复活 Umi-OCR 常驻实例，带防抖动。"""

    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log
        self.exe = cfg.get("ocr.umi_exe", "") or ""
        self._last_spawn = 0.0
        self._lock = threading.Lock()
        self.spawn_count = 0

    def port_open(self, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 1224), timeout=timeout):
                return True
        except OSError:
            return False

    def ensure(self, wait: float = 25.0) -> bool:
        if self.port_open(0.6):
            return True
        if not self.exe or not Path(self.exe).exists():
            return False
        with self._lock:
            now = time.time()
            if now - self._last_spawn < 20:  # 20s 内不重复拉进程
                return False
            self._last_spawn = now
            try:
                subprocess.Popen(
                    [self.exe, "--hide"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
                self.spawn_count += 1
                if self.log:
                    self.log.info("拉起 Umi-OCR 常驻实例（第 %d 次）", self.spawn_count)
            except Exception as exc:
                if self.log:
                    self.log.warning("拉起 Umi-OCR 失败: %s", exc)
                return False
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.port_open(0.5):
                return True
            time.sleep(0.5)
        if self.log:
            self.log.warning("Umi-OCR 在 %.0fs 内未就绪", wait)
        return False


@dataclass
class _Task:
    ocr_id: str
    day: str
    img: Path
    app: str
    title: str = ""
    policy: dict = field(default_factory=dict)
    ocr_policy: dict = field(default_factory=dict)


@dataclass
class OcrStats:
    ok: int = 0
    empty: int = 0
    failed: int = 0
    skipped: int = 0
    by_engine: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.ok + self.empty + self.failed + self.skipped

    @property
    def success_rate(self) -> float:
        done = self.ok + self.empty + self.failed
        return (self.ok / done) if done else 0.0


class OcrQueue:
    """单线程 OCR 队列：submit 立即返回，结果异步追加到 ocr.jsonl。"""

    MAX_DEPTH = 64

    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.log = logger
        self.manager = InstanceManager(cfg, logger)
        self._q: queue.Queue[_Task | None] = queue.Queue(maxsize=self.MAX_DEPTH)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self.stats = OcrStats()
        self._recent: list[float] = []
        self._day_counts: dict[str, int] = {}
        self._done_ids: set[str] = set()
        self._consecutive_fail = 0
        self._cooldown_until = 0.0
        self._engine_idx = 0
        self._recover_interval = max(60.0, float(cfg.get("ocr.recover_probe_sec", 600)))
        self._last_recover_probe = 0.0
        self._task_started_at = 0.0
        self._watchdog_thread: threading.Thread | None = None
        self._engines = self._build_engines()
        self._load_done_ids()

    def _build_engines(self) -> list[BaseEngine]:
        if getattr(self.cfg, "error", ""):
            return [NullEngine()]
        launcher = lambda: self.manager.ensure()  # noqa: E731
        cli = UmiCliEngine(self.cfg.get("ocr.umi_exe", "") or "", paths.OCR_TMP,
                           float(self.cfg.get("ocr.timeout_sec", 90)), launcher)
        http = UmiHttpEngine(self.cfg.get("ocr.http_url", ""),
                             float(self.cfg.get("ocr.timeout_sec", 90)), launcher)
        rapid = RapidEngine(
            int(self.cfg.get("ocr.rapidocr.max_side", 1600) or 0),
            float(self.cfg.get("ocr.rapidocr.min_score", 0.5) or 0.5),
        )
        null = NullEngine()
        mode = (self.cfg.get("ocr.engine", "auto") or "auto").lower()
        if mode == "none":
            return [null]
        if mode == "rapidocr":
            return [rapid, null]
        if mode == "umi_cli":
            return [cli, http, rapid, null]
        if mode == "umi_http":
            return [http, cli, rapid, null]
        # auto：本机有 Umi 就优先用它（更快），否则直接走内置引擎，
        # 免得在没装 Umi 的机器上先白等三次连接失败再降级。
        has_umi = cli.available() or http.available(0.5)
        if has_umi:
            return [http, cli, rapid, null]
        return [rapid, null]

    def current_engine(self) -> BaseEngine:
        return self._engines[self._engine_idx % len(self._engines)]

    def _next_engine(self) -> None:
        self._engine_idx = (self._engine_idx + 1) % len(self._engines)
        if self.log:
            self.log.warning("OCR 切换到引擎: %s", self.current_engine().name)

    def _load_done_ids(self) -> None:
        if not paths.DATA.exists():
            return
        for p in paths.DATA.glob("*/ocr.jsonl"):
            day = p.parent.name
            try:
                with p.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("id"):
                            self._done_ids.add(rec["id"])
                        if rec.get("status") == "ok":
                            self._day_counts[day] = self._day_counts.get(day, 0) + 1
            except OSError:
                continue

    def warmup(self) -> None:
        if getattr(privacy.current_config(self.cfg), "error", ""):
            return
        if not self.cfg.get("ocr.warmup_on_start", True):
            return
        if (self.cfg.get("ocr.engine", "auto") or "auto").lower() == "none":
            return
        # 只对 Umi 预热（拉起常驻实例）；内置引擎首次识别时自然加载，无需预热
        self.manager.ensure()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._start_worker()
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(target=self._watchdog, name="ocr-watchdog",
                                                     daemon=True)
            self._watchdog_thread.start()

    def _start_worker(self) -> None:
        self._task_started_at = 0.0
        self._thread = threading.Thread(target=self._run, name="ocr-worker", daemon=True)
        self._thread.start()

    def _watchdog(self) -> None:
        """OCR 工作线程卡死自愈：单任务超过 2×timeout+60s 就重启 worker。

        卡死的旧线程多半悬在 ONNX 原生调用里，无法真正杀死；
        重启的只是取任务的循环，旧线程返回后写入的结果仍受 _write_lock 保护。
        """
        limit = float(self.cfg.get("ocr.timeout_sec", 90)) * 2 + 60
        while not self._stop.wait(30):
            started = self._task_started_at
            if started and time.time() - started > limit:
                if self.log:
                    self.log.error("OCR 看门狗：单任务超 %.0fs 未返回，重启工作线程", limit)
                self._start_worker()

    def stop(self, timeout: float = 60) -> None:
        """先让 worker 把队列里已提交的任务做完（哨兵排在队尾），再退出。"""
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=timeout)
        self._stop.set()

    def can_accept(self, day: str, now_ts: float | None = None) -> tuple[bool, str]:
        now_ts = now_ts or time.time()
        if self._day_counts.get(day, 0) >= int(self.cfg.get("ocr.max_per_day", 1200)):
            return False, "日配额已满"
        self._recent = [t for t in self._recent if now_ts - t < 60]
        if len(self._recent) >= int(self.cfg.get("ocr.max_per_minute", 6)):
            return False, "分钟配额已满"
        return True, ""

    def submit(self, ocr_id: str, day: str, img: Path, app: str = "", title: str = "", *,
               capture_cfg=None) -> bool:
        if ocr_id in self._done_ids or self._q.full():
            return False
        cfg = privacy.current_config(self.cfg)
        if cfg.get("ocr", {}) != self.cfg.get("ocr", {}):
            return False  # 引擎实例仍是启动时的配置；修改引擎后需重启采集。
        source_cfg = capture_cfg if capture_cfg is not None else cfg
        task = _Task(ocr_id, day, img, app, title, copy.deepcopy(source_cfg.get("privacy", {})),
                 copy.deepcopy(source_cfg.get("ocr", {})))
        if not self._task_allowed(task, cfg):
            return False
        try:
            self._q.put_nowait(task)
            self._recent.append(time.time())
            self._done_ids.add(ocr_id)
            return True
        except queue.Full:
            return False

    def pending(self) -> int:
        return self._q.qsize()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if task is None:
                break
            self._task_started_at = time.time()
            try:
                self._process(task)
            except Exception as exc:
                if self.log:
                    self.log.error("OCR 任务异常（%s，未记录原始异常正文）", type(exc).__name__)
            finally:
                self._task_started_at = 0.0
                self._cleanup(task)

    @staticmethod
    def _cleanup(task: _Task) -> None:
        """删掉为 OCR 临时裁出来的原生分辨率图，避免状态目录膨胀。"""
        try:
            p = task.img
            if p.parent == paths.OCR_TMP and p.suffix.lower() == ".png":
                p.unlink(missing_ok=True)
        except OSError:
            pass

    def _try_recover(self) -> None:
        """降级/冷却期间定期重探测更靠前的引擎：Umi 恢复了就切回去。

        NullEngine 是链尾兜底，一旦落到它上面，没有这条恢复路径就会
        一直"只记标题"直到重启采集。
        """
        now = time.time()
        if now - self._last_recover_probe < self._recover_interval:
            return
        self._last_recover_probe = now
        if self._engine_idx == 0 and now >= self._cooldown_until:
            return  # 首选引擎在岗且不在冷却，无需探测
        for i in range(self._engine_idx):
            eng = self._engines[i]
            try:
                if eng.available(1.0):
                    self._engine_idx = i
                    self._consecutive_fail = 0
                    self._cooldown_until = 0.0
                    if self.log:
                        self.log.info("OCR 引擎恢复：%s 重新可用，已切回", eng.name)
                    return
            except Exception:
                continue

    def _process(self, task: _Task) -> None:
        start = time.time()
        cfg = privacy.current_config(self.cfg)
        if not self._task_allowed(task, cfg):
            self._record(task, OcrResult(task.ocr_id, "skipped", engine="privacy", error="隐私策略禁止识别"))
            return
        self._try_recover()
        if time.time() < self._cooldown_until:
            self._record(task, OcrResult(task.ocr_id, "skipped", engine="cooldown", error="引擎冷却中"))
            return
        engine = self.current_engine()
        if isinstance(engine, NullEngine):
            self._record(task, OcrResult(task.ocr_id, "skipped", engine="none", error="OCR 未启用"))
            return
        result = OcrResult(task.ocr_id, engine=engine.name)
        try:
            text = engine.recognize(task.img, float(self.cfg.get("ocr.timeout_sec", 90)))
            result.text = self._trim(privacy.redact(text, cfg))
            result.status = "ok" if result.text else "empty"
            self._consecutive_fail = 0
        except Exception as exc:
            result.status = "failed"
            result.error = f"{engine.name} 识别失败（{type(exc).__name__}，原始错误正文未记录）"
            self._consecutive_fail += 1
            if self._consecutive_fail >= 3:
                self._next_engine()
                self._consecutive_fail = 0
                # 换引擎后不冷却：新引擎可能是好的，让它立刻接手；
                # 只有当整条链都失败退化到 NullEngine 时才长时间冷却，避免空转烧 CPU。
                if isinstance(self.current_engine(), NullEngine):
                    self._cooldown_until = time.time() + 600
        result.ms = int((time.time() - start) * 1000)
        if result.status == "ok":
            self._day_counts[task.day] = self._day_counts.get(task.day, 0) + 1
        self._record(task, result)

    def _trim(self, text: str) -> str:
        limit = int(self.cfg.get("ocr.max_chars_per_shot", 6000))
        if len(text) > limit:
            return text[:limit] + "\n…（截断）"
        return text

    def _record(self, task: _Task, result: OcrResult) -> None:
        cfg = privacy.current_config(self.cfg)
        app = ""
        if not self._task_allowed(task, cfg):
            result = OcrResult(task.ocr_id, "skipped", ms=result.ms, engine="privacy",
                               error="识别期间隐私状态变化，正文未保存")
        else:
            result.text = privacy.redact(result.text, cfg)
            result.error = privacy.redact(result.error, cfg)
            app = privacy.redact(task.app, cfg)
        rec = asdict(result)
        rec.update(ts=time.time(), day=task.day, app=app)
        path = paths.ocr_path(task.day)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self._write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        if result.status == "ok":
            self.stats.ok += 1
        elif result.status == "empty":
            self.stats.empty += 1
        elif result.status == "failed":
            self.stats.failed += 1
        else:
            self.stats.skipped += 1
        key = result.engine or "?"
        self.stats.by_engine[key] = self.stats.by_engine.get(key, 0) + 1
        if self.log and result.status == "failed":
            self.log.warning("OCR 失败 %s: %s", task.ocr_id, result.error)

    @staticmethod
    def _task_allowed(task: _Task, cfg) -> bool:
        try:
            privacy.require_valid(cfg)
            if task.policy != cfg.get("privacy", {}):
                return False
            if task.ocr_policy != cfg.get("ocr", {}) or cfg.get("ocr.engine", "auto") == "none":
                return False
            if privacy.is_paused() or privacy.is_blocked_app(task.app, cfg):
                return False
            if privacy.is_blocked_title(task.title, cfg) or privacy.should_skip_ocr(task.app, cfg):
                return False
            import winapi

            return not winapi.is_locked()
        except privacy.PrivacyError:
            return False


# ---------------- 供 doctor / ocr-test 使用 ----------------

def probe(cfg) -> dict:
    cfg = privacy.current_config(cfg)
    privacy.require_valid(cfg)
    exe = cfg.get("ocr.umi_exe", "") or ""
    http = UmiHttpEngine(cfg.get("ocr.http_url", ""))
    rapid = RapidEngine()
    return {
        "mode": cfg.get("ocr.engine", "auto"),
        "umi_exe": exe,
        "umi_exe_exists": bool(exe) and Path(exe).exists(),
        "umi_port": http.port,
        "umi_open": http.available(1.2),
        "rapidocr_ready": rapid.available(),
        "build": None,
    }


def recognize_once(cfg, img: Path, timeout: float = 0, allow_launch: bool = True) -> dict:
    """一次性识别，用于 ocr-test / doctor。"""
    import winapi

    cfg = privacy.current_config(cfg)
    privacy.require_valid(cfg)
    policy = copy.deepcopy(cfg.get("privacy", {}))
    ocr_policy = copy.deepcopy(cfg.get("ocr", {}))

    def guard():
        current = privacy.current_config(cfg)
        privacy.require_valid(current)
        if (privacy.is_paused() or winapi.is_locked()
                or current.get("privacy", {}) != policy or current.get("ocr", {}) != ocr_policy):
            raise privacy.PrivacyError("暂停、锁屏或识别策略已变化，已停止识别并丢弃结果")
        return current

    guard()  # 手选图片同样不能绕过暂停、锁屏或无效配置。
    timeout = timeout or float(cfg.get("ocr.timeout_sec", 90))
    manager = InstanceManager(cfg)
    launcher = (lambda: manager.ensure()) if allow_launch else None
    cli = UmiCliEngine(cfg.get("ocr.umi_exe", "") or "", paths.OCR_TMP, timeout, launcher)
    http = UmiHttpEngine(cfg.get("ocr.http_url", ""), timeout, launcher)
    rapid = RapidEngine(
        int(cfg.get("ocr.rapidocr.max_side", 1600) or 0),
        float(cfg.get("ocr.rapidocr.min_score", 0.5) or 0.5),
    )
    mode = (cfg.get("ocr.engine", "auto") or "auto").lower()
    if mode == "none":
        return {"engine": "", "status": "failed", "text": "", "ms": 0, "error": "OCR 已关闭"}
    if mode == "rapidocr":
        engines: list[BaseEngine] = [rapid]
    elif mode == "umi_cli":
        engines = [cli, http, rapid]
    elif mode == "umi_http":
        engines = [http, cli, rapid]
    else:
        has_umi = cli.available() or http.available(1.0)
        engines = [http, cli, rapid] if has_umi else [rapid]
    if allow_launch and engines and isinstance(engines[0], (UmiHttpEngine, UmiCliEngine)):
        manager.ensure()  # 先确保常驻实例在，避免第一次 POST 白等
    errors: list[str] = []
    for eng in engines:
        guard()
        if isinstance(eng, UmiCliEngine) and not eng.available():
            errors.append(f"{eng.name}: exe 不存在")
            continue
        if isinstance(eng, RapidEngine) and not eng.available():
            errors.append("rapidocr: 未安装")
            continue
        t0 = time.time()
        try:
            text = eng.recognize(img, timeout)
            text = privacy.redact(text, guard())
            return {"engine": eng.name, "status": "ok" if text else "empty", "text": text,
                    "ms": int((time.time() - t0) * 1000), "error": ""}
        except privacy.PrivacyError:
            raise
        except Exception as exc:
            errors.append(f"{eng.name}: 识别失败（{type(exc).__name__}，原始错误正文未记录）")
    return {"engine": "", "status": "failed", "text": "", "ms": 0,
            "error": " | ".join(errors) or "未配置可用 OCR 引擎"}


def recognize_screen(cfg) -> dict:
    """唯一的屏幕诊断入口：同一隐私规则、唯一临时文件、结束即删除。"""
    import capture
    import winapi

    cfg = privacy.current_config(cfg)
    privacy.require_valid(cfg)
    if cfg.get("ocr.engine", "auto") == "none":
        raise privacy.PrivacyError("OCR 已关闭，未采集诊断截图")
    image, fg = capture.diagnostic_image(cfg)
    paths.OCR_TMP.mkdir(parents=True, exist_ok=True)
    tmp = paths.OCR_TMP / f"diagnostic_{uuid.uuid4().hex}.png"
    try:
        image.save(tmp, "PNG")
        result = recognize_once(cfg, tmp)
        latest = privacy.current_config(cfg)
        if (privacy.capture_reason(latest, fg, 0, winapi.is_locked(), force=True)
                or privacy.should_skip_ocr(fg.get("app", ""), latest)
                or cfg.get("privacy", {}) != latest.get("privacy", {})
                or cfg.get("ocr", {}) != latest.get("ocr", {})):
            raise privacy.PrivacyError("识别期间隐私状态变化，已丢弃结果")
        return result
    finally:
        tmp.unlink(missing_ok=True)
