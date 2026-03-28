import asyncio
from dataclasses import dataclass

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.config import settings as settings_module
from src.database import session as session_module
from src.database.init_db import initialize_database
from src.database.models import EmailService
from src.database.session import get_db
from src.database.tempmail_bootstrap import (
    ensure_builtin_tempmail_services,
    update_tempmail_runtime_state,
)
from src.services import EmailServiceType
from src.web.routes import email as email_routes
from src.web.routes import registration as registration_routes


def _reset_singletons() -> None:
    settings_module._settings = None
    session_module._db_manager = None


def _init_test_db(tmp_path, monkeypatch, filename: str) -> str:
    db_path = tmp_path / filename
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("APP_DATABASE_URL", db_url)
    _reset_singletons()
    initialize_database(db_url)
    return db_url


def _create_tempmail_service(
    db,
    name: str,
    *,
    provider: str = "mail_tm",
    config: dict | None = None,
    enabled: bool = True,
    last_test_status: str | None = None,
    last_test_message: str | None = None,
) -> EmailService:
    normalized_config = config or {
        "provider": provider,
        "base_url": "https://api.mail.tm",
        "timeout": 30,
        "max_retries": 3,
    }
    service = EmailService(
        service_type=EmailServiceType.TEMPMAIL.value,
        provider=provider,
        name=name,
        config=normalized_config,
        enabled=enabled,
        priority=10,
        is_builtin=False,
        is_immutable=False,
        last_test_status=last_test_status,
        last_test_message=last_test_message,
    )
    db.add(service)
    db.commit()
    db.refresh(service)
    return service


@dataclass
class _FakeProbeResult:
    success: bool
    stage: str
    message: str
    email: str = ""
    is_existing_account: bool = False


class _DummyEmailService:
    def check_health(self):
        raise AssertionError("真实 OTP 探测路径不应调用 check_health")


def test_available_services_only_include_real_otp_passed(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "available_services_otp_gate.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())

            passed = _create_tempmail_service(
                db,
                "OTP Passed",
                last_test_status="success",
                last_test_message="[otp_received] 已确认收到真实 OpenAI OTP",
            )
            passed_id = passed.id
            _create_tempmail_service(
                db,
                "Old Health Success",
                last_test_status="success",
                last_test_message="服务连接正常",
            )
            _create_tempmail_service(
                db,
                "OTP Failed",
                last_test_status="failed",
                last_test_message="[wait_otp] 获取验证码失败",
            )
            _create_tempmail_service(db, "Never Tested")

            update_tempmail_runtime_state(
                db,
                settings_module.get_settings(),
                selection_mode="single",
                single_service_id=passed_id,
            )

        payload = asyncio.run(registration_routes.get_available_email_services())
        service_ids = {item["id"] for item in payload["tempmail"]["services"]}

        assert service_ids == {passed_id}
        assert payload["tempmail"]["available"] is True
        assert payload["tempmail"]["count"] == 1
        assert "pop3" not in payload
        assert payload["selection"]["mode"] == "single"
        assert payload["selection"]["single_service_id"] == passed_id
    finally:
        _reset_singletons()


def test_available_services_excludes_pop3_alias_rules(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "available_services_no_pop3_alias.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())

            _create_tempmail_service(
                db,
                "Legacy POP3 Alias",
                provider="pop3_alias",
                config={
                    "provider": "pop3_alias",
                    "base_email": "123456@225.com",
                    "pop3_host": "pop.225.com",
                    "pop3_port": 995,
                    "pop3_username": "123456@225.com",
                    "pop3_password": "secret",
                    "timeout": 30,
                },
                last_test_status="success",
                last_test_message="[otp_received] 已确认收到真实 OpenAI OTP",
            )
            allowed = _create_tempmail_service(
                db,
                "Tempmail Rule",
                provider="tempmail_lol",
                config={
                    "provider": "tempmail_lol",
                    "base_url": "https://api.tempmail.lol/v2",
                    "timeout": 30,
                    "max_retries": 3,
                },
                last_test_status="success",
                last_test_message="[otp_received] 已确认收到真实 OpenAI OTP",
            )

            update_tempmail_runtime_state(
                db,
                settings_module.get_settings(),
                selection_mode="single",
                single_service_id=allowed.id,
            )

        payload = asyncio.run(registration_routes.get_available_email_services())
        providers = {item["provider"] for item in payload["tempmail"]["services"]}

        assert "pop3_alias" not in providers
        assert "guerrillamail" not in providers
        assert "tempmail_lol" in providers
    finally:
        _reset_singletons()


def test_email_service_types_exclude_offline_providers():
    payload = asyncio.run(email_routes.get_service_types())
    providers = payload["types"][0]["providers"]
    values = {item["value"] for item in providers}

    assert "tempmail_lol" in values
    assert "guerrillamail" not in values
    assert "pop3_alias" not in values


def test_create_email_service_rejects_pop3_alias_provider(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_reject_pop_provider.db")

    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                email_routes.create_email_service(
                    email_routes.EmailServiceCreate(
                        service_type=EmailServiceType.TEMPMAIL.value,
                        provider="pop3_alias",
                        name="legacy-pop-provider",
                        enabled=False,
                        config={
                            "provider": "pop3_alias",
                            "base_email": "123456@225.com",
                        },
                    )
                )
            )

        assert error.value.status_code == 400
        assert "已下线" in error.value.detail
    finally:
        _reset_singletons()


def test_create_email_service_rejects_guerrillamail_provider(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_reject_guerrilla_provider.db")

    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                email_routes.create_email_service(
                    email_routes.EmailServiceCreate(
                        service_type=EmailServiceType.TEMPMAIL.value,
                        provider="guerrillamail",
                        name="legacy-guerrilla-provider",
                        enabled=False,
                        config={
                            "provider": "guerrillamail",
                            "base_url": "https://api.guerrillamail.com/ajax.php",
                        },
                    )
                )
            )

        assert error.value.status_code == 400
        assert "已下线" in error.value.detail
    finally:
        _reset_singletons()


def test_start_registration_rejects_legacy_pop3_type():
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            registration_routes.start_registration(
                registration_routes.RegistrationTaskCreate(email_service_type="pop3"),
                BackgroundTasks(),
            )
        )

    assert error.value.status_code == 400
    assert "仅支持可用临时邮箱规则" in error.value.detail


def test_start_batch_registration_rejects_legacy_pop3_type():
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            registration_routes.start_batch_registration(
                registration_routes.BatchRegistrationRequest(
                    email_service_type="pop3",
                    count=1,
                    registration_mode="batch",
                ),
                BackgroundTasks(),
            )
        )

    assert error.value.status_code == 400
    assert "仅支持可用临时邮箱规则" in error.value.detail


def test_email_service_test_route_records_probe_success_stage(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_probe_success.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())
            target = _create_tempmail_service(db, "Probe Success Target")

        monkeypatch.setattr(
            email_routes.EmailServiceFactory,
            "create",
            staticmethod(lambda *_args, **_kwargs: _DummyEmailService()),
        )

        class _FakeSuccessEngine:
            def __init__(self, *args, **kwargs):
                pass

            def run_otp_probe(self) -> _FakeProbeResult:
                return _FakeProbeResult(
                    success=True,
                    stage="otp_received",
                    message="已确认收到真实 OpenAI OTP",
                    email="probe-success@example.com",
                    is_existing_account=False,
                )

        monkeypatch.setattr(email_routes, "RegistrationEngine", _FakeSuccessEngine)

        result = asyncio.run(email_routes.test_email_service(target.id))

        assert result.success is True
        assert result.status == "success"
        assert result.stage == "otp_received"
        assert result.message == "已确认收到真实 OpenAI OTP"

        with get_db() as db:
            updated = db.query(EmailService).filter(EmailService.id == target.id).first()
            assert updated is not None
            assert updated.last_test_status == "success"
            assert isinstance(updated.last_test_message, str)
            assert updated.last_test_message.startswith("[otp_received]")
            assert "已确认收到真实 OpenAI OTP" in updated.last_test_message
            assert updated.last_tested_at is not None
    finally:
        _reset_singletons()


def test_email_service_test_route_records_probe_failure_stage(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_probe_failure.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())
            target = _create_tempmail_service(db, "Probe Failure Target")

        monkeypatch.setattr(
            email_routes.EmailServiceFactory,
            "create",
            staticmethod(lambda *_args, **_kwargs: _DummyEmailService()),
        )

        class _FakeFailureEngine:
            def __init__(self, *args, **kwargs):
                pass

            def run_otp_probe(self) -> _FakeProbeResult:
                return _FakeProbeResult(
                    success=False,
                    stage="wait_otp",
                    message="获取验证码失败",
                    email="probe-failure@example.com",
                    is_existing_account=False,
                )

        monkeypatch.setattr(email_routes, "RegistrationEngine", _FakeFailureEngine)

        result = asyncio.run(email_routes.test_email_service(target.id))

        assert result.success is False
        assert result.status == "failed"
        assert result.stage == "wait_otp"
        assert result.message == "获取验证码失败"

        with get_db() as db:
            updated = db.query(EmailService).filter(EmailService.id == target.id).first()
            assert updated is not None
            assert updated.last_test_status == "failed"
            assert isinstance(updated.last_test_message, str)
            assert updated.last_test_message.startswith("[wait_otp]")
            assert "获取验证码失败" in updated.last_test_message
            assert updated.last_tested_at is not None
    finally:
        _reset_singletons()


def test_enable_email_service_requires_real_otp_pass(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_enable_gate.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())
            unavailable = _create_tempmail_service(
                db,
                "Unverified Service",
                enabled=False,
                last_test_status="failed",
                last_test_message="[wait_otp] 获取验证码失败",
            )
            available = _create_tempmail_service(
                db,
                "Verified Service",
                enabled=False,
                last_test_status="success",
                last_test_message="[otp_received] 已确认收到真实 OpenAI OTP",
            )
            unavailable_id = unavailable.id
            available_id = available.id

        with pytest.raises(HTTPException) as unavailable_error:
            asyncio.run(email_routes.enable_email_service(unavailable_id))
        assert unavailable_error.value.status_code == 400
        assert "OTP" in unavailable_error.value.detail

        result = asyncio.run(email_routes.enable_email_service(available_id))
        assert result["success"] is True

        with get_db() as db:
            unavailable_after = db.query(EmailService).filter(EmailService.id == unavailable_id).first()
            available_after = db.query(EmailService).filter(EmailService.id == available_id).first()
            assert unavailable_after is not None
            assert available_after is not None
            assert unavailable_after.enabled is False
            assert available_after.enabled is True
    finally:
        _reset_singletons()


def test_create_and_update_reject_enable_without_otp_pass(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch, "email_service_create_update_gate.db")

    try:
        with get_db() as db:
            ensure_builtin_tempmail_services(db, settings_module.get_settings())

        with pytest.raises(HTTPException) as create_error:
            asyncio.run(
                email_routes.create_email_service(
                    email_routes.EmailServiceCreate(
                        service_type=EmailServiceType.TEMPMAIL.value,
                        provider="mail_tm",
                        name="Create Enabled Should Fail",
                        enabled=True,
                        config={
                            "provider": "mail_tm",
                            "base_url": "https://api.mail.tm",
                            "timeout": 30,
                            "max_retries": 3,
                        },
                    )
                )
            )

        assert create_error.value.status_code == 400
        assert "先测试通过" in create_error.value.detail

        with get_db() as db:
            target = _create_tempmail_service(
                db,
                "Update Gate Target",
                enabled=False,
                last_test_status="failed",
                last_test_message="[wait_otp] 获取验证码失败",
            )

        with pytest.raises(HTTPException) as update_error:
            asyncio.run(
                email_routes.update_email_service(
                    target.id,
                    email_routes.EmailServiceUpdate(enabled=True),
                )
            )

        assert update_error.value.status_code == 400
        assert "OTP" in update_error.value.detail
    finally:
        _reset_singletons()
