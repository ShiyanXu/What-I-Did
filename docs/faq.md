# 常见问题

## 安装与启动

**双击提示"Windows 已保护你的电脑"**
exe 没有代码签名，这是 SmartScreen 的默认反应。点「更多信息」→「仍要运行」。企业环境里若杀软误报（PyInstaller 打包的程序偶发），把整个文件夹加入白名单。

**双击没反应，或者一闪就没**
先看有没有黑窗闪一下——那通常是启动即崩。用命令行跑一次 `What-IDid.exe doctor` 看输出；必要时看 `logs\` 下的日志。

**重复双击会开一堆窗口吗**
不会。程序会把已经收在托盘里的那个窗口叫出来。

**点 × 之后程序还在跑**
这是有意的：关窗口只把界面收进托盘，采集继续。要真正退出，右键托盘图标 →「退出（停止采集）」。

**任务栏右下角找不到图标**
Windows 10/11 默认把新图标放进「^」溢出区，点一下展开就能看到。把它拖到常驻区之后就一直在外面。

## 采集

**概览页一直显示"采集没有在运行"**
点「开始采集」。也可以 `What-IDid.exe start`，然后 `What-IDid.exe status` 看心跳。

**截图全黑 / 全白**
计划任务被设成了「不管用户是否登录都运行」，进程落在 Session 0，那里没有桌面。删掉任务，用 `What-IDid.exe autostart --install` 重装，确认是「只在用户登录时运行」。

**锁屏期间还在记录吗**
不会。锁屏始终停止采集，配置里的 `capture.pause_on_locked` 只是兼容字段，写 `false` 也绕不过。

**想临时不记录（开会、共享屏幕）**
`What-IDid.exe pause --minutes 60`，或界面「暂停 30 分钟」。恢复用 `resume` 或点按钮。暂停期间只留匿名时间刻度。

**某些应用不想被记录**
设置页的隐私名单，或直接改配置：`privacy.blocked_apps` 完全屏蔽（只留匿名状态），`privacy.skip_ocr_apps` 只记应用与时长。改完重启采集生效。

**内存或磁盘占用高**
把 `capture.interval_sec` 调大、`capture.max_width` 调小、`retention.shots_days` 调短，都能立刻见效。截图是体积大头，`shots_days: 3` 能省不少。

## 出报

**到点了没出日报**
按顺序看三步：

1. `What-IDid.exe status` —— 最后一行会说"该出还没出"还是"已出"；
2. `What-IDid.exe doctor` —— 「出报定时」看计划任务有没有注册，「出报状态」列出所有待生成的日期；
3. 都没问题时，概览页顶部会有红色提示「X 的日报还没生成 → 立即生成」。

21:00 起的任务每 20 分钟复查一次；晚上没开机，登录后 3 分钟内会补跑。

**想改出报时间**
改 `report.trigger_hour`，然后**必须重新注册计划任务**——触发时刻是注册时写进系统的：

```bat
What-IDid.exe autostart --remove
What-IDid.exe autostart --install
```

**日志里反复出现"等待审核确认"**
开了 `report.review_mode`。内容已经准备好，只是还没确认：日报页点「审核并发送」，或 `What-IDid.exe review approve`。

**日报是"纯时间线版"，没有 AI 写的叙述**
说明模型没被调用或调用失败。检查：Key 是否填了、`privacy.allow_llm` 是否为 `true`、`config.llm.base_url` 是否能连通（设置页有「测试连接」）。降级版会在 `report.degraded_retry_hours` 内自动重试升级，也可以 `report --force` 手动重跑。

**日报写着"降级"，但网络明明恢复了**
升级有两个闸门：时间窗 `report.degraded_retry_hours`（默认 12 小时）和次数 `report.max_upgrade_attempts`（默认 3 次）。超过闸门后只能手动 `report --force`。

**日报里没有具体项目名**
多半是 OCR 没跑到（配额用完，或窗口被名单跳过）。看 `data\<日期>\ocr.jsonl` 里 `status` 是不是 `ok`。调低 `capture.interval_sec` 或调高 `ocr.max_per_day` 能拿到更多细节，代价是更占资源。

**某天完全没生成日报**
当天没有任何观测数据时不会生成空日报——这是为了避免首次安装刷出一堆空页。确认那天的 `data\<日期>\events.jsonl` 是否存在。

**日报结论看着对，但证据对不上**
程序只保证 `block_id` 真实存在，不保证"因为这个块所以这个结论成立"。这是模型的推断问题；如果某段明显记错了，用 `manual override` 改掉那一段，或在「遗忘这天」后重跑。

## 识别

**OCR 一直失败**
先 `What-IDid.exe ocr-test --screen` 看输出。内置 RapidOCR 没有外部依赖；想更快就装 Umi-OCR，把 `ocr.umi_exe` 填上，并在 Umi 设置里开启 HTTP 服务（默认端口 1224）。

**识别特别慢**
内置引擎在 CPU 上跑，大图很吃时间。可以把 `ocr.rapidocr.max_side` 降到 1280，或改用 Umi-OCR。

**识别出来的文字是乱码**
先把 `ocr.rapidocr.min_score` 提到 0.6 以上，太低置信度的行本来就不该进日报。字体过小或缩放异常时，确认 `ocr.crop_active_window` 是 `true`（用原生分辨率裁剪）。

## 数据与隐私

**数据到底存在哪**
`status` 里会打印数据目录。默认是程序目录；不可写时是 `%LOCALAPPDATA%\What-IDid`；设了 `WID_HOME` 就在那里。

**怎么确认它没把图片传出去**
图片不上传是这个程序的设计前提：OCR 全在本地，模型请求里只有文本。想彻底切断外发，把 `privacy.allow_llm` 设为 `false`，或保持审核模式不确认。也可以直接看 `logs\` 里的出报日志与 `reports\<日期>.json` 的 `meta`。

**记到了不该记的内容**
`What-IDid.exe forget --minutes 30` 删掉最近半小时，`forget --day` 删一整天。删除不可逆，也无法撤回已经发给模型服务的部分。

**磁盘越来越满**
`clean --dry-run` 看会删什么，再去掉 `--dry-run` 执行。注意清理由采集进程负责，采集没在跑就不会自动清理。

## 迁移与卸载

**换电脑**
拷整个文件夹，双击运行，向导会重新注册自启。`data\`、`reports\`、`web\` 一起拷过去就能接着看历史。

**想换个地方存数据**
设环境变量 `WID_HOME=D:\MyWorkLog`。注意它不会自动搬迁已有数据，要先手动拷过去再启动。

**彻底卸载**

```bat
What-IDid.exe stop
What-IDid.exe autostart --remove
```

然后删掉整个文件夹。若数据在 `%LOCALAPPDATA%\What-IDid`，连它一起删。

**仓库里的 2026-10-18 是什么数据**
`scripts\dev_fixture.py` 造的演示数据，页面上也有标注。删除：`python scripts\dev_fixture.py --day 2026-10-18 --clean`。

## 边界

- 观测时长不是工时：阅读、看课、会议这类没有输入的活动会被算成低活跃。
- 引用校验不等于结论真实：`block_id` 真实存在，不代表因果关系成立。
- 整屏模式会包含副屏与后台窗口；应用名单只判断前台。
- 目前只支持 Windows 10/11 x64，32 位与 Windows 7 不支持。
- 多屏不同缩放比例的场景尚未充分验证；坐标空间不一致时程序会跳过该帧，最坏是少记几条，不会存错位的图。
