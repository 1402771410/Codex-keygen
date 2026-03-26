#!/usr/bin/env python3
"""
跨平台一键部署/升级/配置工具。

能力概览：
1. 智能推荐部署模式（按系统 + Docker 可用性）
2. 一键部署（Windows/macOS/Linux）
3. Linux 下支持 Docker / 本地二选一；缺 Docker 时可引导安装
4. 一键升级（git 拉取 + 按模式更新）
5. 交互式配置面板（端口、登录账号、密码等）
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request


ROOT_DIR = Path(__file__).resolve().parent.parent
RUNTIME_CONFIG_PATH = ROOT_DIR / "runtime-config.json"
DOTENV_PATH = ROOT_DIR / ".env"
DOCKER_ENV_PATH = ROOT_DIR / ".env.docker"
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"


DEFAULT_CONFIG: Dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 1455,
    "access_username": "admin",
    "access_password": "admin123",
    "debug": False,
    "log_level": "info",
    "linux_preferred_mode": "auto",
    "last_deploy_mode": "",
    "updated_at": "",
}

DEPLOY_HEALTH_TIMEOUT_SECONDS = 60
DEPLOY_HEALTH_INTERVAL_SECONDS = 2


class DeployError(RuntimeError):
    """部署异常。"""


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def format_command(command: Sequence[str]) -> str:
    chunks = []
    for item in command:
        if " " in item or "\t" in item:
            chunks.append(f'"{item}"')
        else:
            chunks.append(item)
    return " ".join(chunks)


def run_command(command: Sequence[str], check: bool = True, cwd: Path = ROOT_DIR) -> subprocess.CompletedProcess:
    print(f"[执行] {format_command(command)}")
    result = subprocess.run(command, cwd=str(cwd), check=False)
    if check and result.returncode != 0:
        raise DeployError(f"命令执行失败（退出码 {result.returncode}）：{format_command(command)}")
    return result


def run_command_capture(command: Sequence[str], check: bool = True, cwd: Path = ROOT_DIR) -> subprocess.CompletedProcess:
    print(f"[执行] {format_command(command)}")
    result = subprocess.run(command, cwd=str(cwd), check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    if check and result.returncode != 0:
        raise DeployError(f"命令执行失败（退出码 {result.returncode}）：{format_command(command)}")
    return result


def snapshot_files(paths: Sequence[Path]) -> Dict[Path, Optional[bytes]]:
    snapshots: Dict[Path, Optional[bytes]] = {}
    for path in paths:
        if path.exists():
            snapshots[path] = path.read_bytes()
        else:
            snapshots[path] = None
    return snapshots


def restore_snapshots(snapshots: Dict[Path, Optional[bytes]]) -> None:
    for path, content in snapshots.items():
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def detect_os() -> str:
    system_name = platform.system().lower()
    if system_name == "windows":
        return "windows"
    if system_name == "darwin":
        return "macos"
    if system_name == "linux":
        return "linux"
    return system_name


def resolve_python_command() -> Sequence[str]:
    venv_windows = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_windows.exists():
        return [str(venv_windows)]

    venv_unix = ROOT_DIR / ".venv" / "bin" / "python"
    if venv_unix.exists():
        return [str(venv_unix)]

    if sys.executable:
        return [sys.executable]
    if command_exists("python3"):
        return ["python3"]
    if command_exists("python"):
        return ["python"]
    if detect_os() == "windows" and command_exists("py"):
        return ["py", "-3"]
    raise DeployError("未检测到 Python 解释器，请先安装 Python 3.10+")


def resolve_compose_command() -> Optional[Sequence[str]]:
    if command_exists("docker"):
        probe = subprocess.run(["docker", "compose", "version"], check=False, capture_output=True, text=True)
        if probe.returncode == 0:
            return ["docker", "compose"]
    if command_exists("docker-compose"):
        probe = subprocess.run(["docker-compose", "version"], check=False, capture_output=True, text=True)
        if probe.returncode == 0:
            return ["docker-compose"]
    return None


def docker_ready() -> bool:
    return command_exists("docker") and resolve_compose_command() is not None


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})

    try:
        cfg["port"] = int(cfg.get("port", DEFAULT_CONFIG["port"]))
    except (TypeError, ValueError):
        cfg["port"] = int(DEFAULT_CONFIG["port"])

    cfg["host"] = str(cfg.get("host") or DEFAULT_CONFIG["host"]).strip() or str(DEFAULT_CONFIG["host"])
    cfg["access_username"] = str(cfg.get("access_username") or DEFAULT_CONFIG["access_username"]).strip() or str(
        DEFAULT_CONFIG["access_username"]
    )
    cfg["access_password"] = str(cfg.get("access_password") or DEFAULT_CONFIG["access_password"])
    cfg["debug"] = bool(cfg.get("debug", DEFAULT_CONFIG["debug"]))
    cfg["log_level"] = str(cfg.get("log_level") or DEFAULT_CONFIG["log_level"]).strip() or str(DEFAULT_CONFIG["log_level"])

    preferred = str(cfg.get("linux_preferred_mode") or "auto").lower()
    if preferred not in {"auto", "docker", "local"}:
        preferred = "auto"
    cfg["linux_preferred_mode"] = preferred

    last_mode = str(cfg.get("last_deploy_mode") or "").lower()
    if last_mode not in {"", "docker", "local"}:
        last_mode = ""
    cfg["last_deploy_mode"] = last_mode

    cfg["updated_at"] = str(cfg.get("updated_at") or "")
    return cfg


def load_config() -> Dict[str, Any]:
    if not RUNTIME_CONFIG_PATH.exists():
        save_config(dict(DEFAULT_CONFIG))
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DeployError(f"读取 runtime-config.json 失败：{exc}") from exc

    if not isinstance(data, dict):
        raise DeployError("runtime-config.json 格式错误：根节点必须是对象")

    return normalize_config(data)


def save_config(config: Dict[str, Any]) -> None:
    cfg = normalize_config(config)
    cfg["updated_at"] = now_text()
    RUNTIME_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def env_encode(value: Any) -> str:
    text = str(value)
    if not text:
        return ""
    if any(ch in text for ch in [" ", "#", "\"", "'", "\t"]):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f'"{escaped}"'
    return text


def sync_env_files(config: Dict[str, Any]) -> None:
    cfg = normalize_config(config)

    dotenv_lines = [
        "# 由 scripts/deploy_manager.py 自动生成",
        f"APP_HOST={env_encode(cfg['host'])}",
        f"APP_PORT={env_encode(cfg['port'])}",
        f"APP_ACCESS_USERNAME={env_encode(cfg['access_username'])}",
        f"APP_ACCESS_PASSWORD={env_encode(cfg['access_password'])}",
        f"WEBUI_HOST={env_encode(cfg['host'])}",
        f"WEBUI_PORT={env_encode(cfg['port'])}",
        f"WEBUI_ACCESS_USERNAME={env_encode(cfg['access_username'])}",
        f"WEBUI_ACCESS_PASSWORD={env_encode(cfg['access_password'])}",
        f"DEBUG={'1' if cfg['debug'] else '0'}",
        f"LOG_LEVEL={env_encode(cfg['log_level'])}",
    ]

    docker_lines = [
        "# 由 scripts/deploy_manager.py 自动生成",
        f"HOST_PORT={env_encode(cfg['port'])}",
        f"WEBUI_HOST={env_encode(cfg['host'])}",
        f"WEBUI_PORT={env_encode(cfg['port'])}",
        f"WEBUI_ACCESS_USERNAME={env_encode(cfg['access_username'])}",
        f"WEBUI_ACCESS_PASSWORD={env_encode(cfg['access_password'])}",
        f"DEBUG={'1' if cfg['debug'] else '0'}",
        f"LOG_LEVEL={env_encode(cfg['log_level'])}",
    ]

    DOTENV_PATH.write_text("\n".join(dotenv_lines) + "\n", encoding="utf-8")
    DOCKER_ENV_PATH.write_text("\n".join(docker_lines) + "\n", encoding="utf-8")


def ask_text(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]：").strip()
    return raw if raw else default


def mask_secret(secret: str) -> str:
    if not secret:
        return "(未设置)"
    if len(secret) <= 2:
        return "*" * len(secret)
    return secret[0] + ("*" * (len(secret) - 2)) + secret[-1]


def ask_password(prompt: str, current_value: str) -> str:
    notice = "（留空保持当前）" if current_value else ""
    raw = input(f"{prompt}{notice}：").strip()

    if raw:
        return raw
    return current_value


def ask_int(prompt: str, default: int, minimum: int = 1, maximum: int = 65535) -> int:
    while True:
        raw = input(f"{prompt} [{default}]：").strip()
        if not raw:
            return default
        try:
            value = int(raw)
            if minimum <= value <= maximum:
                return value
            print(f"请输入 {minimum}~{maximum} 之间的整数")
        except ValueError:
            print("请输入有效整数")


def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    suffix = "Y/n" if default_yes else "y/N"
    raw = input(f"{prompt} ({suffix})：").strip().lower()
    if not raw:
        return default_yes
    return raw in {"y", "yes", "1", "true"}


def update_config_from_prompt(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = normalize_config(config)
    print_section("采集部署参数")
    cfg["host"] = ask_text("监听地址", cfg["host"])
    cfg["port"] = ask_int("端口", int(cfg["port"]))
    cfg["access_username"] = ask_text("登录账号", cfg["access_username"])
    cfg["access_password"] = ask_password("登录密码", cfg["access_password"])
    cfg["debug"] = ask_yes_no("是否启用调试模式", bool(cfg["debug"]))
    cfg["log_level"] = ask_text("日志级别", str(cfg["log_level"]))
    return cfg


def recommendation(config: Dict[str, Any]) -> Tuple[str, str]:
    os_name = detect_os()
    has_docker = docker_ready()
    if os_name == "linux":
        preferred = str(config.get("linux_preferred_mode") or "auto")
        if preferred in {"docker", "local"}:
            return preferred, f"Linux 已设置优先模式：{preferred}"
        if has_docker:
            return "docker", "Linux 检测到 Docker 环境，推荐 Docker 部署"
        return "local", "Linux 未检测到 Docker，推荐本地部署"

    if os_name in {"windows", "macos"}:
        if has_docker:
            return "docker", f"{os_name} 检测到 Docker，推荐 Docker（隔离更好）"
        return "local", f"{os_name} 未检测到 Docker，推荐本地部署"

    return "local", "未知系统，推荐本地部署"


def wait_http_ready(
    url: str,
    timeout_seconds: int = DEPLOY_HEALTH_TIMEOUT_SECONDS,
    interval_seconds: int = DEPLOY_HEALTH_INTERVAL_SECONDS,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_error = ""
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        try:
            req = urllib_request.Request(url=url, method="GET")
            with urllib_request.urlopen(req, timeout=5) as response:
                status_code = response.getcode()
                if 200 <= status_code < 400:
                    return True, f"HTTP {status_code}"
        except urllib_error.URLError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

        if progress_callback is not None:
            elapsed_seconds = int(timeout_seconds - max(0, deadline - time.time()))
            progress_callback(attempts, elapsed_seconds, last_error)

        time.sleep(interval_seconds)

    if not last_error:
        last_error = "请求超时"
    return False, last_error


def run_local_preflight() -> None:
    print_section("本地部署健康检查")
    python_cmd = list(resolve_python_command())
    run_command(
        python_cmd
        + [
            "-c",
            "import webui; from src.web.app import create_app; app=create_app(); print(type(app).__name__)",
        ]
    )
    print("本地预检查通过：应用可导入且 FastAPI 实例可构建。")


def print_docker_diagnostics(compose_cmd: Sequence[str]) -> str:
    print_section("Docker 失败诊断")
    ps_result = run_command_capture(list(compose_cmd) + ["ps", "-a"], check=False)
    container_result = run_command_capture(list(compose_cmd) + ["ps", "-q", "webui"], check=False)
    container_id = (container_result.stdout or "").strip()

    if container_id:
        run_command_capture(
            [
                "docker",
                "inspect",
                "-f",
                "State={{.State.Status}} RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}} Error={{.State.Error}}",
                container_id,
            ],
            check=False,
        )
        run_command_capture(
            ["docker", "inspect", "-f", "Ports={{json .NetworkSettings.Ports}}", container_id],
            check=False,
        )

    logs_result = run_command_capture(list(compose_cmd) + ["logs", "--tail", "120"], check=False)
    merged_output = "\n".join(
        [
            ps_result.stdout or "",
            ps_result.stderr or "",
            logs_result.stdout or "",
            logs_result.stderr or "",
        ]
    )
    return merged_output


def run_docker_health_check(config: Dict[str, Any], compose_cmd: Sequence[str]) -> None:
    print_section("Docker 部署健康检查")
    url = f"http://127.0.0.1:{config['port']}/login"
    spinner = ["|", "/", "-", "\\"]

    def _progress(attempt: int, elapsed_seconds: int, last_error: str) -> None:
        icon = spinner[(attempt - 1) % len(spinner)]
        message = f"\r健康检查中 {icon} 已等待 {elapsed_seconds}s（最长 {DEPLOY_HEALTH_TIMEOUT_SECONDS}s）"
        if last_error and elapsed_seconds > 0 and elapsed_seconds % 10 == 0:
            brief_error = last_error.replace("\n", " ")[:120]
            message += f" 最近错误: {brief_error}"
        print(message, end="", flush=True)

    ok, detail = wait_http_ready(url, progress_callback=_progress)
    print()
    if ok:
        print(f"健康检查通过：{url} ({detail})")
        return

    print(f"健康检查失败：{url}，原因：{detail}")
    diagnostics = print_docker_diagnostics(compose_cmd)

    hint = ""
    if "AttributeError: OUTLOOK" in diagnostics:
        hint = " 检测到旧版 OUTLOOK 枚举残留，请先执行 `git pull --ff-only` 更新到最新代码后重试。"

    raise DeployError(f"Docker 服务启动后未通过健康检查，请根据上方日志排查。{hint}")


def choose_mode(mode_arg: str, config: Dict[str, Any], interactive: bool) -> str:
    if mode_arg in {"docker", "local"}:
        return mode_arg

    rec_mode, rec_reason = recommendation(config)
    print_section("智能推荐")
    print(f"推荐模式：{rec_mode}")
    print(f"推荐原因：{rec_reason}")

    if not interactive:
        return rec_mode

    os_name = detect_os()
    if os_name == "linux":
        print("\nLinux 可选模式：")
        print("1) Docker 部署")
        print("2) 本地部署")
        default_choice = "1" if rec_mode == "docker" else "2"
        choice = input(f"请选择部署模式 [{default_choice}]：").strip() or default_choice
        return "docker" if choice == "1" else "local"

    return rec_mode


def maybe_sync_repo_before_deploy(interactive: bool) -> None:
    if not command_exists("git"):
        return
    if not (ROOT_DIR / ".git").exists():
        return

    run_command(["git", "fetch", "--all", "--prune"], check=False)
    status_result = run_command_capture(["git", "status", "-sb"], check=False)
    first_line = (status_result.stdout or "").splitlines()[0] if (status_result.stdout or "").splitlines() else ""

    if "[behind " not in first_line:
        return

    print("检测到当前目录代码落后远程分支，部署旧代码可能导致容器启动失败。")
    if not interactive:
        print("非交互模式下将继续使用当前代码部署。建议先执行：git pull --ff-only")
        return

    if ask_yes_no("是否现在自动执行 git pull --ff-only 后继续部署？", default_yes=True):
        pull_result = run_command(["git", "pull", "--ff-only"], check=False)
        if pull_result.returncode == 0:
            print("代码已更新，将继续部署。")
        else:
            print("git pull 失败，将继续使用当前代码部署。")


def sudo_prefix() -> Sequence[str]:
    if detect_os() != "linux":
        return []

    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid) and geteuid() == 0:
        return []

    if command_exists("sudo"):
        return ["sudo"]
    return []


def detect_linux_family() -> str:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return "unknown"

    data: Dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"')

    text = " ".join([data.get("ID", ""), data.get("ID_LIKE", "")]).lower()
    if any(word in text for word in ["debian", "ubuntu"]):
        return "debian"
    if any(word in text for word in ["rhel", "centos", "fedora", "rocky", "almalinux"]):
        return "rhel"
    if "arch" in text:
        return "arch"
    return "unknown"


def install_docker_on_linux(auto_yes: bool = False) -> bool:
    if detect_os() != "linux":
        return docker_ready()

    if docker_ready():
        print("Docker 环境已可用，跳过安装。")
        return True

    if not auto_yes:
        agreed = ask_yes_no("未检测到 Docker，是否自动安装 Docker 环境？", default_yes=True)
        if not agreed:
            print("用户拒绝安装 Docker，回退到本地部署。")
            return False

    family = detect_linux_family()
    prefix = list(sudo_prefix())

    print_section("安装 Docker 环境")
    print(f"检测到 Linux 发行版族：{family}")

    install_ok = False
    try:
        if family == "debian":
            run_command(prefix + ["apt-get", "update"])
            result = run_command(prefix + ["apt-get", "install", "-y", "docker.io", "docker-compose-plugin"], check=False)
            install_ok = result.returncode == 0
            if not install_ok:
                result = run_command(prefix + ["apt-get", "install", "-y", "docker.io", "docker-compose"], check=False)
                install_ok = result.returncode == 0
        elif family == "rhel":
            if command_exists("dnf"):
                result = run_command(prefix + ["dnf", "install", "-y", "docker", "docker-compose-plugin"], check=False)
                install_ok = result.returncode == 0
            else:
                result = run_command(prefix + ["yum", "install", "-y", "docker", "docker-compose-plugin"], check=False)
                install_ok = result.returncode == 0
        elif family == "arch":
            result = run_command(prefix + ["pacman", "-Sy", "--noconfirm", "docker", "docker-compose"], check=False)
            install_ok = result.returncode == 0

        if not install_ok:
            print("系统包安装失败，尝试官方安装脚本（get.docker.com）...")
            shell_cmd = "curl -fsSL https://get.docker.com | sh"
            if prefix:
                shell_cmd = f"{' '.join(prefix)} sh -c \"{shell_cmd}\""
            result = subprocess.run(shell_cmd, shell=True, cwd=str(ROOT_DIR), check=False)
            install_ok = result.returncode == 0

        if install_ok:
            run_command(prefix + ["systemctl", "enable", "--now", "docker"], check=False)
    except DeployError as exc:
        print(f"Docker 安装过程失败：{exc}")
        install_ok = False

    if not install_ok:
        print("Docker 安装失败，将使用本地部署。")
        return False

    if docker_ready():
        print("Docker 安装完成。")
        return True

    print("Docker 安装后仍不可用，将使用本地部署。")
    return False


def install_local_dependencies() -> None:
    print_section("安装本地依赖")
    python_cmd = list(resolve_python_command())

    if command_exists("uv"):
        uv_result = run_command(["uv", "sync"], check=False)
        if uv_result.returncode == 0:
            return
        print("uv sync 执行失败，自动回退到 pip 依赖安装流程。")

    pip_cmd = python_cmd + ["-m", "pip"]
    pip_check = run_command(pip_cmd + ["--version"], check=False)
    if pip_check.returncode != 0:
        print("未检测到 pip，尝试通过 ensurepip 自动安装...")
        ensurepip_result = run_command(python_cmd + ["-m", "ensurepip", "--upgrade"], check=False)
        if ensurepip_result.returncode != 0:
            raise DeployError("自动安装 pip 失败，请先安装 pip 后再执行 keygen install")

        pip_check = run_command(pip_cmd + ["--version"], check=False)
        if pip_check.returncode != 0:
            raise DeployError("pip 仍不可用，请检查 Python 运行环境")

    run_command(pip_cmd + ["install", "--upgrade", "pip", "setuptools", "wheel"], check=False)

    install_cmd = pip_cmd + ["install", "-r", str(REQUIREMENTS_PATH)] if REQUIREMENTS_PATH.exists() else pip_cmd + ["install", "-e", "."]
    install_result = run_command(install_cmd, check=False)

    if install_result.returncode != 0:
        print("依赖安装失败，已自动升级安装工具后重试一次...")
        run_command(pip_cmd + ["install", "--upgrade", "pip", "setuptools", "wheel"], check=False)
        run_command(install_cmd, check=True)


def create_local_launchers() -> None:
    sh_path = ROOT_DIR / "start-local.sh"
    bat_path = ROOT_DIR / "start-local.bat"

    sh_text = """#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"
cd \"$ROOT_DIR\"
if [ -x \"$ROOT_DIR/.venv/bin/python\" ]; then
  PY=\"$ROOT_DIR/.venv/bin/python\"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo \"未检测到 Python，请先安装 Python 3.10+\"
  exit 1
fi
\"$PY\" webui.py
"""

    bat_text = """@echo off
setlocal
cd /d %~dp0
if exist "%~dp0\\.venv\\Scripts\\python.exe" (
    "%~dp0\\.venv\\Scripts\\python.exe" webui.py
) else (
    python webui.py
)
"""

    sh_path.write_text(sh_text, encoding="utf-8")
    bat_path.write_text(bat_text, encoding="utf-8")

    if detect_os() != "windows":
        sh_path.chmod(0o755)


def deploy_local(config: Dict[str, Any], interactive: bool) -> None:
    print_section("本地部署")
    install_local_dependencies()
    sync_env_files(config)
    create_local_launchers()
    run_local_preflight()

    print("\n本地部署完成。")
    print(f"- 配置文件：{RUNTIME_CONFIG_PATH}")
    print(f"- 环境文件：{DOTENV_PATH}")
    print("- 启动命令：python webui.py")
    print("- 一键启动脚本：start-local.sh / start-local.bat")

    if interactive and ask_yes_no("是否立即启动 WebUI？", default_yes=False):
        python_cmd = list(resolve_python_command())
        run_command(python_cmd + ["webui.py"], check=True)


def deploy_docker(config: Dict[str, Any]) -> None:
    print_section("Docker 部署")
    compose_cmd = resolve_compose_command()
    if compose_cmd is None:
        raise DeployError("未检测到 docker compose，请先安装 Docker")

    sync_env_files(config)
    run_command(list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "up", "-d", "--build"])
    run_docker_health_check(config=config, compose_cmd=list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH)])

    print("\nDocker 部署完成。")
    print(f"- 配置文件：{RUNTIME_CONFIG_PATH}")
    print(f"- Docker 环境文件：{DOCKER_ENV_PATH}")
    print(f"- 访问地址：http://127.0.0.1:{config['port']}")


def do_deploy(
    mode: str,
    interactive: bool,
    auto_yes_install_docker: bool,
    config_override: Optional[Dict[str, Any]] = None,
) -> None:
    config = normalize_config(config_override) if config_override is not None else load_config()
    if interactive:
        config = update_config_from_prompt(config)

    maybe_sync_repo_before_deploy(interactive=interactive)

    env_snapshots = snapshot_files([DOTENV_PATH, DOCKER_ENV_PATH])

    selected_mode = choose_mode(mode, config, interactive=interactive)

    try:
        if selected_mode == "docker" and detect_os() == "linux" and not docker_ready():
            installed = install_docker_on_linux(auto_yes=auto_yes_install_docker)
            if not installed:
                selected_mode = "local"

        if selected_mode == "docker" and not docker_ready():
            if interactive and ask_yes_no("当前系统未检测到 Docker，可切换为本地部署，是否继续？", default_yes=True):
                selected_mode = "local"
            else:
                raise DeployError("Docker 不可用，部署中止")

        if selected_mode == "docker":
            deploy_docker(config)
        else:
            deploy_local(config, interactive=interactive)

        config["last_deploy_mode"] = selected_mode
        save_config(config)
    except Exception as exc:  # noqa: BLE001
        restore_snapshots(env_snapshots)
        raise DeployError(f"部署失败，已回滚 .env/.env.docker。原始错误：{exc}") from exc


def do_upgrade(mode: str) -> None:
    config = load_config()
    print_section("一键升级")

    if (ROOT_DIR / ".git").exists() and command_exists("git"):
        print("检测到 Git 仓库，尝试拉取最新代码...")
        run_command(["git", "fetch", "--all", "--prune"], check=False)
        pull_result = run_command(["git", "pull", "--ff-only"], check=False)
        if pull_result.returncode != 0:
            print("git pull 未成功（可能有本地改动），继续执行依赖/服务升级。")
    else:
        print("未检测到 Git 仓库，跳过代码拉取。")

    selected_mode = mode
    if selected_mode == "auto":
        selected_mode = config.get("last_deploy_mode") or recommendation(config)[0]

    if selected_mode == "docker":
        compose_cmd = resolve_compose_command()
        if compose_cmd is None:
            raise DeployError("升级失败：未检测到 docker compose")
        sync_env_files(config)
        run_command(list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "pull"], check=False)
        run_command(list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "up", "-d", "--build"])
        print("Docker 模式升级完成。")
    else:
        install_local_dependencies()
        sync_env_files(config)
        print("本地模式升级完成。请重启正在运行的 WebUI 进程。")


def print_config(config: Dict[str, Any]) -> None:
    cfg = normalize_config(config)
    print_section("当前配置")
    print(f"host               : {cfg['host']}")
    print(f"port               : {cfg['port']}")
    print(f"access_username    : {cfg['access_username']}")
    print(f"access_password    : {mask_secret(cfg['access_password'])}")
    print(f"debug              : {cfg['debug']}")
    print(f"log_level          : {cfg['log_level']}")
    print(f"linux_preferred    : {cfg['linux_preferred_mode']}")
    print(f"last_deploy_mode   : {cfg['last_deploy_mode']}")
    print(f"updated_at         : {cfg['updated_at']}")


def config_panel() -> None:
    config = load_config()

    while True:
        print_config(config)
        print("\n配置菜单：")
        print("1) 修改监听地址")
        print("2) 修改端口")
        print("3) 修改登录账号")
        print("4) 修改登录密码")
        print("5) 切换调试模式")
        print("6) 修改日志级别")
        print("7) 设置 Linux 优先部署模式")
        print("8) 保存并同步 .env/.env.docker")
        print("9) 保存并立即部署")
        print("0) 退出")

        choice = input("请选择：").strip()
        if choice == "1":
            config["host"] = ask_text("监听地址", str(config.get("host", DEFAULT_CONFIG["host"])))
        elif choice == "2":
            config["port"] = ask_int("端口", int(config.get("port", DEFAULT_CONFIG["port"])))
        elif choice == "3":
            config["access_username"] = ask_text("登录账号", str(config.get("access_username", DEFAULT_CONFIG["access_username"])))
        elif choice == "4":
            config["access_password"] = ask_password("登录密码", str(config.get("access_password", DEFAULT_CONFIG["access_password"])))
        elif choice == "5":
            config["debug"] = not bool(config.get("debug", False))
        elif choice == "6":
            config["log_level"] = ask_text("日志级别", str(config.get("log_level", DEFAULT_CONFIG["log_level"])))
        elif choice == "7":
            print("可选：auto / docker / local")
            raw = ask_text("Linux 优先部署模式", str(config.get("linux_preferred_mode", "auto"))).lower()
            config["linux_preferred_mode"] = raw if raw in {"auto", "docker", "local"} else "auto"
        elif choice == "8":
            save_config(config)
            sync_env_files(config)
            print("配置已保存并同步。")
        elif choice == "9":
            try:
                do_deploy(
                    mode="auto",
                    interactive=False,
                    auto_yes_install_docker=False,
                    config_override=config,
                )
                config = load_config()
            except DeployError as exc:
                print(f"保存并部署失败：{exc}")
        elif choice == "0":
            save_config(config)
            sync_env_files(config)
            print("已保存并退出。")
            break
        else:
            print("无效选项，请重试。")


def print_recommendation() -> None:
    config = load_config()
    mode, reason = recommendation(config)
    print_section("部署推荐")
    print(f"系统：{detect_os()}")
    print(f"Docker 可用：{'是' if docker_ready() else '否'}")
    print(f"推荐模式：{mode}")
    print(f"原因：{reason}")


def menu() -> None:
    while True:
        print("\n================ 部署管理菜单 ================")
        print("1) 一键部署")
        print("2) 一键升级")
        print("3) 配置面板")
        print("4) 智能推荐")
        print("0) 退出")
        choice = input("请选择：").strip()

        if choice == "1":
            do_deploy(mode="auto", interactive=True, auto_yes_install_docker=False)
        elif choice == "2":
            do_upgrade(mode="auto")
        elif choice == "3":
            config_panel()
        elif choice == "4":
            print_recommendation()
        elif choice == "0":
            print("已退出部署管理。")
            break
        else:
            print("无效选项，请重试。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="跨平台部署/升级/配置工具")
    subparsers = parser.add_subparsers(dest="command")

    deploy_parser = subparsers.add_parser("deploy", help="一键部署")
    deploy_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="部署模式")
    deploy_parser.add_argument("--non-interactive", action="store_true", help="非交互模式，使用现有配置")
    deploy_parser.add_argument(
        "--yes-install-docker",
        action="store_true",
        help="Linux 缺少 Docker 时自动同意安装",
    )

    upgrade_parser = subparsers.add_parser("upgrade", help="一键升级")
    upgrade_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="升级模式")

    subparsers.add_parser("config", help="打开配置面板")
    subparsers.add_parser("recommend", help="查看智能推荐")
    subparsers.add_parser("menu", help="打开主菜单")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command in {None, "menu"}:
            menu()
            return

        if args.command == "deploy":
            do_deploy(
                mode=args.mode,
                interactive=not args.non_interactive,
                auto_yes_install_docker=args.yes_install_docker,
            )
            return

        if args.command == "upgrade":
            do_upgrade(mode=args.mode)
            return

        if args.command == "config":
            config_panel()
            return

        if args.command == "recommend":
            print_recommendation()
            return

        parser.print_help()
    except DeployError as exc:
        print(f"[错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(130)


if __name__ == "__main__":
    main()
