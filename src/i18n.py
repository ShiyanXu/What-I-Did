"""界面文案的多语言支持。

设计要点：**英文是源语言**。代码里直接写英文原文，`t("Settings")` 在英文下原样
返回，其它语言只是在 `i18n_<代码>.py` 里补一张对照表。这样做的三个理由：

1. 加一种语言 = 加一个模块 + 在 `LOCALES` 里登记一行，不改任何业务代码；
   漏翻的句子会退化成英文，而不是显示成 key 或空白。
2. 新文案先写英文，不存在"没翻译就没法显示"的死角。
3. 对照表的 key 就是英文原文，改文案时 diff 一眼看出动了哪句。

不支持逐字热替换：语言一变，界面需要整窗重建（GUI 用重启进程实现），因为
tkinter 控件的文案参与布局计算，建好之后再改会留下错位的间距和宽度。
"""
from __future__ import annotations

import ctypes
import importlib
import os

DEFAULT_LOCALE = "en"

#: 环境变量：临时指定语言，优先级高于配置。
#: 主要给自测和"只想跑一次中文、不想改配置"的场景用，正常使用走 ui.language。
ENV_VAR = "WID_LANG"

#: 语言代码 → 该语言的自称。顺序即设置页下拉框的顺序。
LOCALES: dict[str, str] = {
    "en": "English",
    "zh-CN": "简体中文",
}

_LANG_CHINESE = 0x04          # Windows LANGID 主语言 ID
_current = DEFAULT_LOCALE
_loaded: dict[str, dict[str, str]] = {}


# ---------------------------------------------------------------- 语言选择

def locale_module(code: str) -> str:
    """zh-CN -> i18n_zh_cn。新增语言时不用改这里。"""
    return "i18n_" + code.lower().replace("-", "_")


def normalize(code: str | None) -> str:
    """把配置里的值归一到受支持的代码；不认识的一律回落到默认语言。"""
    raw = "" if code is None else str(code).strip()
    if raw == "" or raw.lower() in ("auto", "system"):
        return detect_system_locale()
    for known in LOCALES:
        if raw.lower() == known.lower():
            return known
    low = raw.lower()
    if low.startswith("zh"):
        return "zh-CN"
    if low.startswith("en"):
        return "en"
    return DEFAULT_LOCALE


def env_override() -> str:
    """`WID_LANG` 里指定的语言，没设就是空串。"""
    return os.environ.get(ENV_VAR, "").strip()


def detect_system_locale() -> str:
    """读 Windows 界面语言，只区分"中文 / 其它"。"""
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return "zh-CN" if (langid & 0xFF) == _LANG_CHINESE else DEFAULT_LOCALE
    except Exception:
        return DEFAULT_LOCALE


def set_locale(code: str | None) -> str:
    """设置当前语言，返回实际生效的代码。`WID_LANG` 优先于传进来的值。"""
    global _current
    _current = normalize(env_override() or code)
    return _current


def current() -> str:
    return _current


def native_name(code: str) -> str:
    return LOCALES.get(code, code)


def display_values() -> list[str]:
    """设置页下拉框用：把自称和代码拼在一起，选中后能反解出代码。"""
    return [f"{name} ({code})" for code, name in LOCALES.items()]


def code_of_display(shown: str) -> str:
    for code, name in LOCALES.items():
        if shown == f"{name} ({code})":
            return code
    return normalize(shown)


# ---------------------------------------------------------------- 取词

def _table(locale: str) -> dict[str, str]:
    table = _loaded.get(locale)
    if table is None:
        if locale == DEFAULT_LOCALE:
            table = {}
        else:
            try:
                mod = importlib.import_module(locale_module(locale))
                table = dict(getattr(mod, "CATALOG", {}) or {})
            except Exception:   # 译文模块缺失或损坏时不能把界面拖死
                table = {}
        _loaded[locale] = table
    return table


def t(src: str, *args, **kwargs) -> str:
    """取当前语言的文案，可顺带做占位符替换。

        t("Settings")                  -> "Settings" / "设置"
        t("Saving {n} files", n=3)     -> "Saving 3 files"

    第一个参数叫 `src` 而不是 `text`：文案里常有 `{text}` 这种占位符，
    形参要是也叫 text，`t("... {text}", text=x)` 会撞成「重复赋值」。
    """
    out = src if _current == DEFAULT_LOCALE else _table(_current).get(src, src)
    if args or kwargs:
        try:
            return out.format(*args, **kwargs)
        except (KeyError, IndexError, ValueError):
            return out
    return out


def tn(count: int, one: str, many: str, **kwargs) -> str:
    """英文单复数：one/many 都写英文原文，其它语言各有一条译文。

        tn(n, "{n} day", "{n} days", n=n)
    """
    return t(one if count == 1 else many, n=count, **kwargs)


def join_list(items) -> str:
    """列表分隔符跟着语言走：英文 ", "，中文 "，"。

    用 t(", ") 实现，中文表里把 ", " 映射成 "，" 即可。
    """
    return t(", ").join(items)
