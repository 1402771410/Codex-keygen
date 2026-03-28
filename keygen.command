#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

is_python3() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info[0] >= 3 else 1)" >/dev/null 2>&1
}

refresh_path_from_brew() {
  if [[ -x "/opt/homebrew/bin/brew" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x "/usr/local/bin/brew" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

try_install_python3() {
  if [[ "${KEYGEN_AUTO_INSTALL:-1}" != "1" ]]; then
    return 1
  fi

  echo "[INFO] Python 3 is missing. Trying automatic installation..."

  if ! command -v brew >/dev/null 2>&1; then
    echo "[INFO] Homebrew not found. Installing Homebrew..."
    if ! command -v curl >/dev/null 2>&1; then
      echo "[ERROR] curl is required to install Homebrew automatically."
      return 1
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi

  refresh_path_from_brew
  if ! command -v brew >/dev/null 2>&1; then
    echo "[ERROR] Homebrew installation failed."
    return 1
  fi

  echo "[INFO] Installing python via Homebrew..."
  brew install python || brew upgrade python
  refresh_path_from_brew

  command -v python3 >/dev/null 2>&1
}

HOST_OS="$(uname -s 2>/dev/null || echo unknown)"
if [[ "$HOST_OS" != "Darwin" ]]; then
  echo "[ERROR] macOS package is only supported on macOS host."
  echo "[INFO] Current host: $HOST_OS"
  if [[ "${KEYGEN_NO_PAUSE:-0}" != "1" ]]; then
    echo
    echo "Press Enter to close..."
    read -r _
  fi
  exit 1
fi

PY_CMD=()
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]] && is_python3 "$SCRIPT_DIR/.venv/bin/python"; then
  PY_CMD=("$SCRIPT_DIR/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1 && is_python3 "$(command -v python3)"; then
  PY_CMD=(python3)
elif command -v python >/dev/null 2>&1 && is_python3 "$(command -v python)"; then
  PY_CMD=(python)
fi

if [[ "${#PY_CMD[@]}" -eq 0 ]]; then
  if try_install_python3; then
    if command -v python3 >/dev/null 2>&1 && is_python3 "$(command -v python3)"; then
      PY_CMD=(python3)
    elif command -v python >/dev/null 2>&1 && is_python3 "$(command -v python)"; then
      PY_CMD=(python)
    fi
  fi
fi

if [[ "${#PY_CMD[@]}" -eq 0 ]]; then
  echo "[ERROR] Python 3 is required for packaging."
  echo "[INFO] Checked: .venv/bin/python, python3, python"
  echo "[INFO] Auto-install is enabled by default (KEYGEN_AUTO_INSTALL=1)."
  echo "[INFO] Set KEYGEN_AUTO_INSTALL=0 to disable automatic installation."
  if [[ "${KEYGEN_NO_PAUSE:-0}" != "1" ]]; then
    echo
    echo "Press Enter to close..."
    read -r _
  fi
  exit 127
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage:"
  echo "  ./keygen.command"
  echo "  ./keygen.command <OUTPUT_DIR>"
  echo
  echo "Examples:"
  echo "  ./keygen.command"
  echo "  ./keygen.command ~/Desktop/codex-build"
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  echo "[INFO] Output directory: $1"
  "${PY_CMD[@]}" "$SCRIPT_DIR/scripts/package_manager.py" --target macos --output-dir "$1"
else
  "${PY_CMD[@]}" "$SCRIPT_DIR/scripts/package_manager.py" --target macos
fi

EXIT_CODE=$?
echo
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "[成功] 打包已完成。"
  echo "[提示] 发布目录与可执行文件路径已在上方显示。"
else
  echo "[错误] 打包失败，退出码: $EXIT_CODE"
fi
if [[ "${KEYGEN_NO_PAUSE:-0}" != "1" ]]; then
  echo
  echo "打包流程结束，窗口不会自动关闭。"
  echo "按回车键后关闭窗口..."
  read -r _
fi
exit "$EXIT_CODE"
