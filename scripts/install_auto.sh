#!/usr/bin/env bash
set -euo pipefail

# 一行命令安装脚本（可重复执行：首次安装，后续升级）
# 用法：
#   curl -L <script-url> | bash
#   curl -L <script-url> | bash -s -- uninstall --purge

REPO_URL="${KEYGEN_REPO_URL:-https://github.com/1402771410/Codex-keygen.git}"
REPO_BRANCH="${KEYGEN_REPO_BRANCH:-main}"
INSTALL_DIR="${KEYGEN_INSTALL_DIR:-$HOME/.codex-keygen}"

ACTION="install"
if [[ "${1:-}" == "install" || "${1:-}" == "upgrade" || "${1:-}" == "uninstall" ]]; then
    ACTION="$1"
    shift
fi

EXTRA_ARGS=("$@")

say() {
    echo "[keygen-auto] $*"
}

has_arg() {
    local target="$1"
    shift
    for item in "$@"; do
        if [[ "$item" == "$target" ]]; then
            return 0
        fi
    done
    return 1
}

ask_yes_no() {
    local prompt="$1"
    local default_yes="${2:-1}"
    local suffix="Y/n"
    if [[ "$default_yes" != "1" ]]; then
        suffix="y/N"
    fi

    local answer=""
    if [[ -r /dev/tty ]]; then
        printf "%s (%s): " "$prompt" "$suffix" > /dev/tty
        IFS= read -r answer < /dev/tty || true
    else
        if [[ "$default_yes" == "1" ]]; then
            return 0
        fi
        return 1
    fi

    answer="${answer,,}"
    answer="${answer// /}"
    if [[ -z "$answer" ]]; then
        [[ "$default_yes" == "1" ]]
        return
    fi
    [[ "$answer" == "y" || "$answer" == "yes" || "$answer" == "1" || "$answer" == "true" ]]
}

detect_os() {
    local os_name
    os_name="$(uname -s 2>/dev/null || true)"
    case "$os_name" in
        Linux*) echo "linux" ;;
        Darwin*) echo "macos" ;;
        MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
        *) echo "unknown" ;;
    esac
}

detect_linux_family() {
    if [[ ! -f /etc/os-release ]]; then
        echo "unknown"
        return 0
    fi
    local text
    text="$(tr '[:upper:]' '[:lower:]' < /etc/os-release)"
    if [[ "$text" == *"debian"* || "$text" == *"ubuntu"* ]]; then
        echo "debian"
    elif [[ "$text" == *"rhel"* || "$text" == *"centos"* || "$text" == *"fedora"* || "$text" == *"rocky"* || "$text" == *"almalinux"* ]]; then
        echo "rhel"
    elif [[ "$text" == *"arch"* ]]; then
        echo "arch"
    else
        echo "unknown"
    fi
}

sudo_prefix() {
    if [[ "$(detect_os)" != "linux" ]]; then
        return 0
    fi
    if [[ "${EUID:-$(id -u)}" == "0" ]]; then
        return 0
    fi
    if command -v sudo >/dev/null 2>&1; then
        echo "sudo"
    fi
}

install_dependency_linux() {
    local dep="$1"
    local family
    family="$(detect_linux_family)"
    local prefix
    prefix="$(sudo_prefix)"

    if [[ "$family" == "debian" ]]; then
        ${prefix:+$prefix }apt-get update
        ${prefix:+$prefix }apt-get install -y "$dep"
        return $?
    fi

    if [[ "$family" == "rhel" ]]; then
        if command -v dnf >/dev/null 2>&1; then
            ${prefix:+$prefix }dnf install -y "$dep"
        else
            ${prefix:+$prefix }yum install -y "$dep"
        fi
        return $?
    fi

    if [[ "$family" == "arch" ]]; then
        ${prefix:+$prefix }pacman -Sy --noconfirm "$dep"
        return $?
    fi

    return 1
}

install_dependency_macos() {
    local dep="$1"
    if ! command -v brew >/dev/null 2>&1; then
        return 1
    fi
    brew install "$dep"
}

ensure_command() {
    local command_name="$1"
    local install_dep_linux="$2"
    local install_dep_macos="$3"

    if command -v "$command_name" >/dev/null 2>&1; then
        return 0
    fi

    say "缺少依赖命令：$command_name"
    if ! ask_yes_no "是否自动安装 $command_name ?" 1; then
        echo "用户取消安装依赖：$command_name"
        exit 1
    fi

    local os_name
    os_name="$(detect_os)"
    local ok=1
    if [[ "$os_name" == "linux" ]]; then
        install_dependency_linux "$install_dep_linux" || ok=0
    elif [[ "$os_name" == "macos" ]]; then
        install_dependency_macos "$install_dep_macos" || ok=0
    else
        ok=0
    fi

    if [[ "$ok" != "1" ]] && ! command -v "$command_name" >/dev/null 2>&1; then
        echo "自动安装 $command_name 失败，请手工安装后重试。"
        exit 1
    fi
}

resolve_python() {
    PYTHON_CMD=()

    if [[ -x "$INSTALL_DIR/.venv/bin/python" ]]; then
        PYTHON_CMD=("$INSTALL_DIR/.venv/bin/python")
        return 0
    fi

    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD=("python3")
        return 0
    fi

    if command -v python >/dev/null 2>&1; then
        PYTHON_CMD=("python")
        return 0
    fi

    if command -v py >/dev/null 2>&1; then
        PYTHON_CMD=("py" "-3")
        return 0
    fi

    return 1
}

ensure_repo() {
    mkdir -p "$INSTALL_DIR"

    if [[ ! -d "$INSTALL_DIR/.git" ]]; then
        say "首次安装：克隆仓库到 $INSTALL_DIR"
        git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
        return 0
    fi

    say "检测到已安装目录，执行升级同步"
    git -C "$INSTALL_DIR" fetch --all --prune
    git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
    if ! git -C "$INSTALL_DIR" pull --ff-only; then
        say "git pull 失败，将继续使用本地代码执行命令"
    fi
}

install_keygen_launcher() {
    local bin_dir="$HOME/.local/bin"
    local launcher_path="$bin_dir/keygen"
    mkdir -p "$bin_dir"

    cat > "$launcher_path" <<EOF
#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="$INSTALL_DIR"
if [[ -x "\$INSTALL_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD=("\$INSTALL_DIR/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD=(python)
elif command -v py >/dev/null 2>&1; then
  PYTHON_CMD=(py "-3")
else
  echo "未检测到 Python 3.10+，请先安装 Python。"
  exit 1
fi
export KEYGEN_LAUNCHER=keygen
\${PYTHON_CMD[@]} "\$INSTALL_DIR/scripts/keygen.py" "\$@"
EOF
    chmod +x "$launcher_path"

    say "已安装管理命令：$launcher_path"
    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
        echo "提示：当前 PATH 不含 $bin_dir"
        echo "请执行：export PATH=\"$bin_dir:\$PATH\""
    fi

    if command -v keygen >/dev/null 2>&1; then
        say "现在可直接运行：keygen"
    else
        say "安装完成后可运行：$launcher_path"
    fi
}

should_use_interactive() {
    if has_arg "--non-interactive" "${EXTRA_ARGS[@]}"; then
        return 1
    fi

    # 管道执行脚本时 stdin 通常不可读，优先尝试接管 /dev/tty
    if [[ -r /dev/tty ]]; then
        return 0
    fi

    return 1
}

build_default_args() {
    local args=()

    if ! has_arg "--mode" "${EXTRA_ARGS[@]}"; then
        args+=("--mode" "auto")
    fi

    if [[ "$ACTION" != "uninstall" ]]; then
        # 二次执行默认静默更新，避免重复采集配置；可通过显式参数覆盖。
        if [[ -f "$INSTALL_DIR/runtime-config.json" ]] && ! has_arg "--non-interactive" "${EXTRA_ARGS[@]}"; then
            args+=("--non-interactive")
        fi

        if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]] && has_arg "--non-interactive" "${args[@]}" "${EXTRA_ARGS[@]}"; then
            args+=("--yes-install-docker")
        fi
    fi

    printf '%s\n' "${args[@]}"
}

main() {
    ensure_command "git" "git" "git"
    ensure_command "python3" "python3" "python"

    ensure_repo
    cd "$INSTALL_DIR"

    if ! resolve_python; then
        echo "未检测到 Python（需 Python 3.10+）"
        exit 1
    fi

    # upgrade 与 install 共用同一入口。
    local command_action="$ACTION"
    if [[ "$command_action" == "upgrade" ]]; then
        command_action="install"
    fi

    local default_args=()
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            default_args+=("$line")
        fi
    done < <(build_default_args)

    local final_args=("scripts/keygen.py" "$command_action" "${default_args[@]}" "${EXTRA_ARGS[@]}")

    say "执行命令: ${PYTHON_CMD[*]} ${final_args[*]}"

    if should_use_interactive; then
        "${PYTHON_CMD[@]}" "${final_args[@]}" < /dev/tty
    else
        "${PYTHON_CMD[@]}" "${final_args[@]}"
    fi

    if [[ "$command_action" == "install" ]]; then
        install_keygen_launcher
        say "完成：同一命令可重复执行，后续会自动走升级流程。"
        say "提示：可直接输入 keygen 打开管理面板。"
    else
        say "完成：卸载流程已执行。"
    fi
}

main
