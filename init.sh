#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# 可用 PY 指定带 tkinter 的 Python 3.10；所有者机器的取值见 AGENTS.md。
PYTHON="${PY:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "ERROR: 找不到 Python。WHY: 项目需要 Python 3.10。FIX: 设置 PY=/path/to/python3.10 后重跑。" >&2
  exit 2
fi

# 先确认解释器版本，避免依赖装完、跑到一半才发现版本不对。
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "ERROR: $PYTHON 版本过低。WHY: 源码要求 Python 3.10+。FIX: 用 PY 指定 3.10 解释器后重跑。" >&2
  exit 2
fi
if ! "$PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
  echo "WARN: $PYTHON 缺少 tkinter。WHY: 界面与 GUI 回归需要它。FIX: 换带 tkinter 的 3.10 解释器，否则只能跑无界面自测。" >&2
fi

INSTALL_CMD=("$PYTHON" -m pip install -r requirements.txt)
VERIFY_CMD=("$PYTHON" scripts/harness.py check)
START_CMD=("$PYTHON" wid.py)

echo "==> 当前目录: $PWD"
if [ "${SKIP_INSTALL:-0}" = "1" ]; then
  echo "==> 跳过依赖安装（SKIP_INSTALL=1）"
else
  echo "==> 同步依赖"
  "${INSTALL_CMD[@]}"
fi

echo "==> 运行基础验证"
"${VERIFY_CMD[@]}"

echo "==> 标准启动命令"
# 逐项 %q 会重复追加间隔，这里先拼成一行再打印。
printf '    %s\n' "$(printf '%q ' "${START_CMD[@]}")"

if [ "${RUN_START_COMMAND:-0}" = "1" ]; then
  echo "==> 启动应用"
  exec "${START_CMD[@]}"
fi

echo "初始化完成。若希望 init.sh 直接启动界面，请设置 RUN_START_COMMAND=1。"
