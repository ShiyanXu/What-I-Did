# PROGRESS.md — What-IDid

> **这是唯一的进度事实来源。** 新会话从这里接续，不要凭记忆或聊天记录猜。
> 维护规则：每次会话结束前更新本文件；只写事实与下一步，不写过程感想。
> 最后更新：2026-11-06 14:40

## 当前状态

> **这一节刻意不写当前 HEAD。** HEAD 每次提交都会变，把它写进一个会被提交的文件，等于让它
> 一经提交就过期——本项目已经因此错过三次。当前值一律用下面自查第 1、2 条现查；
> 只有**已经不动了的**提交号（过去的提交、tag 目标）才写成字面量。

- **版本**：`1.4.0`（`src/gui.py:VERSION`，唯一来源）——**已构建、未打 tag**
- **仓库**：HEAD 已推送、与 `origin/main` 一致。**工作区是否干净不写在这里**——本文件与改动一起提交，
  写死「干净」立刻过期；一律现跑 `git status --short` 判断，把它的输出当唯一依据。
- **tag**：`v1.2.2`（`85b0c67`）→ `1a6d251`、`v1.3.0`（`0f89c19`）→ `753084c`、`v1.3.1`（`4e4c67d`）
  → `092b7c4`，三个附注标签都已推送。tag 只标记**产物来源**，不标记最后的文档状态：
  `v1.3.0` 之后还有 CHANGELOG 与状态修正（`764d2c2`），这些**不在 tag 内**——有意的取舍，不是遗漏。
- **v1.3.1 状态**：**已发布**。代码入库 `092b7c4`、tag 同名、GitHub Release 2026-11-06 14:10 发布；
  产物在 `release/v1.3.1/`，生产实例当前跑的就是这一版。
- **v1.4.0 状态**：**范围收窄版，已构建、待发布**。内容只有一件事：把来源链接（R1）与知识卡（R2）
  彻底移出产品（依据 `agents/decisions.md` D-19），另修一个深层配置键缩进 bug。打 tag 与 Release 待做。
- **已发布**：GitHub Release `v1.3.1`（<https://github.com/ShiyanXu/What-I-Did/releases/tag/v1.3.1>），
  资产 `What-IDid-win64-1.3.1-20261106.zip`（116,985,077 字节 / 1106 条目 / sha256 `aa85acf6…1d05`，
  与 `release/v1.3.1/SHA256SUMS.txt` 一致；GitHub 侧 `digest` 字段与本地逐位相同）。
  上一版 `v1.3.0` 的资产是 `What-IDid-win64-1.3.0-20261106.zip`（116,983,623 字节 / `297b73b1…95c2`）
- **自测**：`scripts/harness.py check` 全绿（2026-11-06 14:55 实跑，`exit_code=0`）——
  它一次跑完静态校验、Harness 自测、隐私检查、隔离 E2E；**项数随分支浮动，以命令输出为准，
  本文件不写死数字**。
  1.4.0 裁剪后重跑：`verify_dpi.py` 14/14、`gui_regression.py` 41/41（2026-11-06 14:50 实跑，
  用 `seed_gui_home.py` 新建的隔离目录，跑完即删）；
  `harness.py status` 当前 5/5 `passing`、`blocked` / `in_progress` 归零（`source-links-001` 已删除）。
- **文档**：`AGENTS.md`、本文件、`agents/` 下专题首次入库于 `888c30f`；两轮「克隆后五问」纠偏在
  `227935c`；`07fe7f3` 把仓库地图移出入口文件（新建 `agents/layout.md`，入口从 127 行降到 97 行）
- **生产实例**：`D:\What-IDid` 已换 v1.3.1（exe md5 `5ae8148c…`、`_internal` 1104 文件、
  `doctor --full` 18 项全绿），历史数据原地保留；采集由计划任务 `WhatIDid-Recorder` 拉起，心跳正常
- **已核对的元信息**：`release/v1.2.2/RELEASE_NOTES.md` 与 GitHub Release 正文的 `Source commit`
  都是 `1a6d251`（tag 实际目标），两侧已对齐。本文件此前记成 `1e4a5ed`——那是产物生成时的 dev HEAD，
  Release 正文在 2026-11-05 16:33 已改好，只是本文件没跟上；**属本文件的过期断言，不是发布问题**。
  改这类断言前先跑 `git rev-parse v1.2.2^{commit}` 与接口查正文，别沿用上一轮结论。
  - `release/v1.3.0/RELEASE_NOTES.md` 是**手工修正**的：`Source commit` 由 `883320c` 改成 `753084c`，
    `Self-tests` 行改为 harness 口径，Notes 各补两条（旧 `config.yaml` 照旧可用、绿色版已内置 SQLite）。
    手工的原因当时是「不改 CHANGELOG、不动 tag」；后来 CHANGELOG 已在 tag 之后补上同样两条，
    所以现在重跑 `publish --force` 生成的正文与这份一致，只有 `Built` 时间与 `Source commit`
    （届时取当时的 HEAD）会不同——重跑前先想清楚要不要这两个值变。

**自查（不要只信上面的断言，直接跑）**：

```bash
git status --short                              # 必须为空
git rev-list --left-right --count @{u}...HEAD   # 必须是 "0	0"（不领先也不落后）
git rev-parse v1.2.2^{commit}                   # 应为 1a6d251，tag 不会移动
git rev-parse v1.3.0^{commit}                   # 应为 753084c，tag 不会移动
git rev-parse v1.3.1^{commit}                   # 应为 092b7c4，tag 不会移动
git tag -l "v1.3*"                              # 应含 v1.3.0 与 v1.3.1（本机缺 tag 时先 git fetch --tags）
PY=.../Python310/python.exe; $PY scripts/harness.py check   # 一次跑完静态+Harness+隐私+隔离E2E
```

**不在仓库里、只能现场查的**：`D:\What-IDid` 的运行状态与心跳、`dist/` 是否比源码旧、
`release/` 目录是否还在、iMac 侧是否还有未消费的 `.new`。这些写进文件只会立刻过期。

## 已完成

- [x] v1.0 首次公开发布（采集 + OCR + 日报 + 看板）
- [x] v1.1 日报改为「成果简报」：成果卡、待办与卡点、工作主线、时间色带、证据折叠
- [x] v1.2 界面英文化 + 中文可切换；文档全双语
- [x] v1.2.1 修复切语言后侧栏/图例不跟随（模块级 `t()` 固化）
- [x] v1.2.2 日报引入证据分档：写作类动作必须有输入态或文字增量证据；聊天软件只认「已发出」
- [x] v1.2.2 的 zip 落盘、生产实例替换、push、tag、GitHub Release 全部完成
- [x] v1.3.0 来源链接（R1）：`src/browse.py` 读本机 Chrome/Edge 历史、地址栏兜底、仅域名兜底；
      设置页三个开关；分享版去 query；`forget` 与保留期同步覆盖 `urls.jsonl`；`doctor` 新增检查项
- [x] v1.3.0 打包修复：`build.spec` 曾把 `sqlite3` 写进 `excludes`，已移入 `hiddenimports`，
      否则只有绿色版会坏
- [x] v1.3.0 聊天证据收口：11-05 日报把「只看了群会话列表」写成「扫群消息」并推出
      「等别人确认排期」的待跟进，用户纠正后改了三处——报告数据按 `manual.json` 覆盖重写、
      提示词把禁令扩到 label/detail/followups/tomorrow、本地兜底把聊天窗口映射为 reading
- [x] 建立本文件与 `AGENTS.md`、`agents/*`（仓库即事实来源），并入 `888c30f`
- [x] **Harness 工作系统**（2026-11-05）：`feature_list.json` 机器状态 + `scripts/harness.py`
  状态机（start/verify/block/resume，WIP=1、依赖门控、passing 不可逆、失败不写通过证据）+
  `scripts/check_harness.py` 24 项隔离自测 + `init.sh` 统一初始化 + 本机 `state/harness/` JSONL 轨迹；
  配套 `agents/harness.md`（路由与分工）、`sprint-contract.md`、`clean-state-checklist.md`、
  `session-handoff.md`、`evaluator-rubric.md`、`quality-document.md`、`graph.md`（进阶蓝图）
- [x] 1104 用新提示词重出报告（旧版备份 `reports/2026-11-04.json.bak-20261105`，仅本机）
- [x] **两轮「克隆后五问」文档纠偏**，并入 `227935c`（17 项）。做法：从 GitHub 克隆纯净副本 →
      全新会话只读它回答五个基本问题 → 按它指出的矛盾逐条核代码再改；改完再造一份「推送后快照」
      让第二个新会话复查。第一轮查出 11 处，第二轮查出 8 处（含我自己改错的两处）。
      修的类别：`dev/` 根假设、已过期的进度摘要、计划任务条数、「正式采集不 OCR」、`scripts/*` 的
      `.build/` 过期路径、写死的回归项数、`publish.py` / `WORKSPACE.md` 的本机引用；
      另补 `requirements.txt`（9 个包按实测版本钉住）并让 `build_dist.py` 按它安装。
- [x] **v1.3.0 入库与发布**（2026-11-06）：`7f4e02c`（Harness）、`753084c`（v1.3.0），
      附注标签 `v1.3.0` 已推送；`764d2c2` 补 CHANGELOG 与状态修正（不在 tag 内，沿用 v1.2.2 的取舍）。
      GitHub Release 于 10:35 发布，资产与本地 sha256 一致。
- [x] **v1.3.1 入库与发布**（2026-11-06）：`07fe7f3`（仓库地图移出入口）、`2ac33c9`（Harness 证据去本机路径）、
      `60cc91e`（操作按钮独占一行 + `gui-001` 登记）、`092b7c4`（版本号与 CHANGELOG）；附注标签 `v1.3.1` 已推送。
      Release 于 14:10 发布，资产与本地 sha256 一致（GitHub `digest` 逐位相同）。
      修的是真 bug：`badge_box` 实际 520 宽、内容需 677 宽，pack 把末位「审核待发内容」压成 1×1，
      `winfo_ismapped()` 为 0——只看得见「找不到按钮」，看不见「按钮在但点不到」。
      回归断言因此从「找得到」改成「看得到」，`find_op()` 统一在 `page.ops_row` 里找并断言可见。
- [x] **v1.4.0 范围收窄**（2026-11-06）：来源链接（R1）与知识卡（R2）整块移出产品，依据 D-19。
      删 `browse.py` 与它的全部消费者（`report.py` 的出报主路径、`Block.urls`、refs 注入循环、
      `export_safe` 的 domain 角标）、`ocr.jsonl` 的行坐标、设置页整张卡片、`doctor` 检查项、
      `browse.*` 配置与 8 份文档；打包不再带 `sqlite3`。顺带修一个真 bug：`_set_path` 插三层键时
      缩进写浅一级（父节已有子键时才暴露）。

## 进行中

- **Harness 工作系统已建成**：`python scripts/harness.py check` 全绿。功能层面的推进状态以
  `python scripts/harness.py status` 为准（当前 5 项全部 `passing`、`blocked` 归零），本文件不复制功能清单，
  也不记录「几项待提交」这类一提交就过期的数字。
- 提交与推送仍由 iMac 侧按 `agents/workflow.md` 执行；本机只改与验。

## 需求交付状态 · WID-REQ-2026-11-001（R1 已移除、R2 已取消）

需求原文：`WID-REQ-2026-11-001` **v0.2**（2026-11-05，已评审、口径锁定，冲突以 §15 为准），
**已入库**：`agents/requirements/wid-req-2026-11-001.md`（逐字归档，开头有一段入库说明）。
本机另有一份同源副本在 `E:\Self\Project\DSH\Default\notes\`（不在仓库），两处内容一致，以仓库那份为准。
**本节的判定表是交付事实的唯一口径**；原文是评审时的快照，不再回改。

**原文的分批约定**：1.3.0 只做 R1（来源链接全线，含微信域名级来源与 PDF 文件链接），
知识卡 R2 整块移到 1.3.1（原文 §0 / §12 / 附录 D 的 D-8）。

**2026-11-06 的产品决策把这两批都移出范围**（`agents/decisions.md` **D-19**）：软件只回答「我这一天
做了什么」，不再做知识内容的总结与跳转。**该决策已在 `1.4.0` 落实到代码**，下面两张表因此不再是待办，
而是「曾经交付过什么」的记录与移除依据：

- **R1**：v1.3.0 交付、v1.3.1 带着，**已于 `1.4.0` 从代码中整体移除**（实况见本节末尾
  「R1 移除实况」）。已发布的 v1.3.0 / v1.3.1 资产与 tag 不回滚——那两个版本确实带过这个功能。
- **R2**：一行代码没写，**整块取消**，含 D-18 拆出的 R2a / R2b。D-17（落 1.4.0）与 D-18（拆 R2a/R2b）
  均被 D-19 取代。

### R1 功能需求（v1.3.0 交付、v1.4.0 移除；下表记录的是「曾经实现过什么」）

| 编号 | 需求 | 状态 | 落地位置 |
| --- | --- | --- | --- |
| FR-1.1 | 报告期反查 Chrome/Edge 历史库 | 完成 | `src/browse.py`：把 `History` 拷到 `state/tmp`、`mode=ro` 查询，时间换算式实测正确 |
| FR-1.2 | 命中 URL 以 `kind=url` 注入 refs | 完成 | 直接注入候选，未新增 provider（按原文 §5.2 的决定） |
| FR-1.3 | 地址栏 OCR 兜底 | 完成 | `browse.address_bar_url()`；`src/ocr.py` 落盘 `lines`（y0/y1 + 文本），无坐标的旧记录直接跳过 |
| FR-1.4 | 微信/飞书退到域名级来源 | 完成 | `browse.domain_hint()` 只认 OCR 原文里的域名；模板渲染 `(domain only)` 角标 |
| FR-1.5 | PDF 按文件名 → `file://` | **半完成** | 机制在（`refs/extract.py` 的 `RE_FILE` 含 pdf、`LocalFileProvider` 可用），但 `refs.search_roots` 默认空，得用户自己填目录；从未拿真实 PDF 块验过 |
| FR-1.6 | URL 归一化：留 query、敏感值打码、去 fragment | 完成 | `privacy.normalize_url()` / `canonical_url()`，13 条自测断言 |
| FR-1.7 | 结果整份覆盖写 `data/<day>/urls.jsonl` | 完成 | `browse.py` 覆盖写；`forget.py` 与保留期都覆盖该文件 |
| FR-1.8 | 任一环节失败静默降级 | 完成 | 各通道异常各自吞掉，历史库不可读只记 debug，不影响出报 |

验收项（原文 §10，1.3.0 批次共 12 条）逐条判定：

| 编号 | 判定 | 依据 |
| --- | --- | --- |
| AC-1 默认关时行为与 v1.2.2 一致 | **未自动验证** | 代码按 `browse.history.enabled=False` 短路，但没有「默认配置下不产出任何 URL」的回归断言 |
| AC-2 开启后命中率 ≥80% | ✅ 实测 86% | v1.3.0 发布说明：真实一天 64 个网页块命中 55 个 |
| AC-3 query 保留 + 敏感值变 `[REDACTED]` | ✅ | `check_privacy.test_source_links` |
| AC-4 分享版默认去 query | ✅ | 同上，另 `export_safe` 读 `share.keep_query` |
| AC-5 打包产物内含 sqlite3 | ✅ | `dist/What-IDid/_internal/_sqlite3.pyd`；`doctor` 有 `Source links` 项报告三态 |
| AC-6 OCR 每条含 `lines`、旧记录不报错 | ✅ | 落盘实现 + 「无坐标即跳过」断言 + 「顶部区域外不算地址栏」断言 |
| AC-7 微信文章页改判 `web`、聊天窗仍 `comm` | ✅ | 4 条 classify 断言（含「飞书在 MEET 分支之后改判会失效」这个坑） |
| AC-8 设置页开关即时生效 | ✅ | GUI 三个开关（读历史 / 地址栏 / 分享保留 query）+ 三层键写盘自测 |
| AC-9 `forget` 覆盖 `urls.jsonl` | **实现有、无测试** | `forget.py` 已列入该文件，但没有断言证明它真被删掉 |
| AC-10 隐私自测新增项 | ✅ | `check_privacy` 的来源链接段 |
| AC-11 边界自测 | ✅ | 随 `harness.py check` 跑 |
| AC-12 旧日报重渲染不报错 | **未验证** | 模板对缺失字段容错是 Jinja 默认行为，但没拿 v1.2.2 的 json 真跑过 |
| 样本数据扩展 | **未做** | 原文要求样本日含 `urls.jsonl`（带敏感参数）、微信文章块、PDF 块；`scripts/dev_fixture.py` 里没有任何 `urls` 相关内容 |

小计：12 条验收项 **9 条已验、1 条实现有测试无（AC-9）、2 条未验（AC-1 / AC-12）**；功能需求 8 条里
7 条完整、FR-1.5 只到「可配置」；样本数据扩展未做。

### R2 知识卡（**已取消**，D-19；以下是取消时的空白清单与原型结论，留作「为什么不做的依据」）

取消前一行代码未写。原文 §6 整块空白，当时可核对的证据：

- `src/knowledge.py` **不存在**，`build.spec:MODULES` 自然也没有它。
- `config.DEFAULTS` **没有 `knowledge` 段**：`knowledge.max_pages`、`knowledge.model_summary` 都没落盘。
  会话切分要用的 `report.gap_threshold_min` 已存在，可直接复用。
- 缺失的六块：页面会话聚合（同页多块合并）、L1 确定性抽取（同输入同输出）、`sources[]` schema、
  `validate.py` 的四条校验（block_ids 真实、ref_id 降级、quote 必须是原文子串、gist 不许出现原文没有的数字）、
  附录渲染（正文只留一行「今日知识来源 N 条」）、L2 可选模型调用（默认关）；外加图像型页面的
  「未识别到文字」标注（原文 D-1 方案 A）。
- AC-13 / AC-14 / AC-15 三条验收**全部无法判定**。
- `share.include_sources` 已在 `DEFAULTS` 与校验白名单里，但**没有任何消费者**——它是给 R2 预留的键，
  配置里能改、改了没效果，见已知问题 10。

#### 原型结论（2026-11-06，拿 11-05 真实数据按 §6.2 跑）

在 201 块 / 188 会话上按原文规则出卡，**结果不可用**，四条硬伤（均已复现，不是推测）：

1. **行级抽取把"视觉断行"当句子**：屏幕行是按宽度切的，gist 常是残句——实测出现
   `A Programming Paradigm fo:×`（Chrome 内置 PDF 阅读器的标签栏 + 关闭按钮）、
   `Engineering，并给了它一句话定义：`（句子正好切在冒号后）。
2. **`(app, 标题)` 聚合在聊天与 PDF 上失效**：飞书窗口标题恒为「飞书」，全天聊天被并成两张一模一样
   的卡，内容全是别人的消息（违反 D-08 证据档位）；PDF 阅读器的 OCR 抓的是工具栏。
3. **打分没有区分度**：前 40 个会话的最高分全落在 2–4 分，界面控件（`再学31分钟18秒学完`）与正文同分。
4. **没有按分类过滤**：那天 66 个 `other`、46 个 `meet` 块也进了卡片池；不排除会议/聊天窗口就会
   产出「会议知识卡」。

结论：**R2b（L1 打分）不能按 §6.2 直接实现**。要它成立，得先把「OCR 行 → 语义段」做出来
（按 `lines` 的 y 坐标续行、按行尾标点断句），而 `lines` **只有 v1.3.0 之后采集的日期才有**
（11-05 及更早没有）——现在连评估质量的数据都不够。

**R2a 曾定过范围**（D-18：只对 `web` / `doc` 生成、聚类优先 URL、标题等于应用名即丢弃、只给
站点 / 标题 / 停留 / 首行片段），**已随 R2 一并取消**。原型结论留在这里，是为了以后有人再提「加知识卡」
时先看到这段实测：不是没时间做，是这条路在真实数据上走不通。

### R1 移除实况（`1.4.0` 已执行；依据 D-19）

原计划是「先不发版、攒反馈」，2026-11-06 改为直接裁剪并重新发版。实际改动如下（本版提交的
`git show --stat` 可核）：

| # | 位置 | 改了什么 |
| --- | --- | --- |
| 1 | `src/browse.py` | 整文件删除 |
| 2 | `build.spec` | 删 `MODULES` 的 `"browse"`；删 `hiddenimports` 的 `"sqlite3"` / `"_sqlite3"`（全仓唯一的 sqlite 消费者就是它） |
| 3 | `src/config.py` | 删 `DEFAULTS.browse`、`share.include_sources` 与对应的六行校验 |
| 4 | `src/ocr.py` | 删 `OcrResult.lines` / `height`、`_top_lines()`、`_box_y()`、三个 `recognize_rich()` 与调用分支 |
| 5 | `src/gui.py` | 删「来源链接」整张卡片（读历史、地址栏两个开关）；`share.keep_query` 迁进隐私卡片 |
| 6 | `src/doctor.py` | 删 `Source links` 与 sqlite3 自检（2b 段） |
| 7 | `src/forget.py` | 删 `_filter_urls()` 与调用；对 `urls.jsonl` 的**遗留文件清理保留** |
| 8 | `src/templates/day.html.j2`、`_style.html.j2` | 删 `(domain only)` 角标与它的样式 |
| 9 | `scripts/check_privacy.py` | 删地址栏/域名用例；保留的打码与微信/飞书分类断言改名为 `test_url_and_classify` |
| 10 | `src/i18n_zh_cn.py` | 删来源链接词条，保留三条分享脱敏文案 |
| 11 | `config.default.yaml`、`docs/` 八份 | 删 `browse` 段、`include_sources`、整节「读取浏览历史」、架构里的「补链接」步骤与 `urls.jsonl` |
| 12 | `feature_list.json` | 移除 `source-links-001` 条目 |

**原清单漏了四处消费者**（当时只 grep 了 `browse.`，漏掉 `import browse as xxx` 与字段级引用）：

- `src/report.py`：出报前调用 `browse.enrich()` 的那段 `try` —— 这是主路径，漏删直接 `ImportError`；
- `src/blocks.py`：`Block.urls` 字段；
- `src/refs/extract.py`：把 `Block.urls` 注入 refs 候选的循环；
- `src/export_safe.py`：给 `source == "domain"` 的 ref 加「(domain only)」后缀。

**教训**：删一个模块要同时 grep 模块名、它引入的字段名、以及 `import X as Y` 三种形态，只 grep 一种必漏。

**没有跟着删的**（仍在使用）：`url.*` 配置与 `privacy.normalize_url` / `canonical_url`（`export_safe.py` 与
`refs/resolve.py` 在用）、`share.keep_query`（分享副本的 query 脱敏）、`paths.urls_path()` 与 `forget` 里对
`urls.jsonl` 的清理（只为清掉旧版本留下的文件）、`refs` 整套（v1.2 就有）。

## 已知问题

1. **`src/paths.py:56-60` 有一段等价分支**：带 `portable.marker` 的分支与紧随其后的「可写就用程序目录」
   返回同一个值，marker 实际不起作用；`docs/architecture*` 也只写了三级路径、没提这个 marker。
   要么删掉分支，要么让 marker 真能强制便携。改它要重建 dist，留到下次动 `paths.py` 时一起做。
2. **`W:` 共享的权限边界（实测修正，原表述「不可写已有文件」是错的）**：根目录可写可删；
   仓库子目录允许**新建文件**，也允许对已有文件**原地截断写/追加**；真正被禁的是**改名与删除**
   ——git 提交要 write-temp + rename，所以 Windows 侧 commit / push 必失败。
   改过的文件过去一律用 `.new` 中转，是当初误判「不可写」时留下的习惯；普通文件可以直接覆盖，
   但覆盖会丢可执行位（`init.sh` 就这么从 100755 变成 100644，工作区因此显示脏），
   有 x 位的文件仍走 `.new` + iMac 侧 `mv -f`。路径大小写也要写准：`What-IDid` 能 `ls`
   却 `cd` 不进、写入报 No such file。
3. **Windows 侧无 git 凭据**，push / tag / GitHub Release 只能 iMac 侧做。
4. **同步是手动的**（`git fetch && git reset --mixed`）。推送后不归零，下次改动的 diff 里会混进旧提交。
5. **本文件依赖人/agent 主动更新**，没有任何自动检查会发现它过期。已经错过三次：
   一次写「6 项待提交」而实际已推送、一次写「工作区干净」而实际 15 项没提交、
   一次写「17 项未提交」而当时刚提交完。**根因是写死当前 HEAD 与未提交项清单**——文件一被提交，
   这两个值必然变化。现在的做法：状态一律写成不变量 + 自查命令（见「当前状态」开头），
   字面量只留给已经不动了的提交号。改这一节时先跑自查，别凭上一次的记忆写。
6. **块清单的「分类」是本地按标题猜的**（含「文档/.md」的标题会被猜成 writing）。
   它只用于聚类，不能作为行为判定依据，prompt 里已明确写死。
7. **本文件与 `feature_list.json` 是两条独立维护的线，没有脚本比对**：产品行为可以在本文件里
   标成已完成，而功能项仍是 `not_started`。**功能状态一律以 `harness.py status` 为准**，
   本文件只记项目级事实，不复制状态计数。
8. **`v1.3.0` 的 tag 里少两条 CHANGELOG 说明**：tag 指向 `753084c`，其 CHANGELOG 标题是
   `2026-11-05` 且没有「旧 config.yaml 照旧可用」「内置 SQLite 组件」两条；这两条是 tag 之后
   的文档提交 `764d2c2` 补上的，已写进 GitHub Release 正文。**tag 已随 Release 发布，不再移动**——
   移动一个已发布 Release 的 tag，代价大于「checkout 后 CHANGELOG 少两行」。
   `v1.3.1` 的 tag 已带上完整版本，此后从最新 tag checkout 不再缺内容；残留的只有 `v1.3.0` 那个 tag。
   **`v1.4.0` 起不再有这个问题**：CHANGELOG 段在打包前写好，`publish.py` 直接从它截取。

## 下一步

1. **发 `v1.4.0`**：产物已在 `release/v1.4.0/`；打 tag 与建 GitHub Release 由 iMac 侧做
   （本机无凭据）。Release 正文用 `release/v1.4.0/RELEASE_NOTES.md`，资产上传那个 zip。
2. **换生产实例**：`D:\What-IDid` 目前跑 1.3.1。换 1.4.0 时保留 `data/` 与 `reports/`，
   替换后跑一次 `doctor --full`，确认采集心跳恢复。
3. **观察日报本身，而不是加功能**：这是软件唯一的核心。重点看证据档位——有无把「看」写成「写」、
   有无把群消息写成「我参与了」；有偏差按 `agents/decisions.md` 的 D-08 调 prompt，不加特例。
4. **新需求先过准入判据**（D-19）：它是在记录「我做了什么」，还是在管理「这些内容是什么」？
   后者不进这个软件——这是本次收窄留下的唯一长期规则。
