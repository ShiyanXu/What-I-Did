# 怎么验证

> 目标：任何一次改动都能在 2 分钟内拿到「能信」的结论。答不上来「这次该跑什么」时看第一节。

## 一、按改动选测试

| 改了哪里 | 必跑 | 建议加跑 |
|---|---|---|
| 任何 `src/*.py` | `check_privacy.py` + `check_boundaries.py`（必须隔离 `WID_HOME`） | — |
| `privacy.py`、`digest.py`、`llm.py`、`config.py` | 同上 + `dev_fixture.py --mock`（空跑全链路） | 用真实某天重出一份报告人工读 |
| `gui.py`、`tray.py`、`i18n*` | 同上 + `gui_regression.py` | `verify_dpi.py` |
| `build.spec`、`scripts/build_dist.py` | 重建 dist + `verify_artifact()` + 双击 exe | `doctor --full`（打包版） |
| 模板 `src/templates/*` | 同上 + 重出一天看 HTML | 检查空数据/缺字段的天 |
| Harness 文件、`scripts/harness.py`、`scripts/check_harness.py` | `scripts/harness.py check` | 全新会话五问测试 |
| 文档 / 注释 / CHANGELOG | 无 | — |

## 二、命令与期望

```bash
PY=C:/Users/<你的用户名>/AppData/Local/Programs/Python/Python310/python.exe

$PY scripts/harness.py check        # 期望：Harness + 隐私 + 隔离 E2E 全 ok（E2E 自带临时 WID_HOME）
$PY scripts/check_privacy.py        # 期望：全部 ok，0 failed；项数以当次输出为准

# 边界自测会清掉并重写样本日的 manual.json、还会写周汇总，必须隔离：
mkdir -p .tmp_wid_verify
WID_HOME="$PWD/.tmp_wid_verify" $PY scripts/dev_fixture.py --day 2026-10-18 --mock
WID_HOME="$PWD/.tmp_wid_verify" $PY scripts/check_boundaries.py   # 期望：全部 ok，0 failed
rm -rf .tmp_wid_verify              # 跑完删掉，别留在仓库里

$PY scripts/verify_dpi.py           # 期望：14 项全 ok
$PY scripts/gui_regression.py       # 期望：全 ok；项数随分支变化（v1.2.1 实测 41）
$PY wid.py doctor --full            # 期望：全部 ok，末尾 summary 无 failed
```

**不设 `WID_HOME` 直接跑 `check_boundaries.py` 会返回 2**：脚本在真实数据目录里会先删样本日的
`manual.json` 再写入合成补录，等于毁掉当天的人工修改。这个门禁是有意加的，不要为了"跑通"去绕过。

测试项数会随分支和条件分支变化，以当次脚本的「通过 / 失败」汇总为准。**`gui_regression` 的项数同样是浮动的**：
脚本里有条件分支，不同环境会走不同分支，静态 `check()` 调用点约 36 个。所以别把它当固定断言，
跑完看「通过 / 失败」两栏即可。

**跑 `gui_regression` 前先想清楚副作用**：它会真开界面、真点击，如果此时 `WhatIDid-Recorder` 在采集，
这些窗口和点击会进当天的活动块，污染日报。要么先 `wid.py pause`，要么在不需要干净数据的机器上跑。

`$PY wid.py doctor --full` 检查的是「能不能跑起来」：解释器、依赖、配置、API Key、OCR 引擎、
数据目录可写、计划任务注册、磁盘空间。**它不验证业务逻辑**，业务逻辑靠前两条。

## 三、各项覆盖什么

- **`check_privacy.py`（不联网、不需样本）**
  断言「图片不出本机」「发送前必脱敏」「密钥/手机号/身份证/邮箱被替换」「分享版不含 block_id/seg_id」
  「隐私策略变更会中断请求」。改任何与数据外发相关的代码，这项失败意味着**不能发布**。
- **`check_boundaries.py`（需要样本日，且必须设 `WID_HOME`）**
  断言输入边界与异常路径：空关键词、日期/天数越界、损坏日报不影响检索与周汇总、空周也能出汇总、
  人工补录幂等与时间补零、改标覆盖生效、分享版不含 `block_id`/`seg_id`。
  它防的是「异常输入把报告写坏，或测试顺手覆盖了真实的人工修改」。
- **`verify_dpi.py`（14 项）**
  高 DPI 下的坐标、字号、图片缩放；用 `dlineinfo()` 打 x 坐标核对左边界，**看截图会误判**。
- **`gui_regression.py`（约 41 项，随分支浮动）**
  四页控件存在性、按钮文案、切语言后控件文本变化。开头会调 `gui._apply_locale()`。
- **`dev_fixture.py --day YYYY-MM-DD --mock`**
  造一天假数据 + 假模型，跑完整链路（不需要 Key、不产生费用），验证「结构没断」。
  `--clean` 删掉样本。

## 四、端到端验证（要真实数据时）

```bash
# 用生产数据、本地源码跑，不覆盖生产报告：把 WID_HOME 指向临时目录
# 前提：手上有一份真实数据（本机在 D:\What-IDid\data\<day>\）；没有真实数据就跳过这一节，
# 用 dev_fixture --mock 造样本即可
mkdir -p .tmp_verify/config .tmp_verify/data
cp /d/What-IDid/config.yaml .tmp_verify/ && cp /d/What-IDid/config/*.txt .tmp_verify/config/ 2>/dev/null
cp -r /d/What-IDid/data/2026-11-04 .tmp_verify/data/
WID_HOME="$PWD/.tmp_verify" $PY wid.py report --date 2026-11-04 --no-open
# 读 .tmp_verify/reports/2026-11-04.json 检查 headline / behaviors / summary_md
rm -rf .tmp_verify
```

要点：**改提示词或判定逻辑时，一定要用真实的一天重跑并人工读一遍**。
结构正确不代表语义正确——1104 那次「把阅读写成写作」就是结构全绿、语义全错。

## 五、常见失败与其含义

| 现象 | 多半是什么 |
|---|---|
| `check_privacy` 报「发送前未脱敏」 | 新增的外发路径绕过了 `privacy.redact_value` |
| `check_boundaries` 报断言失败 | 输入校验、manual 幂等/覆盖或 weekly 空周逻辑被改坏；看输出里具体 label |
| `gui_regression` 报控件文案不符 | 改了文案没同步 `i18n_zh_cn.py`，或又出现了模块级 `t()` |
| `doctor` 报 OCR 不可用 | Umi-OCR 没常驻，且内置模型缺失 |
| 打包版一运行就 `ImportError` | 新增 `src/*.py` 忘了加进 `build.spec:MODULES` |
| 报告 `status=degraded` | 模型调用失败/JSON 不可解析，看 `degraded_reason` 与 `logs/` |
| 心跳不动 | 采集进程死了或被锁挡住，看 `state/` 与计划任务 |

## 六、发布前额外一条

所有者机器上的发布脚本（`publish.py`，不在仓库里）会自己跑前两项，并检查产物里没有 `config.yaml` /
`config/` / `data/` / `reports/` / `state/` / `logs/` / `web/`。**被它拦下时不要绕过，先清理**。
手工打包时照同样的清单自查一遍再上传。
