#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨平台一键打包工具（Windows / macOS）。

说明：
1. 交互选择目标平台（Windows 或 macOS）
2. 自动调用 PyInstaller 生成可执行文件
3. 输出可直接运行的发布目录（含 runtime-config.json）
4. 登录账号/密码/端口统一在 runtime-config.json 中修改
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
RUNTIME_CONFIG_PATH = ROOT_DIR / "runtime-config.json"
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"


DEFAULT_RUNTIME_CONFIG: Dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 1455,
    "access_username": "admin",
    "access_password": "admin123",
    "debug": False,
    "log_level": "info",
}


class PackageError(RuntimeError):
    """打包异常。"""


def detect_os() -> str:
    system_name = platform.system().lower()
    if system_name == "windows":
        return "windows"
    if system_name == "darwin":
        return "macos"
    if system_name == "linux":
        return "linux"
    return system_name


def format_command(command: Sequence[str]) -> str:
    chunks = []
    for item in command:
        if " " in item:
            chunks.append(f'"{item}"')
        else:
            chunks.append(item)
    return " ".join(chunks)


def run_command(command: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"[执行] {format_command(command)}")
    result = subprocess.run(command, cwd=str(ROOT_DIR), check=False)
    if check and result.returncode != 0:
        raise PackageError(f"命令执行失败（退出码 {result.returncode}）：{format_command(command)}")
    return result


def check_pyinstaller_version(python_cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(python_cmd) + ["-m", "PyInstaller", "--version"],
        cwd=str(ROOT_DIR),
        check=False,
        capture_output=True,
        text=True,
    )


def resolve_python() -> Sequence[str]:
    if sys.executable:
        return [sys.executable]
    if shutil.which("python3"):
        return ["python3"]
    if shutil.which("python"):
        return ["python"]
    if detect_os() == "windows" and shutil.which("py"):
        return ["py", "-3"]
    raise PackageError("未检测到 Python 3.10+，无法打包")


def ensure_runtime_config() -> None:
    if RUNTIME_CONFIG_PATH.exists():
        return
    RUNTIME_CONFIG_PATH.write_text(json.dumps(DEFAULT_RUNTIME_CONFIG, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


def ensure_pip_available(python_cmd: Sequence[str]) -> List[str]:
    pip_cmd = list(python_cmd) + ["-m", "pip"]
    pip_check = run_command(pip_cmd + ["--version"], check=False)
    if pip_check.returncode == 0:
        return pip_cmd

    print("未检测到 pip，尝试通过 ensurepip 自动安装...")
    ensurepip_result = run_command(list(python_cmd) + ["-m", "ensurepip", "--upgrade"], check=False)
    if ensurepip_result.returncode != 0:
        raise PackageError("自动安装 pip 失败，请先安装 pip 后再执行打包")

    pip_check = run_command(pip_cmd + ["--version"], check=False)
    if pip_check.returncode != 0:
        raise PackageError("pip 仍不可用，请检查 Python 运行环境")

    return pip_cmd


def project_dependency_probe_ok(python_cmd: Sequence[str]) -> bool:
    probe = run_command(
        list(python_cmd)
        + [
            "-c",
            "import fastapi,uvicorn,jinja2,sqlalchemy,pydantic,pydantic_settings,curl_cffi,websockets",
        ],
        check=False,
    )
    return probe.returncode == 0


def ensure_project_dependencies(python_cmd: Sequence[str]) -> None:
    if project_dependency_probe_ok(python_cmd):
        return

    print("检测到项目依赖缺失，开始自动安装依赖...")
    pip_cmd = ensure_pip_available(python_cmd)
    run_command(pip_cmd + ["install", "--upgrade", "pip", "setuptools", "wheel"], check=False)

    install_cmd = pip_cmd + ["install", "-r", str(REQUIREMENTS_PATH)] if REQUIREMENTS_PATH.exists() else pip_cmd + ["install", "-e", "."]
    install_result = run_command(install_cmd, check=False)
    if install_result.returncode != 0:
        print("依赖安装失败，已自动升级安装工具后重试一次...")
        run_command(pip_cmd + ["install", "--upgrade", "pip", "setuptools", "wheel"], check=False)
        run_command(install_cmd, check=True)


def ensure_pyinstaller(python_cmd: Sequence[str]) -> List[str]:
    pip_cmd = ensure_pip_available(python_cmd)
    python_list = list(python_cmd)
    check = check_pyinstaller_version(python_list)
    if check.returncode == 0:
        return python_list

    print("未检测到 PyInstaller，开始自动安装...")
    run_command(pip_cmd + ["install", "--upgrade", "pip", "wheel"], check=False)
    run_command(pip_cmd + ["install", "setuptools<81"], check=False)
    run_command(pip_cmd + ["install", "pyinstaller"], check=False)

    verify = check_pyinstaller_version(python_list)
    if verify.returncode == 0:
        return python_list

    print("检测到 PyInstaller 运行环境异常，尝试安装兼容依赖组合...")
    run_command(
        pip_cmd + ["install", "--force-reinstall", "setuptools<81", "altgraph<0.18", "pyinstaller<7"],
        check=False,
    )
    verify = check_pyinstaller_version(python_list)
    if verify.returncode != 0:
        stderr = (verify.stderr or "").strip()
        stdout = (verify.stdout or "").strip()
        details = stderr or stdout or "无可用错误日志"
        raise PackageError(f"PyInstaller 初始化失败，请检查 Python 环境后重试。详情: {details}")

    return python_list


def pick_target(target_arg: str) -> str:
    if target_arg == "auto":
        host = detect_os()
        if host in {"windows", "macos"}:
            return host
        raise PackageError("当前系统不是 Windows/macOS，auto 模式不可用；请在目标系统（Windows 或 macOS）执行打包")

    if target_arg in {"windows", "macos"}:
        return target_arg

    host = detect_os()
    if host == "windows":
        print("当前系统：Windows，仅支持打包 Windows")
        print("1) Windows")
        _ = input("按回车继续 [1]：").strip()
        return "windows"

    if host == "macos":
        print("当前系统：macOS，仅支持打包 macOS")
        print("1) macOS")
        _ = input("按回车继续 [1]：").strip()
        return "macos"

    print("请选择打包目标：")
    print("1) Windows")
    print("2) macOS")
    choice = input("输入序号 [1]：").strip() or "1"
    return "windows" if choice == "1" else "macos"


def validate_host_support(target: str) -> None:
    host = detect_os()
    if target == "windows" and host != "windows":
        raise PackageError("Windows 包需要在 Windows 系统执行打包命令")
    if target == "macos" and host != "macos":
        raise PackageError("macOS 包需要在 macOS 系统执行打包命令")


def resolve_release_root(output_dir_arg: Optional[str], interactive: bool) -> Path:
    default_root = DIST_DIR / "releases"

    selected = (output_dir_arg or "").strip()
    if interactive and not selected:
        selected = input("输入发布目录（默认 dist/releases）：").strip()

    if not selected:
        return default_root

    candidate = Path(selected).expanduser()
    if not candidate.is_absolute():
        candidate = (ROOT_DIR / candidate).resolve()
    return candidate


def build_pyinstaller(target: str, clean: bool, dry_run: bool) -> Path:
    if dry_run:
        python_cmd = list(resolve_python())
    else:
        python_cmd = list(resolve_python())
        ensure_project_dependencies(python_cmd)
        python_cmd = list(ensure_pyinstaller(python_cmd))

    build_name = "codex-keygen-win" if target == "windows" else "codex-keygen-macos"
    sep = ";" if os.name == "nt" else ":"
    add_data_items = [
        f"{ROOT_DIR / 'templates'}{sep}templates",
        f"{ROOT_DIR / 'static'}{sep}static",
    ]

    command = python_cmd + [
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        build_name,
        "--hidden-import",
        "uvicorn",
        "--hidden-import",
        "uvicorn.config",
        "--hidden-import",
        "uvicorn.main",
        "--hidden-import",
        "websockets",
    ]
    if clean:
        command.append("--clean")

    for entry in add_data_items:
        command.extend(["--add-data", entry])

    command.append(str(ROOT_DIR / "webui.py"))

    if dry_run:
        print("[Dry Run] 将执行：")
        print(format_command(command))
    else:
        run_command(command)

    executable_name = f"{build_name}.exe" if target == "windows" else build_name
    return DIST_DIR / executable_name


def create_release(target: str, built_file: Path, dry_run: bool, release_root: Path) -> tuple[Path, Path]:
    release_dir = release_root / target
    final_name = "codex-keygen.exe" if target == "windows" else "codex-keygen"
    final_path = release_dir / final_name

    if dry_run:
        print(f"[Dry Run] 将创建发布目录：{release_dir}")
        print(f"[Dry Run] 将复制：{built_file} -> {final_path}")
        return release_dir, final_path

    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    if not built_file.exists():
        raise PackageError(f"未找到构建产物：{built_file}")

    shutil.copy2(built_file, final_path)

    ensure_runtime_config()
    shutil.copy2(RUNTIME_CONFIG_PATH, release_dir / "runtime-config.json")

    if target == "windows":
        launcher = release_dir / "start.bat"
        launcher.write_text(
            "@echo off\n"
            "setlocal\n"
            "cd /d %~dp0\n"
            "set LOG_FILE=startup-error.log\n"
            "codex-keygen.exe 1>>startup.log 2>>%LOG_FILE%\n"
            "set EXIT_CODE=%ERRORLEVEL%\n"
            "if not \"%EXIT_CODE%\"==\"0\" (\n"
            "  echo.\n"
            "  echo [错误] codex-keygen.exe 启动失败，退出码 %EXIT_CODE%\n"
            "  echo 请查看 %LOG_FILE% 获取详细错误。\n"
            "  if not \"%KEYGEN_NO_PAUSE%\"==\"1\" pause\n"
            ")\n"
            "exit /b %EXIT_CODE%\n",
            encoding="utf-8",
        )
    else:
        launcher = release_dir / "start.command"
        launcher.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "cd \"$(dirname \"$0\")\"\n"
            "chmod +x ./codex-keygen\n"
            "./codex-keygen\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        final_path.chmod(0o755)

    readme = release_dir / "README-运行说明.txt"
    readme.write_text(
        "打包产物使用说明\n"
        "================\n\n"
        "1. 运行程序前，请先修改同目录的 runtime-config.json。\n"
        "   可修改字段：host / port / access_username / access_password。\n"
        "2. Windows：双击 start.bat 或 codex-keygen.exe。\n"
        "3. macOS：双击 start.command（首次可能需授予执行权限）。\n"
        "4. 启动后访问：http://127.0.0.1:<port>\n",
        encoding="utf-8",
    )

    return release_dir, final_path


def package(target_arg: str, clean: bool, dry_run: bool, output_dir: Optional[str] = None) -> None:
    target = pick_target(target_arg)
    validate_host_support(target)
    release_root = resolve_release_root(output_dir_arg=output_dir, interactive=(target_arg == "interactive"))

    print(f"\n目标平台：{target}")
    print(f"发布根目录：{release_root}")
    built_file = build_pyinstaller(target=target, clean=clean, dry_run=dry_run)
    release_dir, binary_path = create_release(
        target=target,
        built_file=built_file,
        dry_run=dry_run,
        release_root=release_root,
    )

    print("\n打包完成。")
    print(f"发布目录：{release_dir}")
    print(f"可执行文件：{binary_path}")
    if not dry_run:
        print("可直接运行，参数请编辑 runtime-config.json。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows/macOS 一键打包工具")
    parser.add_argument(
        "--target",
        choices=["auto", "windows", "macos", "interactive"],
        default="auto",
        help="打包目标（默认 auto：仅在 Windows/macOS 按当前系统自动识别）",
    )
    parser.add_argument("--no-clean", action="store_true", help="不清理历史 build 缓存")
    parser.add_argument("--dry-run", action="store_true", help="仅打印打包命令，不真正执行")
    parser.add_argument(
        "--output-dir",
        default="",
        help="发布输出根目录（默认 dist/releases；会自动追加 windows/macos 子目录）",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    target_arg = "interactive" if args.target == "interactive" else args.target
    clean = not args.no_clean

    try:
        package(target_arg=target_arg, clean=clean, dry_run=args.dry_run, output_dir=args.output_dir)
    except PackageError as exc:
        print(f"[错误] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断操作")
        sys.exit(130)


if __name__ == "__main__":
    main()
