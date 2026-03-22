from contextlib import contextmanager
from types import SimpleNamespace
from typing import Optional

import src.core.register as register_module
from src.config.constants import OPENAI_PAGE_TYPES
from src.config.constants import EmailServiceType
from src.core.register import RegistrationEngine, RegistrationResult, SignupFormResult
from src.services.base import BaseEmailService


class DummyEmailService(BaseEmailService):
    def __init__(self):
        super().__init__(EmailServiceType.DUCK_MAIL, name="dummy_duck_mail")

    def create_email(self, config=None):
        return {
            "email": "tester@example.com",
            "service_id": "mail-1",
        }

    def get_verification_code(
        self,
        email: str,
        email_id: Optional[str] = None,
        timeout: int = 120,
        pattern: str = r"(?<!\d)(\d{6})(?!\d)",
        otp_sent_at: Optional[float] = None,
    ):
        return "123456"

    def list_emails(self, **kwargs):
        return []

    def delete_email(self, email_id: str) -> bool:
        return True

    def check_health(self) -> bool:
        return True


class FakeCookies:
    def __init__(self, mapping):
        self._mapping = dict(mapping)

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def get_dict(self):
        return dict(self._mapping)


class FakeSession:
    def __init__(self, cookies):
        self.cookies = cookies


def _prepare_engine_with_happy_path(monkeypatch):
    engine = RegistrationEngine(DummyEmailService())

    monkeypatch.setattr(engine, "_check_ip_location", lambda: (True, "US"))

    def fake_create_email():
        engine.email = "tester@example.com"
        engine.email_info = {"service_id": "mail-1"}
        return True

    monkeypatch.setattr(engine, "_create_email", fake_create_email)

    def fake_init_session():
        engine.__dict__["session"] = FakeSession(
            FakeCookies({"__Secure-next-auth.session-token": "sess-123"})
        )
        return True

    monkeypatch.setattr(engine, "_init_session", fake_init_session)
    monkeypatch.setattr(engine, "_start_oauth", lambda: True)
    monkeypatch.setattr(engine, "_get_device_id", lambda: "did-1")
    monkeypatch.setattr(engine, "_check_sentinel", lambda did: None)
    monkeypatch.setattr(engine, "_send_verification_code", lambda: True)
    monkeypatch.setattr(engine, "_get_verification_code", lambda: "654321")
    monkeypatch.setattr(engine, "_validate_verification_code", lambda code: True)
    monkeypatch.setattr(engine, "_select_workspace", lambda workspace_id: "https://example.com/continue")
    monkeypatch.setattr(
        engine,
        "_follow_redirects",
        lambda continue_url: "http://localhost:1455/auth/callback?code=abc&state=xyz",
    )
    monkeypatch.setattr(
        engine,
        "_handle_oauth_callback",
        lambda callback_url: {
            "account_id": "acc-1",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
        },
    )

    return engine


def test_run_new_account_falls_back_when_workspace_missing(monkeypatch):
    engine = _prepare_engine_with_happy_path(monkeypatch)
    call_order = []

    monkeypatch.setattr(
        engine,
        "_submit_signup_form",
        lambda did, sen_token: SignupFormResult(
            success=True,
            page_type=OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION"],
            is_existing_account=False,
            response_data={},
        ),
    )

    def fake_register_password():
        engine.password = "P@ssw0rd123"
        return True, engine.password

    monkeypatch.setattr(engine, "_register_password", fake_register_password)
    monkeypatch.setattr(engine, "_create_user_account", lambda: True)

    def fake_second_login():
        call_order.append("second-login")
        return "ws-1"

    monkeypatch.setattr(engine, "_run_second_oauth_login_after_create", fake_second_login)

    def fake_get_workspace_id():
        call_order.append("workspace-initial")
        return None

    monkeypatch.setattr(engine, "_get_workspace_id", fake_get_workspace_id)

    result = engine.run()

    assert result.success is True
    assert result.source == "register"
    assert result.workspace_id == "ws-1"
    assert call_order == ["workspace-initial", "second-login"]


def test_run_existing_account_skips_second_login(monkeypatch):
    engine = _prepare_engine_with_happy_path(monkeypatch)

    def fake_submit_signup_form(did, sen_token):
        engine._is_existing_account = True
        return SignupFormResult(
            success=True,
            page_type=OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"],
            is_existing_account=True,
            response_data={},
        )

    monkeypatch.setattr(engine, "_submit_signup_form", fake_submit_signup_form)
    monkeypatch.setattr(engine, "_register_password", lambda: (_ for _ in ()).throw(AssertionError("不应调用密码注册")))
    monkeypatch.setattr(engine, "_send_verification_code", lambda: (_ for _ in ()).throw(AssertionError("不应主动发送验证码")))
    monkeypatch.setattr(engine, "_create_user_account", lambda: (_ for _ in ()).throw(AssertionError("不应创建账户")))
    monkeypatch.setattr(
        engine,
        "_run_second_oauth_login_after_create",
        lambda: (_ for _ in ()).throw(AssertionError("已注册账号不应触发二次登录")),
    )
    monkeypatch.setattr(engine, "_get_workspace_id", lambda: "ws-existing")

    result = engine.run()

    assert result.success is True
    assert result.source == "login"
    assert result.workspace_id == "ws-existing"


def test_second_login_flow_uses_login_hint_and_passwordless_otp(monkeypatch):
    engine = RegistrationEngine(DummyEmailService())
    engine.email = "tester@example.com"
    engine.password = "P@ssw0rd123"

    order = []
    captured = {}

    monkeypatch.setattr(engine, "_reset_session_for_fallback_login", lambda: True)
    monkeypatch.setattr(engine, "_start_oauth", lambda: True)
    monkeypatch.setattr(engine, "_get_device_id", lambda: "did-2")
    monkeypatch.setattr(engine, "_check_sentinel", lambda did: None)

    def fake_submit_signup_form(did, sen_token, update_existing_state=True, screen_hint="signup"):
        captured["update_existing_state"] = update_existing_state
        captured["screen_hint"] = screen_hint
        return SignupFormResult(
            success=True,
            page_type=OPENAI_PAGE_TYPES["LOGIN_PASSWORD"],
            is_existing_account=False,
            response_data={},
        )

    monkeypatch.setattr(engine, "_submit_signup_form", fake_submit_signup_form)

    def fake_send_passwordless_otp():
        order.append("send-passwordless-otp")
        return True

    def fake_get_otp():
        order.append("get-otp")
        return "112233"

    def fake_validate_otp(code):
        assert code == "112233"
        order.append("validate-otp")
        return True

    monkeypatch.setattr(engine, "_send_passwordless_login_otp", fake_send_passwordless_otp)
    monkeypatch.setattr(engine, "_get_verification_code", fake_get_otp)
    monkeypatch.setattr(engine, "_validate_verification_code", fake_validate_otp)
    monkeypatch.setattr(engine, "_get_workspace_id", lambda: "ws-second-login")

    assert engine._run_second_oauth_login_after_create() == "ws-second-login"
    assert captured["update_existing_state"] is False
    assert captured["screen_hint"] == "login"
    assert order == ["send-passwordless-otp", "get-otp", "validate-otp"]


def test_second_login_fails_on_unexpected_page_type(monkeypatch):
    engine = RegistrationEngine(DummyEmailService())
    engine.email = "tester@example.com"
    engine.password = "P@ssw0rd123"

    monkeypatch.setattr(engine, "_reset_session_for_fallback_login", lambda: True)
    monkeypatch.setattr(engine, "_start_oauth", lambda: True)
    monkeypatch.setattr(engine, "_get_device_id", lambda: "did-3")
    monkeypatch.setattr(engine, "_check_sentinel", lambda did: None)
    monkeypatch.setattr(
        engine,
        "_submit_signup_form",
        lambda did, sen_token, update_existing_state=True, screen_hint="signup": SignupFormResult(
            success=True,
            page_type="consent",
            is_existing_account=False,
            response_data={},
        ),
    )

    assert engine._run_second_oauth_login_after_create() is None


def test_password_page_type_aliases_are_supported():
    engine = RegistrationEngine(DummyEmailService())

    assert engine._is_password_page_type(OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION"]) is True
    assert engine._is_password_page_type(OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION_ALT"]) is True
    assert engine._is_password_page_type("email_otp_verification") is False
    assert engine._is_login_password_page_type(OPENAI_PAGE_TYPES["LOGIN_PASSWORD"]) is True


def test_run_new_account_records_metadata_when_workspace_fallback_fails(monkeypatch):
    engine = _prepare_engine_with_happy_path(monkeypatch)

    monkeypatch.setattr(
        engine,
        "_submit_signup_form",
        lambda did, sen_token: SignupFormResult(
            success=True,
            page_type=OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION"],
            is_existing_account=False,
            response_data={},
        ),
    )

    def fake_register_password():
        engine.password = "P@ssw0rd123"
        return True, engine.password

    monkeypatch.setattr(engine, "_register_password", fake_register_password)
    monkeypatch.setattr(engine, "_create_user_account", lambda: True)
    monkeypatch.setattr(engine, "_get_workspace_id", lambda: None)
    monkeypatch.setattr(engine, "_run_second_oauth_login_after_create", lambda: None)

    result = engine.run()

    assert result.success is False
    assert result.error_message == "获取 Workspace ID 失败"
    assert result.metadata == {
        "account_created": True,
        "error_stage": "workspace_fallback_login",
    }


def test_save_to_database_persists_serialized_cookies(monkeypatch):
    engine = RegistrationEngine(DummyEmailService())
    engine.email_info = {"service_id": "mail-1"}
    engine.__dict__["session"] = FakeSession(FakeCookies({"z": "3", "a": "1"}))

    result = RegistrationResult(
        success=True,
        email="tester@example.com",
        password="P@ssw0rd123",
        account_id="acc-2",
        workspace_id="ws-2",
        access_token="access-token",
        refresh_token="refresh-token",
        id_token="id-token",
        session_token="sess-2",
        metadata={"from": "test"},
    )

    monkeypatch.setattr(register_module, "get_settings", lambda: SimpleNamespace(openai_client_id="client-id"))

    @contextmanager
    def fake_get_db():
        yield object()

    monkeypatch.setattr(register_module, "get_db", fake_get_db)

    captured = {}

    def fake_create_account(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=99)

    monkeypatch.setattr(register_module.crud, "create_account", fake_create_account)

    assert engine.save_to_database(result) is True
    assert captured["cookies"] == "a=1; z=3"
