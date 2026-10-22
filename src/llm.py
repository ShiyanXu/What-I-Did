"""OpenAI 兼容的极简客户端：一个 POST 就够，不引 SDK。"""
from __future__ import annotations

import copy
import json
import re
import time

import requests

import privacy

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class LLMError(RuntimeError):
    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind = kind          # auth | server | network | bad_json | budget
        self.retryable = retryable


class LLMClient:
    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log
        self.base_url = (cfg.get("llm.base_url", "") or "").rstrip("/")
        self.model = cfg.get("llm.model", "deepseek-chat")
        self.timeout = float(cfg.get("llm.timeout_sec", 180))
        self.max_retries = int(cfg.get("llm.max_retries", 3))
        self.temperature = float(cfg.get("llm.temperature", 0.2))
        self.api_key = cfg.api_key()
        self.privacy_policy = copy.deepcopy(cfg.get("privacy", {}))
        # 单份日报的调用数上限（含修复轮）：真正执行，防止费用失控
        self.max_calls = max(1, int(cfg.get("llm.max_calls_per_report", 12)))
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def configured(self) -> bool:
        cfg = privacy.current_config(self.cfg)
        return bool(not getattr(cfg, "error", "") and cfg.get("privacy.allow_llm", True)
                    and self.api_key and self.base_url)

    def _private_payload(self, payload: dict) -> dict:
        """在每一次实际 HTTP 请求前执行；重试也不能越过新保存的隐私设置。"""
        cfg = privacy.current_config(self.cfg)
        try:
            privacy.require_valid(cfg)
            if not cfg.get("privacy.allow_llm", True):
                raise privacy.PrivacyError("已禁止模型外发，仅可生成本地时间线")
            if cfg.get("privacy", {}) != self.privacy_policy:
                raise privacy.PrivacyError("隐私策略已变化，请按新策略重新生成摘要")
            if (str(cfg.get("llm.base_url", "") or "").rstrip("/") != self.base_url
                    or cfg.get("llm.model", "deepseek-chat") != self.model):
                raise privacy.PrivacyError("模型配置已变化，请使用新设置重新生成")
            result = dict(payload)
            result["messages"] = privacy.redact_value(
                payload.get("messages", []), cfg, extra_secrets=(self.api_key, cfg.api_key()))
            return result
        except privacy.PrivacyError as exc:
            raise LLMError("privacy", str(exc), retryable=False) from exc

    def _post(self, payload: dict) -> dict:
        payload = self._private_payload(payload)
        sess = requests.Session()
        # 访问公网模型服务要跟随系统代理，这里与"本机请求"相反，显式保留默认行为
        try:
            r = sess.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as exc:
            raise LLMError("network", f"请求超时 {self.timeout:.0f}s", retryable=True) from None
        except requests.exceptions.RequestException as exc:
            # 异常和响应正文可能回显 URL、请求或凭据，不写入日志/日报。
            raise LLMError("network", "网络请求失败，请检查连接、代理和模型地址", retryable=True) from None
        finally:
            sess.close()

        if r.status_code == 401:
            raise LLMError("auth", "API Key 无效或已过期（401）", retryable=False)
        if r.status_code == 402:
            raise LLMError("budget", "账户余额不足（402）", retryable=False)
        if r.status_code == 429:
            raise LLMError("server", "限流（429）", retryable=True)
        if r.status_code >= 500:
            raise LLMError("server", f"服务端错误 {r.status_code}", retryable=True)
        if r.status_code != 200:
            raise LLMError("server", f"HTTP {r.status_code}（响应正文未记录；不跟随重定向）", retryable=False)
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError("server", "响应不是 JSON（响应正文未记录）", retryable=True) from None

    def chat_json(self, system: str, user: str, max_tokens: int = 6000) -> dict:
        cfg = privacy.current_config(self.cfg)
        try:
            privacy.require_valid(cfg)
        except privacy.PrivacyError as exc:
            raise LLMError("privacy", str(exc)) from exc
        if not cfg.get("privacy.allow_llm", True):
            raise LLMError("privacy", "已禁止模型外发，仅可生成本地时间线")
        if not self.configured():
            raise LLMError("auth", "未配置 API Key", retryable=False)
        if self.calls >= self.max_calls:
            raise LLMError("budget", f"已达单份日报调用上限（{self.max_calls} 次）", retryable=False)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        last: LLMError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._post(payload)
                self.calls += 1
                usage = resp.get("usage") or {}
                self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
                content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
                self._private_payload(payload)  # 在途请求无法撤回，但策略改变后不再交付旧结果。
                return privacy.redact_value(self._parse_json(content), cfg, extra_secrets=(self.api_key,))
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                wait = min(20, 2 ** attempt)
                if self.log:
                    self.log.warning("模型调用失败（%s），%ds 后第 %d 次重试", exc.kind, wait, attempt + 1)
                time.sleep(wait)
        raise last or LLMError("server", "未知错误")

    @staticmethod
    def _parse_json(content: str) -> dict:
        text = (content or "").strip()
        text = _FENCE.sub("", text).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError("bad_json", f"JSON 解析失败: {exc}") from exc
            else:
                raise LLMError("bad_json", "响应里找不到 JSON")
        if not isinstance(data, dict):
            raise LLMError("bad_json", "顶层不是对象")
        return data
