#!/usr/bin/env python3
"""
统一命令入口：keygen

目标：
- 安装/升级（同一命令）：keygen install
- 升级兼容别名：keygen upgrade
- 卸载：keygen uninstall
- 服务控制：keygen start/stop/restart/status
- 开机自启：keygen autostart-on/autostart-off
- 信息查看：keygen info
- 打包：keygen package
- 配置面板：keygen config
- 直接输入 keygen 默认打开管理面板
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import deploy_manager, package_manager  # noqa: E402


def detect_launcher() -> str:
    """根据 launcher 环境变量检测实际调用的程序名，用于 help 输出。"""
    launcher = os.environ.get("KEYGEN_LAUNCHER", "").strip()
    if launcher:
        return launcher
    # 无 launcher 时回退为已安装包的入口名（keygen），供 pyproject 脚本入口调用时使用
    return "keygen"


def build_parser() -> argparse.ArgumentParser:
    launcher = detect_launcher()
    parser = argparse.ArgumentParser(
        prog=launcher,
        description="统一部署/升级/打包/配置命令入口",
    )
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="一键安装/升级（按系统自动选择流程）")
    install_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="安装模式")
    install_parser.add_argument("--non-interactive", action="store_true", help="非交互安装，使用现有配置")
    install_parser.add_argument(
        "--yes-install-docker",
        action="store_true",
        help="Linux 缺少 Docker 时自动同意安装",
    )

    upgrade_parser = subparsers.add_parser("upgrade", help="兼容命令（等价 install）")
    upgrade_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="升级模式")
    upgrade_parser.add_argument("--non-interactive", action="store_true", help="非交互模式")

    uninstall_parser = subparsers.add_parser("uninstall", help="一键卸载")
    uninstall_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="卸载模式")
    uninstall_parser.add_argument("--purge", action="store_true", help="深度清理（删除本地环境文件/虚拟环境；Docker 清理卷）")
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

    package_parser = subparsers.add_parser("package", help="一键打包（auto 仅支持 Windows/macOS）")
    package_parser.add_argument(
        "--target",
        choices=["auto", "windows", "macos", "interactive"],
        default="auto",
        help="打包目标（auto: 按当前系统识别，仅 Windows/macOS）",
    )
    package_parser.add_argument("--no-clean", action="store_true", help="不清理历史 build 缓存")
    package_parser.add_argument("--dry-run", action="store_true", help="仅打印命令，不执行打包")

    subparsers.add_parser("config", help="打开配置面板")
    subparsers.add_parser("recommend", help="查看安装推荐")
    subparsers.add_parser("menu", help="打开菜单")
    return parser


def run_install(args: argparse.Namespace) -> int:
    deploy_manager.do_deploy(
        mode=args.mode,
        interactive=not args.non_interactive,
        auto_yes_install_docker=args.yes_install_docker,
        config_override=None,
    )
    return 0


def run_upgrade(args: argparse.Namespace) -> int:
    print("[提示] upgrade 为兼容命令，建议统一使用 install。")
    deploy_manager.do_deploy(
        mode=args.mode,
        interactive=not args.non_interactive,
        auto_yes_install_docker=False,
        config_override=None,
    )
    return 0


def run_uninstall(args: argparse.Namespace) -> int:
    deploy_manager.do_uninstall(mode=args.mode, purge=bool(args.purge), interactive=not args.non_interactive)
    return 0


def run_start(args: argparse.Namespace) -> int:
    deploy_manager.do_start(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_stop(args: argparse.Namespace) -> int:
    deploy_manager.do_stop(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_restart(args: argparse.Namespace) -> int:
    deploy_manager.do_restart(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_status(args: argparse.Namespace) -> int:
    deploy_manager.do_status(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_autostart_on(args: argparse.Namespace) -> int:
    deploy_manager.do_enable_autostart(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_autostart_off(args: argparse.Namespace) -> int:
    deploy_manager.do_disable_autostart(mode=args.mode, interactive=not args.non_interactive)
    return 0


def run_info() -> int:
    deploy_manager.print_paths_info()
    return 0


def run_package(args: argparse.Namespace) -> int:
    target = args.target
    package_manager.package(target_arg=target, clean=not args.no_clean, dry_run=args.dry_run)
    return 0


def run_config() -> int:
    deploy_manager.config_panel()
    return 0


def run_recommend() -> int:
    deploy_manager.print_recommendation()
    return 0


def run_menu() -> int:
    deploy_manager.menu()
    return 0


def dispatch(args: argparse.Namespace) -> int:
    command = args.command

    # 默认行为：直接 keygen 打开管理面板
    if command is None:
        return run_menu()

    if command == "install":
        return run_install(args)
    if command == "upgrade":
        return run_upgrade(args)
    if command == "uninstall":
        return run_uninstall(args)
    if command == "start":
        return run_start(args)
    if command == "stop":
        return run_stop(args)
    if command == "restart":
        return run_restart(args)
    if command == "status":
        return run_status(args)
    if command == "autostart-on":
        return run_autostart_on(args)
    if command == "autostart-off":
        return run_autostart_off(args)
    if command == "info":
        return run_info()
    if command == "package":
        return run_package(args)
    if command == "config":
        return run_config()
    if command == "recommend":
        return run_recommend()
    if command == "menu":
        return run_menu()

    raise deploy_manager.DeployError(f"未知命令: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return dispatch(args)
    except deploy_manager.DeployError as exc:
        print(f"[错误] {exc}")
        return 1
    except package_manager.PackageError as exc:
        print(f"[错误] {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n用户中断操作")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
