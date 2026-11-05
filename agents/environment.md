# 环境与运行

> 跑不起来、OCR 不工作、进程起不来、路径不确定时读这份。

## 一、解释器

**只有一个是可用的**：

```
C:\Users\xushiyan\AppData\Local\Programs\Python\Python310\python.exe   ← GUI、打包、自测都用它
```

PATH 里的 `python` 是托管版 3.13，**没有 tkinter**，跑 `wid.py gui` 会直接报错。
打包时用的虚拟环境是 `.build/venv`（仓库根下，由 `scripts/build_dist.py` 管理，不要手动装东西进去）。

依赖版本基线见 `requirements.txt`：六个运行时包（Pillow / numpy / requests / Jinja2 / PyYAML / psutil）
+ 可选的内置 OCR 两个（rapidocr / onnxruntime）+ 构建用的 pyinstaller。`build_dist.py` 从镜像装的是
**最新版**，所以真正复现构建时要按 `requirements.txt` 里的版本装，别信「装最新就行」。

> README 面向通用用户写的是「Python 3.10+」，那是发布包的安装要求；**在这台开发机上只有上面
> 那个 3.10 可用**，两者不矛盾。

## 二、路径三级（`paths.py`）

| 优先级 | 位置 | 什么时候用 |
|---|---|---|
| 1 | 环境变量 `WID_HOME` | 自测/临时环境（判据最明确） |
| 2 | 程序目录（若可写） | 绿色便携使用 |
| 3 | `%LOCALAPPDATA%\What-IDid` | 程序目录只读（如 Program Files）时 |

- 只读资源（模板、docs、OCR 模型）走 `paths.BUNDLE`（打包后是 `sys._MEIPASS`）。
- 可写数据（data/reports/web/state/logs/config.yaml）走 `paths.ROOT`。
- **所有模块都从 `paths` 取目录，不要在别处拼 `__file__`。**

## 三、OCR 两条腿

| 引擎 | 何时用 | 怎么工作 |
|---|---|---|
| Umi-OCR（首选） | `ocr.engine: auto` 且检测到常驻实例 | 先 `--hide` 常驻，再走 HTTP `127.0.0.1:1224/api/ocr`，快 |
| 内置 RapidOCR（兜底） | 没有 Umi 时 | ONNX，30 MB 模型随包，离线，约 5 s/张 |

要点：整屏识别质量差，**必须裁活动窗口原生分辨率再识别**；RapidOCR 默认往 stderr 打 INFO，
构造前把 logger 压到 WARNING。

## 四、两条计划任务（`autostart.py`）

| 任务名 | 作用 | 触发 |
|---|---|---|
| `WhatIDid-Recorder` | 常驻采集 | 登录时启动，`ExecutionTimeLimit=PT0S`（永不超时） |
| `WhatIDid-Report` | 出报 | 21:00 起每 20 分钟重复 2 小时；登录 +3 分钟补跑；次日 04:05 终版 |

- 只有这两条由代码创建（`src/autostart.py` 的 `TASK_NAME` / `REPORT_TASK_NAME`）。公开文档
  `docs/architecture.md` 写「最多注册两个」与此一致。
- **`WhatIDid-LaunchGui` 不在代码里**：它是所有者机器上手工建的一条空任务（无触发器、无参数，
  目的只是 `Start-ScheduledTask` 能拉起界面），`autostart.py install/uninstall/status` 都不管它。
  克隆里没有这条任务，也不该有——真要复现，按本节末尾的命令手工建，别指望代码给你。
- XML 模板写死在 `autostart.py`，改 `report.trigger_hour` 必须重装任务（钟点写进 XML）。
- 必须用 `InteractiveToken`。用「不管用户是否登录」会落到 Session 0，**截图全黑**。
- 注册走双路：先 `Register-ScheduledTask -Xml`（PowerShell `-EncodedCommand`，先剥 `<?xml?>`），
  失败退 `schtasks`。`schtasks.exe` 在程序黑名单时 `subprocess.run` 抛的是 `OSError` 而非返回码。

<details>
<summary>手工建 `WhatIDid-LaunchGui`（仅所有者机器的补救手段）</summary>

```powershell
$a = New-ScheduledTaskAction -Execute "D:\What-IDid\What-IDid.exe"
Register-ScheduledTask -TaskName WhatIDid-LaunchGui -Action $a -Force
```

不要给它加触发器：它被设计成「只在需要时手动 Start」的一次性入口。
</details>

## 五、在工具会话里启动进程

在这个环境里 `Popen(DETACHED)`、`cmd start`、explorer 转发、COM、WMI **都会被拦或活不过命令边界**
（实测心跳正好停在命令结束那一秒）。可行方式只有计划任务：

```powershell
Start-ScheduledTask -TaskName WhatIDid-Recorder     # 拉起采集
Stop-ScheduledTask  -TaskName WhatIDid-Recorder     # 停止（一次只能传一个名字）
Start-ScheduledTask -TaskName WhatIDid-LaunchGui    # 拉起界面
```

拉起后必须跨命令复核三件事：pid 存活、心跳 `ts` 在前进（`wid.py status`）、界面窗口标题可见
（`EnumWindows` 找标题；标题跟语言走）。

## 六、权限边界（Windows 侧 vs iMac 侧）

| 能做 | 只能由 iMac 侧做 |
|---|---|
| 改代码、跑自测、重建 dist、打 zip、`cp` 到 `W:` 成 `.new`、换 `D:\What-IDid`、清 `.old` | `mv -f *.new`、`git add/commit/push`、`git tag` 及推送、GitHub Release 建版与上传 |

`W:` 是 SMB 共享（`\\10.52.26.80\...`），对 Windows 侧**只允许新建、不允许覆盖或删除**。
不是权限位、不是占用、不是沙箱——`chmod`、杀 `TGitCache`、换路径都无效。所以 `dev/` 不能放到 `W:`，
git 在那边写不了对象。

## 七、常见坑

1. 本机 HTTP 请求一律 `requests.Session(trust_env=False)`——系统有 `HTTP_PROXY=127.0.0.1:65481`；
   但**访问公网模型服务要保留代理**，`llm.py` 故意不关。
2. 空闲时长用 `GetTickCount() & 0xFFFFFFFF`（会回绕）。
3. 窗口化 exe 里 `sys.stdout` / `sys.stderr` 是 `None`，`wid.main()` 开头兜 devnull。
4. PyInstaller 覆盖 `dist/` 会触发批量删除确认：先 rename 旧目录；旧产物被占用会撞
   `PermissionError`，先停 exe。
5. `Popen` 拉后台进程三流全 DEVNULL，否则父命令一直等管道关闭。
6. `ExtractIconExW` / `Shell_NotifyIcon*` 都在 **shell32**，不是 user32。
7. 托盘菜单弹前 `SetForegroundWindow`、弹后 `PostMessage(hwnd, WM_NULL)`，否则点外面不消失。
8. Win10/11 默认把新托盘图标塞进「^」溢出区，没有 API 能强制常驻，只能提示用户拖一次。
9. 托盘窗口必须独立线程 + 自己的 `GetMessage` 循环；建在 tk 主线程会崩在
   `PyEval_RestoreThread: NULL thread state`。跨线程只用 `queue`，主线程 `after` 轮询。
10. `day_window()[0] + 21h` 是**次日 01:00**（窗口起点 04:00）。钟点一律走 `timeutil.trigger_at()`。
11. 单 exe 后 `control._is_ours()` 只能靠命令行里有没有 `daemon` 区分，否则会误杀界面。
12. 界面点 × 只是收进托盘，**没有 CLI 退出入口**；换版时用 `psutil.Process(pid).terminate()`
    （界面无状态，强杀不丢数据）。
