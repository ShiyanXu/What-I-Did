"""开发用假模型服务：模拟 OpenAI 兼容接口，验证「冻结版能不能真的把整条链路跑完」。

    python .build/fake_llm_server.py            # 监听 127.0.0.1:8799
    # 然后把 config.llm.base_url 指到 http://127.0.0.1:8799/v1

回包里的 block_id / seg_id 全部从请求正文里现抓，保证校验链和真实调用走同一套逻辑。
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BID = re.compile(r"\|\s*(b-\d{8}-\d{4}-[0-9a-f]{4}(?:-\d+)?)\s*\|")
SEG = re.compile(r"^### (seg-\d+)", re.M)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):        # 静默，避免污染被验证命令的输出
        pass

    def do_POST(self):                   # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        user = ""
        for msg in body.get("messages") or []:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user = str(msg.get("content") or "")
        bids = BID.findall(user)
        segs = SEG.findall(user)
        data = {
            "headline": "打包版审核链路自测：假模型回包已接入",
            "opening_md": "这份日报由本地假模型服务生成，用来验证冻结版能否走完"
                          "「构造输入 → 审核预览 → 确认发送 → 校验 → 渲染」整条链路。",
            "summary_md": (
                "**09:12–10:06**（55 分钟）在 report.py 里补幂等判断，"
                "只有覆盖水位往前推才真正出报。\n\n"
                "**10:15–10:58**（44 分钟）查 OCR 断连，改成单线程队列 + 失败冷却。"
            ),
            "behaviors": [
                {"seg_id": sid, "verb": "coding", "label": "写报告链路",
                 "detail": "假模型回包，用于验证冻结版渲染", "confidence": "high"}
                for sid in segs
            ],
            "highlights": [
                {"what": "冻结版完整跑通审核与发送",
                 "evidence": "假模型服务的回包 + 本地校验通过",
                 "impact": "确认打包产物不含 ImportError，链路无缺件",
                 "block_ids": bids[:1]},
            ],
            "friction": [
                {"what": "缺少真实模型 Key 无法端到端验证",
                 "cause": "验证环境不接公网模型",
                 "cost": "约 10 分钟",
                 "fix": "用本地假模型服务模拟 OpenAI 兼容接口",
                 "block_ids": bids[1:2]},
            ],
            "confidence": "medium",
        }
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model") or "mock",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": json.dumps(data, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": 300},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    print(f"假模型服务：http://127.0.0.1:{PORT}/v1/chat/completions", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
