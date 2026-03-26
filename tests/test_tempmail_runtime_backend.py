import asyncio
import sqlite3

import pytest

from src.config import settings as settings_module
from src.config.settings import get_settings, update_settings
from src.database import session as session_module
from src.database.init_db import initialize_database
from src.database.models import EmailService
from src.database.session import DatabaseSessionManager, get_db
from src.database.tempmail_bootstrap import (
    ensure_builtin_tempmail_services,
    update_tempmail_runtime_state,
)
from src.services import EmailServiceType
from src.web.routes import accounts as accounts_routes
from src.web.routes import email as email_routes
from src.web.routes import registration as registration_routes


def _reset_singletons() -> None:
    settings_module._settings = None
    session_module._db_manager = None


def test_sqlite_migration_adds_tempmail_runtime_columns(tmp_path):
    db_path = tmp_path / "legacy_runtime.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE email_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_type VARCHAR(50) NOT NULL,
                provider VARCHAR(50) DEFAULT 'tempmail_lol',
                name VARCHAR(100) NOT NULL,
                config TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                is_builtin BOOLEAN DEFAULT 0,
                is_immutable BOOLEAN DEFAULT 0,
                builtin_key VARCHAR(100),
                priority INTEGER DEFAULT 0,
                last_used DATETIME,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    manager = DatabaseSessionManager(f"sqlite:///{db_path.as_posix()}")
    manager.migrate_tables()

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info('email_services')").fetchall()
        }
    finally:
        conn.close()

    assert {
        "provider_runtime_meta",
        "last_test_status",
        "last_tested_at",
        "last_test_message",
        "selection_mode",
        "single_service_id",
    }.issubset(columns)


def test_routes_use_service_runtime_state_instead_of_global_settings(tmp_path, monkeypatch):
    db_path = tmp_path / "route_contract.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    monkeypatch.setenv("APP_DATABASE_URL", db_url)
    _reset_singletons()
    initialize_database(db_url)

    try:
        # 写入与运行时规则冲突的旧全局设置，验证路由不再依赖这些值作为真值。
        update_settings(
            tempmail_base_url="https://legacy.invalid/v2",
            tempmail_enabled=False,
            tempmail_selection_mode="multi",
            tempmail_single_service_id=None,
        )
        settings = get_settings()

        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings)
            custom_service = EmailService(
                service_type=EmailServiceType.TEMPMAIL.value,
                provider="mail_tm",
                name="Runtime Mail.tm",
                config={
                    "provider": "mail_tm",
                    "base_url": "https://api.mail.tm",
                    "timeout": 30,
                    "max_retries": 3,
                },
                enabled=True,
                priority=3,
                is_builtin=False,
                is_immutable=False,
            )
            db.add(custom_service)
            db.commit()
            db.refresh(custom_service)
            custom_service_id = custom_service.id

            update_tempmail_runtime_state(
                db,
                settings,
                global_enabled=True,
                selection_mode="single",
                single_service_id=custom_service_id,
            )

        stats = asyncio.run(email_routes.get_email_services_stats())
        assert stats["global_enabled"] is True
        assert stats["selection_mode"] == "single"
        assert stats["single_service_id"] == custom_service_id

        available = asyncio.run(registration_routes.get_available_email_services())
        assert available["selection"]["mode"] == "single"
        assert available["selection"]["single_service_id"] == custom_service_id

        with get_db() as db:
            explicit_config = accounts_routes._build_inbox_config(
                db,
                EmailServiceType.TEMPMAIL,
                str(custom_service_id),
            )
            fallback_config = accounts_routes._build_inbox_config(
                db,
                EmailServiceType.TEMPMAIL,
                None,
            )

            assert explicit_config is not None
            assert fallback_config is not None
            assert explicit_config["base_url"] == "https://api.mail.tm"
            assert fallback_config["base_url"] == "https://api.mail.tm"

            # 关闭所有服务后，选择逻辑应返回空，而非回退到 settings.tempmail_*。
            all_services = db.query(EmailService).filter(EmailService.service_type == "tempmail").all()
            for item in all_services:
                item.enabled = False
            db.commit()

            with pytest.raises(ValueError):
                registration_routes._select_tempmail_service(db, settings, None)
    finally:
        _reset_singletons()
