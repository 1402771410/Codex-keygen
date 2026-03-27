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
TTY_INTERACTIVE=0
if [[ -r /dev/tty ]]; then
    TTY_INTERACTIVE=1
fi

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

install_dependency_windows() {
    local dep="$1"
    if ! command -v winget >/dev/null 2>&1; then
        return 1
    fi

    local package_id=""
    case "$dep" in
        git)
            package_id="Git.Git"
            ;;
        python|python3)
            package_id="Python.Python.3.12"
            ;;
        docker)
            package_id="Docker.DockerDesktop"
            ;;
        *)
            return 1
            ;;
    esac

    winget install --id "$package_id" -e --accept-source-agreements --accept-package-agreements
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
    elif [[ "$os_name" == "windows" ]]; then
        install_dependency_windows "$command_name" || ok=0
    else
        ok=0
    fi

    if [[ "$ok" != "1" ]] && ! command -v "$command_name" >/dev/null 2>&1; then
        echo "自动安装 $command_name 失败，请手工安装后重试。"
        exit 1
    fi
}

ensure_python_runtime() {
    if resolve_python; then
        return 0
    fi

    say "未检测到 Python 3 运行环境"
    if ! ask_yes_no "是否自动安装 Python 3 ?" 1; then
        echo "用户取消安装 Python，流程终止。"
        exit 1
    fi

    local os_name
    os_name="$(detect_os)"
    local ok=1
    if [[ "$os_name" == "linux" ]]; then
        install_dependency_linux "python3" || ok=0
        install_dependency_linux "python3-pip" || true
    elif [[ "$os_name" == "macos" ]]; then
        install_dependency_macos "python" || ok=0
    elif [[ "$os_name" == "windows" ]]; then
        install_dependency_windows "python3" || ok=0
    else
        ok=0
    fi

    if [[ "$ok" != "1" ]] && ! resolve_python; then
        echo "自动安装 Python 失败，请手动安装 Python 3.10+ 后重试。"
        exit 1
    fi

    if ! resolve_python; then
        echo "未检测到 Python（需 Python 3.10+）"
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
    local os_name
    os_name="$(detect_os)"

    if [[ "$os_name" == "windows" ]]; then
        local install_dir_win="$INSTALL_DIR"
        if command -v cygpath >/dev/null 2>&1; then
            install_dir_win="$(cygpath -w "$INSTALL_DIR")"
        fi

        local launcher_path="$HOME/keygen.bat"
        cat > "$launcher_path" <<EOF
@echo off
setlocal
set "KEYGEN_HOME=$install_dir_win"
set "KEYGEN_LAUNCHER=keygen"
if exist "%KEYGEN_HOME%\.venv\Scripts\python.exe" (
    "%KEYGEN_HOME%\.venv\Scripts\python.exe" "%KEYGEN_HOME%\scripts\keygen.py" %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 "%KEYGEN_HOME%\scripts\keygen.py" %*
    ) else (
        python "%KEYGEN_HOME%\scripts\keygen.py" %*
    )
)
EOF

        say "已安装 Windows 管理命令：$launcher_path"
        if [[ "$TTY_INTERACTIVE" == "1" ]] && ask_yes_no "是否自动将 %USERPROFILE% 加入 PATH 以便在 CMD 直接运行 keygen？" 1; then
            if command -v powershell.exe >/dev/null 2>&1; then
                powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '$p=[Environment]::GetEnvironmentVariable("Path","User"); if([string]::IsNullOrWhiteSpace($p)){ $p="" }; if(-not (($p -split ";") -contains $env:USERPROFILE)){ [Environment]::SetEnvironmentVariable("Path", (($p.TrimEnd(";") + ";" + $env:USERPROFILE).Trim(";")), "User") }'
                say "PATH 已更新，请重新打开 CMD/PowerShell 后使用 keygen。"
            else
                say "未检测到 powershell.exe，请手动将 %USERPROFILE% 加入 PATH。"
            fi
        fi

        say "Windows 终端中可直接运行：keygen"
        return 0
    fi

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
        if [[ "$TTY_INTERACTIVE" == "1" ]] && ask_yes_no "是否自动写入 ~/.bashrc 和 ~/.zshrc 以加入 PATH？" 1; then
            for profile_file in "$HOME/.bashrc" "$HOME/.zshrc"; do
                touch "$profile_file"
                if ! grep -Fq "export PATH=\"$bin_dir:\$PATH\"" "$profile_file"; then
                    printf '\nexport PATH="%s:$PATH"\n' "$bin_dir" >> "$profile_file"
                fi
            done
            echo "已写入 PATH 配置，重新打开终端后可直接使用 keygen。"
        else
            echo "请执行：export PATH=\"$bin_dir:\$PATH\""
        fi
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
    if [[ "$TTY_INTERACTIVE" == "1" ]]; then
        return 0
    fi

    return 1
}

ensure_linux_mode_choice_arg() {
    if [[ "$(detect_os)" != "linux" ]]; then
        return 0
    fi

    if has_arg "--mode" "${EXTRA_ARGS[@]}"; then
        return 0
    fi

    if [[ "$TTY_INTERACTIVE" != "1" ]]; then
        say "Linux 环境下未显式指定 --mode，且当前非交互终端。"
        say "请显式指定：--mode local 或 --mode docker"
        exit 1
    fi

    local choice=""
    while true; do
        printf "Linux 安装模式请选择:\n1) 本地部署\n2) Docker 部署\n请输入选项 [1/2]: " > /dev/tty
        IFS= read -r choice < /dev/tty || true
        choice="${choice// /}"
        if [[ "$choice" == "1" ]]; then
            choice="local"
        elif [[ "$choice" == "2" ]]; then
            choice="docker"
        else
            choice="${choice,,}"
        fi

        if [[ "$choice" == "local" || "$choice" == "docker" ]]; then
            EXTRA_ARGS+=("--mode" "$choice")
            say "已选择 Linux 安装模式：$choice"
            return 0
        fi
        echo "请输入 1 或 2" > /dev/tty
    done
}

build_default_args() {
    local args=()

    if ! has_arg "--mode" "${EXTRA_ARGS[@]}"; then
        if [[ "$(detect_os)" == "linux" ]]; then
            # Linux 必须由用户显式选择 local/docker。
            :
        else
            args+=("--mode" "auto")
        fi
    fi

    if [[ "$ACTION" != "uninstall" ]] && has_arg "--non-interactive" "${args[@]}" "${EXTRA_ARGS[@]}"; then
        if [[ "$(detect_os)" == "linux" ]]; then
            args+=("--yes-install-docker")
        fi
    fi

    printf '%s\n' "${args[@]}"
}

main() {
    ensure_command "git" "git" "git"
    ensure_python_runtime

    ensure_repo
    cd "$INSTALL_DIR"

    if [[ "$ACTION" != "uninstall" ]]; then
        ensure_linux_mode_choice_arg
    fi

    ensure_python_runtime

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
