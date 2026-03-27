#!/usr/bin/env python3
"""
跨平台一键部署/升级/配置工具。

能力概览：
1. 智能推荐部署模式（按系统 + Docker 可用性）
2. 一键部署（Windows/macOS/Linux）
3. Linux 下支持 Docker / 本地二选一；缺 Docker 时可引导安装
4. 一键升级（git 拉取 + 按模式更新）
5. 一键卸载（按模式移除部署产物）
6. 交互式配置面板（端口、登录账号、密码等）
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
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
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
LOCAL_PID_PATH = DATA_DIR / "webui.pid"
LOCAL_STDOUT_LOG = LOGS_DIR / "webui.stdout.log"
LOCAL_STDERR_LOG = LOGS_DIR / "webui.stderr.log"
AUTOSTART_WINDOWS_NAME = "codex-keygen-webui.bat"
AUTOSTART_LINUX_SERVICE = "codex-keygen-webui.service"
AUTOSTART_MACOS_PLIST = "com.codex.keygen.webui.plist"


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
    host_port = int(config["port"])
    primary_url = f"http://127.0.0.1:{host_port}/login"
    spinner = ["|", "/", "-", "\\"]

    def _progress(attempt: int, elapsed_seconds: int, last_error: str) -> None:
        icon = spinner[(attempt - 1) % len(spinner)]
        message = f"\r健康检查中 {icon} 已等待 {elapsed_seconds}s（最长 {DEPLOY_HEALTH_TIMEOUT_SECONDS}s）"
        if last_error and elapsed_seconds > 0 and elapsed_seconds % 10 == 0:
            brief_error = last_error.replace("\n", " ")[:120]
            message += f" 最近错误: {brief_error}"
        print(message, end="", flush=True)

    ok, detail = wait_http_ready(primary_url, progress_callback=_progress)
    print()
    if ok:
        print(f"健康检查通过：{primary_url} ({detail})")
        return

    fallback_ok = False
    fallback_detail = ""
    if host_port != 1455:
        fallback_url = "http://127.0.0.1:1455/login"
        print(f"自定义端口 {host_port} 检查失败，追加诊断探测：{fallback_url}")
        fallback_ok, fallback_detail = wait_http_ready(fallback_url, timeout_seconds=15, interval_seconds=2)
        if fallback_ok:
            print(f"诊断结果：1455 可访问（{fallback_detail}），疑似应用仍监听默认端口。")
        elif fallback_detail:
            print(f"诊断结果：1455 不可访问（{fallback_detail}）。")

    print(f"健康检查失败：{primary_url}，原因：{detail}")
    diagnostics = print_docker_diagnostics(compose_cmd)

    hint = ""
    if "AttributeError: OUTLOOK" in diagnostics:
        hint = " 检测到旧版 OUTLOOK 枚举残留，请先执行 `git pull --ff-only` 更新到最新代码后重试。"

    if fallback_ok:
        hint += " 检测到 1455 可访问，疑似服务仍监听默认端口。"

    if host_port != 1455 and "0.0.0.0:1455->1455/tcp" in diagnostics and "启动 Web UI 在 http://0.0.0.0:1455" in diagnostics:
        hint += " 检测到应用实际监听 1455 端口但映射端口为自定义值，请拉取最新代码后重新部署。"

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
        print("非交互模式下将自动执行 git pull --ff-only，同步后继续部署。")
        pull_result = run_command(["git", "pull", "--ff-only"], check=False)
        if pull_result.returncode == 0:
            print("代码已自动更新。")
        else:
            print("git pull 失败，将继续使用当前代码部署。")
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


def install_local_dependencies(interactive: bool = True) -> None:
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
        if interactive and not ask_yes_no("未检测到 pip，是否自动安装 pip？", default_yes=True):
            raise DeployError("缺少 pip，且用户拒绝自动安装")

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
        if interactive and not ask_yes_no("依赖安装失败，是否自动修复并重试？", default_yes=True):
            raise DeployError("依赖安装失败，且用户取消自动修复")

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
    install_local_dependencies(interactive=interactive)
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


def remove_path_if_exists(path: Path) -> bool:
    """删除文件或目录（若存在），返回是否执行了删除。"""
    if not path.exists():
        return False

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def read_local_pid() -> Optional[int]:
    if not LOCAL_PID_PATH.exists():
        return None

    try:
        pid = int(str(LOCAL_PID_PATH.read_text(encoding="utf-8")).strip())
    except (OSError, ValueError):
        return None

    return pid if pid > 0 else None


def write_local_pid(pid: int) -> None:
    ensure_runtime_dirs()
    LOCAL_PID_PATH.write_text(f"{pid}\n", encoding="utf-8")


def clear_local_pid() -> None:
    if LOCAL_PID_PATH.exists():
        LOCAL_PID_PATH.unlink()


def is_local_process_running(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False

    if detect_os() == "windows":
        result = run_command_capture(["tasklist", "/FI", f"PID eq {pid}"], check=False)
        return result.returncode == 0 and str(pid) in (result.stdout or "")

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def build_webui_command(config: Dict[str, Any]) -> Sequence[str]:
    cfg = normalize_config(config)
    command = list(resolve_python_command()) + [
        "webui.py",
        "--host",
        str(cfg["host"]),
        "--port",
        str(cfg["port"]),
        "--access-username",
        str(cfg["access_username"]),
        "--access-password",
        str(cfg["access_password"]),
        "--log-level",
        str(cfg["log_level"]),
    ]

    if bool(cfg.get("debug")):
        command.append("--debug")

    return command


def start_local_service(config: Dict[str, Any]) -> None:
    ensure_runtime_dirs()
    existing_pid = read_local_pid()
    if is_local_process_running(existing_pid):
        print(f"本地服务已在运行（PID={existing_pid}）")
        return

    if existing_pid and not is_local_process_running(existing_pid):
        clear_local_pid()

    command = list(build_webui_command(config))
    print_section("启动本地服务")
    print(f"启动命令：{format_command(command)}")

    with LOCAL_STDOUT_LOG.open("ab") as stdout_file, LOCAL_STDERR_LOG.open("ab") as stderr_file:
        if detect_os() == "windows":
            creation_flags = 0
            creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creation_flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            process = subprocess.Popen(
                command,
                cwd=str(ROOT_DIR),
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creation_flags,
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT_DIR),
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )

    write_local_pid(process.pid)
    time.sleep(1.5)
    if not is_local_process_running(process.pid):
        clear_local_pid()
        raise DeployError("本地服务启动失败，请查看 logs/webui.stderr.log")

    cfg = normalize_config(config)
    print(f"本地服务启动成功（PID={process.pid}）")
    print(f"访问地址：http://127.0.0.1:{cfg['port']}")


def stop_local_service() -> None:
    print_section("停止本地服务")
    pid = read_local_pid()
    if not pid:
        print("未发现本地服务 PID 记录。")
        return

    if not is_local_process_running(pid):
        print(f"PID={pid} 已不在运行，清理 PID 记录。")
        clear_local_pid()
        return

    if detect_os() == "windows":
        run_command(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

        deadline = time.time() + 10
        while time.time() < deadline:
            if not is_local_process_running(pid):
                break
            time.sleep(0.5)

        if is_local_process_running(pid):
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError:
                pass

    clear_local_pid()
    print("本地服务已停止。")


def restart_local_service(config: Dict[str, Any]) -> None:
    stop_local_service()
    start_local_service(config)


def print_local_service_status(config: Dict[str, Any]) -> None:
    print_section("本地服务状态")
    pid = read_local_pid()
    running = is_local_process_running(pid)
    print(f"PID 文件：{LOCAL_PID_PATH}")
    print(f"当前 PID：{pid or '(无)'}")
    print(f"运行状态：{'运行中' if running else '未运行'}")
    print(f"stdout 日志：{LOCAL_STDOUT_LOG}")
    print(f"stderr 日志：{LOCAL_STDERR_LOG}")

    if running:
        cfg = normalize_config(config)
        url = f"http://127.0.0.1:{cfg['port']}/login"
        ok, detail = wait_http_ready(url, timeout_seconds=6, interval_seconds=2)
        if ok:
            print(f"健康检查：通过（{detail}）")
        else:
            print(f"健康检查：失败（{detail}）")


def resolve_compose_with_env() -> Sequence[str]:
    compose_cmd = resolve_compose_command()
    if compose_cmd is None:
        raise DeployError("未检测到 docker compose，请先安装 Docker")
    return list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH)]


def start_docker_service(config: Dict[str, Any]) -> None:
    print_section("启动 Docker 服务")
    sync_env_files(config)
    compose_cmd = list(resolve_compose_with_env())
    run_command(compose_cmd + ["up", "-d", "--build"])
    run_docker_health_check(config=config, compose_cmd=compose_cmd)
    print("Docker 服务启动成功。")


def stop_docker_service() -> None:
    print_section("停止 Docker 服务")
    compose_cmd = list(resolve_compose_with_env())
    run_command(compose_cmd + ["stop"], check=False)
    print("Docker 服务已停止。")


def restart_docker_service(config: Dict[str, Any]) -> None:
    print_section("重启 Docker 服务")
    sync_env_files(config)
    compose_cmd = list(resolve_compose_with_env())
    run_command(compose_cmd + ["restart"], check=False)
    run_docker_health_check(config=config, compose_cmd=compose_cmd)
    print("Docker 服务重启完成。")


def print_docker_service_status() -> None:
    print_section("Docker 服务状态")
    compose_cmd = list(resolve_compose_with_env())
    run_command_capture(compose_cmd + ["ps"], check=False)


def resolve_mode_for_operations(mode: str, config: Dict[str, Any], interactive: bool) -> str:
    if mode in {"docker", "local"}:
        return mode

    last_mode = str(config.get("last_deploy_mode") or "").strip().lower()
    if last_mode in {"docker", "local"}:
        if not interactive:
            return last_mode
        if ask_yes_no(f"检测到上次部署模式为 {last_mode}，是否继续使用该模式？", default_yes=True):
            return last_mode

    return choose_mode("auto", config, interactive=interactive)


def do_start(mode: str, interactive: bool = True) -> None:
    config = load_config()
    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)

    if selected_mode == "docker":
        if detect_os() == "linux" and not docker_ready():
            installed = install_docker_on_linux(auto_yes=not interactive)
            if not installed:
                raise DeployError("Docker 不可用，无法启动 Docker 模式")
        start_docker_service(config)
    else:
        sync_env_files(config)
        create_local_launchers()
        start_local_service(config)

    config["last_deploy_mode"] = selected_mode
    save_config(config)


def do_stop(mode: str, interactive: bool = True) -> None:
    config = load_config()
    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)

    if selected_mode == "docker":
        stop_docker_service()
    else:
        stop_local_service()


def do_restart(mode: str, interactive: bool = True) -> None:
    config = load_config()
    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)

    if selected_mode == "docker":
        restart_docker_service(config)
    else:
        sync_env_files(config)
        create_local_launchers()
        restart_local_service(config)

    config["last_deploy_mode"] = selected_mode
    save_config(config)


def do_status(mode: str, interactive: bool = False) -> None:
    config = load_config()

    if mode == "auto" and not interactive:
        print_section("当前模式推断")
        print(f"上次部署模式：{config.get('last_deploy_mode') or '(未记录)'}")
        print_local_service_status(config)
        if docker_ready():
            print_docker_service_status()
        else:
            print("\nDocker 状态：未检测到 Docker 环境")
        return

    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)
    if selected_mode == "docker":
        print_docker_service_status()
    else:
        print_local_service_status(config)


def _linux_autostart_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / AUTOSTART_LINUX_SERVICE


def _macos_autostart_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / AUTOSTART_MACOS_PLIST


def _windows_startup_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        raise DeployError("未检测到 APPDATA，无法设置 Windows 开机自启")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / AUTOSTART_WINDOWS_NAME


def _build_webui_command_text(config: Dict[str, Any]) -> str:
    command = list(build_webui_command(config))
    return format_command(command)


def enable_local_autostart(config: Dict[str, Any]) -> None:
    os_name = detect_os()

    if os_name == "windows":
        startup_path = _windows_startup_path()
        startup_path.parent.mkdir(parents=True, exist_ok=True)
        content = """@echo off
cd /d {root}
if exist "{launcher}" (
    call "{launcher}"
) else (
    if exist "{venv_python}" (
        start "" /min "{venv_python}" webui.py
    ) else (
        start "" /min python webui.py
    )
)
""".format(
            root=str(ROOT_DIR),
            launcher=str(ROOT_DIR / "start-local.bat"),
            venv_python=str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"),
        )
        startup_path.write_text(content, encoding="utf-8")
        print(f"Windows 开机自启已开启：{startup_path}")
        return

    if os_name == "linux":
        service_path = _linux_autostart_service_path()
        service_path.parent.mkdir(parents=True, exist_ok=True)
        command_text = _build_webui_command_text(config)
        service_content = f"""[Unit]
Description=Codex Keygen WebUI
After=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT_DIR}
ExecStart=/bin/bash -lc '{command_text}'
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
        service_path.write_text(service_content, encoding="utf-8")
        run_command(["systemctl", "--user", "daemon-reload"], check=False)
        run_command(["systemctl", "--user", "enable", AUTOSTART_LINUX_SERVICE], check=False)
        run_command(["systemctl", "--user", "restart", AUTOSTART_LINUX_SERVICE], check=False)
        print(f"Linux 开机自启已开启：{service_path}")
        print("提示：若重启后用户服务不启动，请执行 `loginctl enable-linger $USER`")
        return

    if os_name == "macos":
        plist_path = _macos_autostart_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        command = list(build_webui_command(config))
        args_xml = "\n".join([f"        <string>{item}</string>" for item in command])
        plist_content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
    <key>Label</key>
    <string>com.codex.keygen.webui</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{ROOT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOCAL_STDOUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>{LOCAL_STDERR_LOG}</string>
</dict>
</plist>
"""
        plist_path.write_text(plist_content, encoding="utf-8")
        run_command(["launchctl", "unload", str(plist_path)], check=False)
        run_command(["launchctl", "load", str(plist_path)], check=False)
        print(f"macOS 开机自启已开启：{plist_path}")
        return

    raise DeployError(f"当前系统不支持开机自启设置：{os_name}")


def disable_local_autostart() -> None:
    os_name = detect_os()

    if os_name == "windows":
        startup_path = _windows_startup_path()
        if remove_path_if_exists(startup_path):
            print(f"Windows 开机自启已关闭：{startup_path}")
        else:
            print("Windows 开机自启未设置。")
        return

    if os_name == "linux":
        service_path = _linux_autostart_service_path()
        run_command(["systemctl", "--user", "disable", "--now", AUTOSTART_LINUX_SERVICE], check=False)
        remove_path_if_exists(service_path)
        print(f"Linux 开机自启已关闭：{service_path}")
        return

    if os_name == "macos":
        plist_path = _macos_autostart_plist_path()
        run_command(["launchctl", "unload", str(plist_path)], check=False)
        remove_path_if_exists(plist_path)
        print(f"macOS 开机自启已关闭：{plist_path}")
        return

    raise DeployError(f"当前系统不支持开机自启设置：{os_name}")


def enable_docker_autostart() -> None:
    compose_cmd = list(resolve_compose_with_env())
    result = run_command_capture(compose_cmd + ["ps", "-q", "webui"], check=False)
    container_id = str(result.stdout or "").strip()
    if container_id:
        run_command(["docker", "update", "--restart=unless-stopped", container_id], check=False)
    print("Docker 开机自启已开启（restart=unless-stopped）。")


def disable_docker_autostart() -> None:
    compose_cmd = list(resolve_compose_with_env())
    result = run_command_capture(compose_cmd + ["ps", "-q", "webui"], check=False)
    container_id = str(result.stdout or "").strip()
    if not container_id:
        print("未检测到运行中的 Docker 容器，无法关闭重启策略。")
        return
    run_command(["docker", "update", "--restart=no", container_id], check=False)
    print("Docker 开机自启已关闭（restart=no）。")


def do_enable_autostart(mode: str, interactive: bool = True) -> None:
    config = load_config()
    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)

    if selected_mode == "docker":
        enable_docker_autostart()
    else:
        sync_env_files(config)
        create_local_launchers()
        ensure_runtime_dirs()
        enable_local_autostart(config)


def do_disable_autostart(mode: str, interactive: bool = True) -> None:
    config = load_config()
    selected_mode = resolve_mode_for_operations(mode, config, interactive=interactive)

    if selected_mode == "docker":
        disable_docker_autostart()
    else:
        disable_local_autostart()


def print_paths_info() -> None:
    config = load_config()
    print_section("文件目录信息")
    print(f"项目根目录          : {ROOT_DIR}")
    print(f"配置文件            : {RUNTIME_CONFIG_PATH} ({'存在' if RUNTIME_CONFIG_PATH.exists() else '不存在'})")
    print(f"本地环境变量文件     : {DOTENV_PATH} ({'存在' if DOTENV_PATH.exists() else '不存在'})")
    print(f"Docker 环境变量文件  : {DOCKER_ENV_PATH} ({'存在' if DOCKER_ENV_PATH.exists() else '不存在'})")
    print(f"数据目录            : {DATA_DIR} ({'存在' if DATA_DIR.exists() else '不存在'})")
    print(f"日志目录            : {LOGS_DIR} ({'存在' if LOGS_DIR.exists() else '不存在'})")
    print(f"本地 PID 文件       : {LOCAL_PID_PATH} ({'存在' if LOCAL_PID_PATH.exists() else '不存在'})")
    print(f"最近部署模式         : {config.get('last_deploy_mode') or '(未记录)'}")


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


def do_upgrade(mode: str, interactive: bool = True) -> None:
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
        selected_mode = choose_mode("auto", config, interactive=interactive)

    if selected_mode == "docker":
        compose_cmd = resolve_compose_command()
        if compose_cmd is None:
            raise DeployError("升级失败：未检测到 docker compose")
        sync_env_files(config)
        run_command(list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "pull"], check=False)
        run_command(list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "up", "-d", "--build"])
        print("Docker 模式升级完成。")
    else:
        install_local_dependencies(interactive=interactive)
        sync_env_files(config)
        print("本地模式升级完成。请重启正在运行的 WebUI 进程。")


def do_uninstall(mode: str, purge: bool = False, interactive: bool = True) -> None:
    """按模式卸载部署产物。"""
    config = load_config()
    print_section("一键卸载")

    selected_mode = mode
    if selected_mode == "auto":
        selected_mode = choose_mode("auto", config, interactive=interactive)

    if selected_mode == "docker":
        compose_cmd = resolve_compose_command()
        if compose_cmd is None:
            raise DeployError("卸载失败：未检测到 docker compose")

        sync_env_files(config)
        down_cmd = list(compose_cmd) + ["--env-file", str(DOCKER_ENV_PATH), "down", "--remove-orphans"]
        if purge:
            down_cmd.append("-v")
        run_command(down_cmd, check=False)

        if purge:
            run_command(["docker", "image", "prune", "-f"], check=False)

        print("Docker 模式卸载完成。")
        print("- 已执行 compose down --remove-orphans")
        if purge:
            print("- 已额外清理 volumes 与悬空镜像")
        return

    # local 模式
    print_section("本地卸载")
    removed_any = False
    for launcher_path in [ROOT_DIR / "start-local.sh", ROOT_DIR / "start-local.bat"]:
        if remove_path_if_exists(launcher_path):
            print(f"已删除启动脚本：{launcher_path.name}")
            removed_any = True

    if purge:
        for extra_path in [ROOT_DIR / ".venv", DOTENV_PATH, DOCKER_ENV_PATH]:
            if remove_path_if_exists(extra_path):
                print(f"已清理：{extra_path.name}")
                removed_any = True

    if not removed_any:
        print("未发现可清理的本地产物。")
    else:
        print("本地模式卸载完成。")

    print("提示：默认不会删除 data/ 与 runtime-config.json。")
    if purge:
        print("提示：已执行 --purge，环境文件与虚拟环境已清理。")


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
        print("1) 安装/更新（同一命令）")
        print("2) 更新（兼容入口）")
        print("3) 卸载")
        print("4) 查看配置")
        print("5) 修改监听地址")
        print("6) 修改端口")
        print("7) 修改登录账号")
        print("8) 修改登录密码")
        print("9) 启动服务")
        print("10) 停止服务")
        print("11) 重启服务")
        print("12) 查看服务状态")
        print("13) 设置开机自启")
        print("14) 关闭开机自启")
        print("15) 文件目录信息")
        print("16) 智能推荐")
        print("17) 进入高级配置面板")
        print("0) 退出")
        choice = input("请选择：").strip()

        if choice == "1":
            do_deploy(mode="auto", interactive=True, auto_yes_install_docker=False)
        elif choice == "2":
            do_upgrade(mode="auto", interactive=True)
        elif choice == "3":
            purge = ask_yes_no("是否执行深度清理（--purge）？", default_yes=False)
            do_uninstall(mode="auto", purge=purge, interactive=True)
        elif choice == "4":
            print_config(load_config())
        elif choice == "5":
            config = load_config()
            config["host"] = ask_text("监听地址", str(config.get("host", DEFAULT_CONFIG["host"])))
            save_config(config)
            sync_env_files(config)
            print("监听地址已更新。")
        elif choice == "6":
            config = load_config()
            config["port"] = ask_int("端口", int(config.get("port", DEFAULT_CONFIG["port"])))
            save_config(config)
            sync_env_files(config)
            print("端口已更新。")
        elif choice == "7":
            config = load_config()
            config["access_username"] = ask_text("登录账号", str(config.get("access_username", DEFAULT_CONFIG["access_username"])))
            save_config(config)
            sync_env_files(config)
            print("登录账号已更新。")
        elif choice == "8":
            config = load_config()
            config["access_password"] = ask_password("登录密码", str(config.get("access_password", DEFAULT_CONFIG["access_password"])))
            save_config(config)
            sync_env_files(config)
            print("登录密码已更新。")
        elif choice == "9":
            do_start(mode="auto", interactive=True)
        elif choice == "10":
            do_stop(mode="auto", interactive=True)
        elif choice == "11":
            do_restart(mode="auto", interactive=True)
        elif choice == "12":
            do_status(mode="auto", interactive=False)
        elif choice == "13":
            do_enable_autostart(mode="auto", interactive=True)
        elif choice == "14":
            do_disable_autostart(mode="auto", interactive=True)
        elif choice == "15":
            print_paths_info()
        elif choice == "16":
            print_recommendation()
        elif choice == "17":
            config_panel()
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
    upgrade_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    uninstall_parser = subparsers.add_parser("uninstall", help="一键卸载")
    uninstall_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="卸载模式")
    uninstall_parser.add_argument("--purge", action="store_true", help="深度清理（删除 .venv/.env/.env.docker；Docker 模式清理卷）")
    uninstall_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    start_parser = subparsers.add_parser("start", help="启动服务")
    start_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    start_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    stop_parser = subparsers.add_parser("stop", help="停止服务")
    stop_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    stop_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    restart_parser = subparsers.add_parser("restart", help="重启服务")
    restart_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    restart_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    status_parser = subparsers.add_parser("status", help="查看服务状态")
    status_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    status_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    autostart_on_parser = subparsers.add_parser("autostart-on", help="开启开机自启")
    autostart_on_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    autostart_on_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    autostart_off_parser = subparsers.add_parser("autostart-off", help="关闭开机自启")
    autostart_off_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="服务模式")
    autostart_off_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    subparsers.add_parser("info", help="显示文件目录信息")

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
            do_upgrade(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "uninstall":
            do_uninstall(mode=args.mode, purge=args.purge, interactive=not args.non_interactive)
            return

        if args.command == "start":
            do_start(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "stop":
            do_stop(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "restart":
            do_restart(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "status":
            do_status(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "autostart-on":
            do_enable_autostart(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "autostart-off":
            do_disable_autostart(mode=args.mode, interactive=not args.non_interactive)
            return

        if args.command == "info":
            print_paths_info()
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
