# 约定与陷阱

> 这份只写「不看代码就会写错」的东西。风格问题跟随现有代码即可。

## 一、依赖政策（硬性）

只用标准库 + Pillow / numpy / requests / Jinja2 / PyYAML / psutil。**不新增任何 pip 依赖**，
包括看起来很小的那些。已有的替代方案：

| 想要 | 已经用了什么 |
|---|---|
| 截图库（mss） | `PIL.ImageGrab` |
| Windows API（pywin32） | `ctypes`（`winapi.py`、`tray.py`） |
| 定时任务（apscheduler） | Windows 计划任务（`autostart.py`） |
| 图像哈希（imagehash） | 手写 dHash/pHash（`vision.py`） |
| OpenAI SDK | 一个 `requests.post`（`llm.py`） |
| Web 框架 | 静态 HTML + 本地文件 |

## 二、多语言（踩过 5 次，全在运行期炸）

1. **英文是源语言。** 代码里直接写英文，`t("Settings")` 在英文下原样返回；中文只放在
   `src/i18n_zh_cn.py:CATALOG`。加语言 = 复制 `i18n_<code>.py` + 在 `i18n.LOCALES` 登记一行。
2. **不要用 `t` / `tn` 当局部变量名。** `t = ...`、`t, s = ...`（解包）、`for t in ...` 会让**整个函数**
   里的 `t()` 全废：先 `UnboundLocalError`，进循环后变 `'dict' object is not callable`。
   循环变量一律 `item` / `task` / `tick` / `tk` / `worker` / `icon`。
3. **不要在模块级、类体、默认参数里调 `t()`。** 切语言是**进程内重建窗口**，不重新 import，
   这类调用会永久冻在首次语言（v1.2.1 的真实事故：侧栏一直英文）。三种正确写法：

   ```python
   NAV = [("dash", "Overview", "dash")]          # 存英文源
   def nav_items(): return [(k, t(label), i) for k, label, i in NAV]

   def show_placeholder(msg: str | None = None):
       msg = msg or t("Pick a report on the left")   # 体内取

   CAT_LABEL = {"dev": "Dev"}                     # 存英文源
   def cat_label(k): return t(CAT_LABEL.get(k, k))
   ```

4. **`t()` 第一个形参叫 `src`**，不能叫 `text`——文案里常有 `{text}` 占位符，会撞成重复赋值。
5. **本地化文本不落盘、不做文件名。** JSON 里不存显示文案（星期按日期重算）；文件名固定英文
   （`<day>.md`、`share/<day>-share.md`、`<day> daily report.md`），否则切一次语言就多一份。
6. **逻辑判定不许比较显示文案。** 用稳定代码（`WHY_FINAL`、`incremental`），显示时才翻译。
7. **查漏翻的办法**：同一页分别以 `WID_LANG=en` / `zh-CN` 遍历控件树导出 `text`/`values`，
   两边完全相同的英文串就是没跟语言走的（`API Key`、`English (en)` 属有意保留）。

## 三、配置

- 新增配置项**必须同时**写进 `config.default.yaml`（模板，入库）与 `src/config.py:DEFAULTS`。
  只写一处会导致不校验类型，字符串 `"false"` 被当真值。
- `config.yaml` 与 `config/` 是本机实际配置，**永不入库、永不进发布包**。
- 读配置一律走 `cfg.get("a.b", default)`，不要直接读文件。

## 四、模板与渲染

- Jinja2 配了 `trim_blocks=True`：`{% endif %}` 后的换行会被吃掉。模板每行必须以字面文本结尾，
  空行交给 `render._fix_md()` 统一处理，不要在模板里手写空行。
- **`| default(x)` 兜不住 `Undefined` 取属性**：`d.stats.rhythm.longest_focus_min` 会抛错，
  写成 `((d.stats or {}).get('rhythm') or {}).get('longest_focus_min', 0)`。
- 报告正文的排版永远在 `src/templates/`，模型只负责内容，不负责格式。

## 五、错误处理与日志

- 失败要显式：配置损坏抛 `ConfigError`，隐私拦截抛 `PrivacyError`，模型问题抛
  `LLMError(kind=auth|server|network|bad_json|budget)`，**不要吞掉再返回 None**。
- 降级是允许的（模型不可用时输出纯时间轴），但必须把原因写进 `status` 与 `degraded_reason`，
  不能静默降级。
- 打日志用模块传入的 `log`，不要 `print`：窗口化 exe 里 `sys.stdout` 是 `None`。

## 六、代码风格

- 中文注释与中文文档字符串（面向维护者），英文文案（面向界面）。
- 公开函数写类型注解；数据用 `@dataclass`（见 `blocks.Block`、`behavior.Session`）。
- 单文件职责单一，超过 900 行就该考虑拆分（`gui.py` 是已知的历史例外）。
- 不要为了"以后可能用到"加抽象；这个项目里每一层抽象都能指出具体调用点。

## 七、加文件 / 加字段的清单

新增 `src/*.py`：① 写模块 → ② 加进 `build.spec:MODULES`（`i18n*.py` 自动收集）→
③ 若被 CLI 用到，在 `wid.py` 加子命令 → ④ 跑 `check_boundaries` 确认没破坏边界。

新增报告字段：① 加进 `_USER_SCHEMA` 与 `validate` → ② 模板里加渲染 → ③ 旧报告兼容
（`d.get(...)`，不要假设字段存在）→ ④ `schema_version` 不变。
