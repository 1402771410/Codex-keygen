"""
Web UI 启动入口
"""

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# 添加项目根目录到 Python 路径
# PyInstaller 打包后 __file__ 在临时解压目录，需要用 sys.executable 所在目录作为数据目录
import os
if getattr(sys, 'frozen', False):
    # 打包后：使用可执行文件所在目录
    project_root = Path(sys.executable).parent
    _src_root = Path(getattr(sys, '_MEIPASS', project_root))
else:
    project_root = Path(__file__).parent
    _src_root = project_root
sys.path.insert(0, str(_src_root))

from src.core.utils import setup_logging
from src.database.init_db import initialize_database
from src.config.settings import get_settings


def _set_env_override(key: str, value) -> None:
    """仅在环境变量缺失时写入运行时值。"""
    if value is None:
        return
    current_value = os.environ.get(key)
    if current_value not in (None, ""):
        return
    os.environ[key] = str(value)


def _extract_runtime_values(runtime_config: Dict[str, Any]) -> Dict[str, Any]:
    app_section_raw = runtime_config.get("app")
    app_section: Dict[str, Any] = app_section_raw if isinstance(app_section_raw, dict) else {}

    def _pick(*keys: str):
        for key in keys:
            value = runtime_config.get(key)
            if value not in (None, ""):
                return value
            app_value = app_section.get(key)
            if app_value not in (None, ""):
                return app_value
        return None

    values: Dict[str, Any] = {
        "host": _pick("host", "webui_host"),
        "port": _pick("port", "webui_port"),
        "access_username": _pick("access_username", "webui_access_username"),
        "access_password": _pick("access_password", "webui_access_password"),
        "debug": _pick("debug"),
        "log_level": _pick("log_level"),
    }

    if values["port"] is not None:
        try:
            values["port"] = int(values["port"])
        except (TypeError, ValueError):
            values["port"] = None

    if values["debug"] is not None and not isinstance(values["debug"], bool):
        values["debug"] = str(values["debug"]).lower() in ("1", "true", "yes", "on")

    return values


def _read_runtime_config_values() -> Dict[str, Any]:
    runtime_config_path = project_root / "runtime-config.json"
    if not runtime_config_path.exists():
        return {}

    try:
        with open(runtime_config_path, encoding="utf-8") as f:
            runtime_config = json.load(f)
        if not isinstance(runtime_config, dict):
            return {}
        return _extract_runtime_values(runtime_config)
    except Exception as exc:
        print(f"加载 runtime-config.json 失败: {exc}")
        return {}


def _load_dotenv():
    """加载 runtime-config.json 与 .env 文件（可执行文件同目录或项目根目录）。"""
    runtime_values = _read_runtime_config_values()
    host = runtime_values.get("host")
    port = runtime_values.get("port")
    username = runtime_values.get("access_username")
    password = runtime_values.get("access_password")
    debug = runtime_values.get("debug")
    log_level = runtime_values.get("log_level")

    if host is not None:
        _set_env_override("APP_HOST", host)
        _set_env_override("WEBUI_HOST", host)
    if port is not None:
        _set_env_override("APP_PORT", port)
        _set_env_override("WEBUI_PORT", port)
    if username is not None:
        _set_env_override("APP_ACCESS_USERNAME", username)
        _set_env_override("WEBUI_ACCESS_USERNAME", username)
    if password is not None:
        _set_env_override("APP_ACCESS_PASSWORD", password)
        _set_env_override("WEBUI_ACCESS_PASSWORD", password)
    if debug is not None:
        _set_env_override("DEBUG", "1" if bool(debug) else "0")
    if log_level is not None:
        _set_env_override("LOG_LEVEL", log_level)

    env_path = project_root / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # .env 只用于补充缺失值，不覆盖 runtime-config.json 和系统环境变量
            if key and key not in os.environ:
                os.environ[key] = value


def setup_application():
    """设置应用程序"""
    # 加载 .env 文件（优先级低于已有环境变量）
    _load_dotenv()

    # 确保数据目录和日志目录在可执行文件所在目录（打包后也适用）
    data_dir = project_root / "data"
    logs_dir = project_root / "logs"
    data_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    # 将数据目录路径注入环境变量，供数据库配置使用
    os.environ.setdefault("APP_DATA_DIR", str(data_dir))
    os.environ.setdefault("APP_LOGS_DIR", str(logs_dir))

    # 初始化数据库（必须先于获取设置）
    try:
        initialize_database()
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        raise

    # 获取配置（需要数据库已初始化）
    settings = get_settings()

    # 配置日志（日志文件写到实际 logs 目录）
    log_file = str(logs_dir / Path(settings.log_file).name)
    setup_logging(
        log_level=settings.log_level,
        log_file=log_file
    )

    logger = logging.getLogger(__name__)
    logger.info("数据库初始化完成")
    logger.info(f"数据目录: {data_dir}")
    logger.info(f"日志目录: {logs_dir}")

    logger.info("应用程序设置完成")
    return settings


class RuntimeConfigSyncWorker:
    """运行时配置同步器：监控 runtime-config.json 变更并同步到数据库设置。"""

    def __init__(self, poll_interval: float = 2.0):
        self.runtime_path = project_root / "runtime-config.json"
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_mtime: Optional[float] = None
        self._last_values: Dict[str, Any] = _read_runtime_config_values()

    def start(self) -> None:
        if not self.runtime_path.exists():
            return

        try:
            self._last_mtime = self.runtime_path.stat().st_mtime
        except OSError:
            self._last_mtime = None

        self._thread = threading.Thread(target=self._run, name="runtime-config-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        from src.config.settings import update_settings

        logger = logging.getLogger(__name__)
        while not self._stop_event.wait(self.poll_interval):
            if not self.runtime_path.exists():
                continue

            try:
                mtime = self.runtime_path.stat().st_mtime
            except OSError:
                continue

            if self._last_mtime is not None and mtime <= self._last_mtime:
                continue
            self._last_mtime = mtime

            values = _read_runtime_config_values()
            updates: Dict[str, Any] = {}

            host = values.get("host")
            if host not in (None, self._last_values.get("host")):
                updates["webui_host"] = str(host)

            port = values.get("port")
            if port not in (None, self._last_values.get("port")):
                updates["webui_port"] = int(port)

            username = values.get("access_username")
            if username not in (None, self._last_values.get("access_username")):
                updates["webui_access_username"] = str(username)

            password = values.get("access_password")
            if password not in (None, self._last_values.get("access_password")):
                updates["webui_access_password"] = str(password)

            if values.get("debug") is not None and values.get("debug") != self._last_values.get("debug"):
                updates["debug"] = bool(values.get("debug"))

            log_level = values.get("log_level")
            if log_level not in (None, self._last_values.get("log_level")):
                updates["log_level"] = str(log_level)

            if not updates:
                self._last_values = values
                continue

            try:
                update_settings(**updates)
                self._last_values = values
                logger.info("检测到 runtime-config.json 变更，已同步配置项：%s", ", ".join(updates.keys()))

                if "webui_host" in updates or "webui_port" in updates:
                    logger.warning("监听地址/端口已更新到配置，需重启进程后端口绑定才会生效")
            except Exception as exc:  # noqa: BLE001
                logger.error("同步 runtime-config.json 失败: %s", exc)


def start_webui():
    """启动 Web UI"""
    import importlib

    uvicorn = importlib.import_module("uvicorn")

    # 设置应用程序
    settings = setup_application()

    # 导入 FastAPI 应用（延迟导入以避免循环依赖）
    from src.web.app import app

    # 配置 uvicorn
    uvicorn_config = {
        "app": "src.web.app:app",
        "host": settings.webui_host,
        "port": settings.webui_port,
        "reload": settings.debug,
        "log_level": "info" if settings.debug else "warning",
        "access_log": settings.debug,
        "ws": "websockets",
    }

    logger = logging.getLogger(__name__)
    logger.info(f"启动 Web UI 在 http://{settings.webui_host}:{settings.webui_port}")
    logger.info(f"调试模式: {settings.debug}")

    runtime_sync_worker = RuntimeConfigSyncWorker(poll_interval=2.0)
    runtime_sync_worker.start()

    # 启动服务器
    try:
        uvicorn.run(**uvicorn_config)
    finally:
        runtime_sync_worker.stop()


def main():
    """主函数"""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Codex-keygen Web UI")
    parser.add_argument("--host", help="监听主机 (也可通过 WEBUI_HOST/APP_HOST 环境变量设置)")
    parser.add_argument("--port", type=int, help="监听端口 (也可通过 WEBUI_PORT/APP_PORT 环境变量设置)")
    parser.add_argument("--debug", action="store_true", help="启用调试模式 (也可通过 DEBUG=1 环境变量设置)")
    parser.add_argument("--reload", action="store_true", help="启用热重载")
    parser.add_argument("--log-level", help="日志级别 (也可通过 LOG_LEVEL 环境变量设置)")
    parser.add_argument("--access-username", help="Web UI 访问账号 (也可通过 WEBUI_ACCESS_USERNAME/APP_ACCESS_USERNAME 环境变量设置)")
    parser.add_argument("--access-password", help="Web UI 访问密钥 (也可通过 WEBUI_ACCESS_PASSWORD/APP_ACCESS_PASSWORD 环境变量设置)")
    args = parser.parse_args()

    # 在读取环境变量前先加载 .env，确保容器/本地统一生效。
    _load_dotenv()

    # 更新配置
    from src.config.settings import update_settings

    updates = {}
    
    # 优先使用命令行参数，如果没有则尝试从环境变量获取
    host = args.host or os.environ.get("WEBUI_HOST") or os.environ.get("APP_HOST")
    if host:
        updates["webui_host"] = host
        
    port = args.port or os.environ.get("WEBUI_PORT") or os.environ.get("APP_PORT")
    if port:
        updates["webui_port"] = int(port)
        
    debug = args.debug or os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    updates["debug"] = debug
        
    log_level = args.log_level or os.environ.get("LOG_LEVEL")
    if log_level:
        updates["log_level"] = log_level

    access_username = (
        args.access_username
        or os.environ.get("WEBUI_ACCESS_USERNAME")
        or os.environ.get("APP_ACCESS_USERNAME")
    )
    if access_username:
        updates["webui_access_username"] = access_username

    access_password = (
        args.access_password
        or os.environ.get("WEBUI_ACCESS_PASSWORD")
        or os.environ.get("APP_ACCESS_PASSWORD")
    )
    if access_password:
        updates["webui_access_password"] = access_password

    if updates:
        update_settings(**updates)

    # 启动 Web UI
    start_webui()


if __name__ == "__main__":
    main()
