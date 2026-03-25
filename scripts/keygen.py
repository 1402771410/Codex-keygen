#!/usr/bin/env python3
"""
统一命令入口：keygen

目标：
- 安装：keygen install
- 升级：keygen upgrade
- 打包：keygen package
- 配置面板：keygen config
- 直接输入 keygen 默认打开配置面板
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import deploy_manager, package_manager  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keygen",
        description="统一部署/升级/打包/配置命令入口",
    )
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="一键安装（按系统自动选择流程）")
    install_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="安装模式")
    install_parser.add_argument("--non-interactive", action="store_true", help="非交互安装，使用现有配置")
    install_parser.add_argument(
        "--yes-install-docker",
        action="store_true",
        help="Linux 缺少 Docker 时自动同意安装",
    )

    upgrade_parser = subparsers.add_parser("upgrade", help="一键升级")
    upgrade_parser.add_argument("--mode", choices=["auto", "docker", "local"], default="auto", help="升级模式")

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
    deploy_manager.do_upgrade(mode=args.mode)
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

    # 默认行为：直接 keygen 打开配置面板
    if command is None:
        return run_config()

    if command == "install":
        return run_install(args)
    if command == "upgrade":
        return run_upgrade(args)
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
