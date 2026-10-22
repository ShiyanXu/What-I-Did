# 安全策略

## 报告问题

如果发现的是**隐私或安全缺陷**（例如数据在预期之外被发送、脱敏规则可被绕过、本地 HTTP 接口可被外部访问），请**不要**开公开 issue，改用仓库页面的 **Security → Advisories → Report a vulnerability** 私下报告，或直接联系维护者。

普通的 bug 与功能建议走公开 issue 即可。

报告时请附上：

- 版本（界面左下角显示的 `v` 号，或 `git rev-parse --short HEAD`）
- 复现步骤，以及能说明问题的**脱敏后**日志片段
- 你的 Windows 版本

**请不要附上真实的 `data\`、`reports\` 或截图**——它们本身就含有敏感内容。用 `scripts\dev_fixture.py` 造的样本即可复现绝大多数问题。

## 已定义的安全边界

这些不是漏洞，是设计上明确接受的行为：

| 行为 | 说明 |
| --- | --- |
| 截图可能包含窗口内敏感内容 | 程序按窗口矩形裁剪，不做像素级识别或遮挡；靠名单与暂停控制 |
| 整屏模式包含副屏与后台窗口 | `all_screens` 必须显式开启 |
| 正则脱敏可能漏识别 | 规则不理解语义，只覆盖已知形态 |
| 已外发内容无法撤回 | 本地删除不影响已发送给模型服务的数据 |
| 分享版不是脱敏保证 | 导出后请自行复核 |
| 构建清单不是签名 | 它用于防止误删，不防篡改 |

## 使用建议

- 把模型 Key 放在环境变量 `WID_LLM_API_KEY` 或 `config\api_key.txt`，不要写进会分享的文件。
- 不要在公开仓库里提交 `data\`、`reports\`、`web\`、`state\`、`logs\`、`config.yaml` 与 `config\api_key.txt`。仓库的 `.gitignore` 已经覆盖这些路径，提交前仍建议 `git status` 复核一遍。
- 涉及密码、支付、私钥的应用加入 `privacy.blocked_apps`；聊天工具放入 `privacy.skip_ocr_apps`。
- 不想外发时，`privacy.allow_llm: false` 是唯一彻底的开关——`report.open_browser: false` 只是不弹浏览器。

## 支持范围

仅维护主分支最新版本。Windows 10/11 x64 之外的平台不在支持范围内。
