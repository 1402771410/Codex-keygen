from typing import Any, cast

from src.core.register import RegistrationEngine, SignupFormResult
from src.services.base import BaseEmailService


class _FakeEmailService:
    def __init__(self):
        self.config = {"sender_keyword": "noreply@fixed-domain.test"}
        self.calls = []

    def get_verification_code(self, **kwargs):
        self.calls.append(kwargs)
        return None


def _build_engine_with_log_collector():
    engine = RegistrationEngine.__new__(RegistrationEngine)
    log_records = []

    def _capture_log(message: str, level: str = "info"):
        log_records.append((level, message))

    engine._log = _capture_log
    return engine, log_records


def test_wait_verification_code_with_retry_resends_until_success():
    engine, logs = _build_engine_with_log_collector()

    results = iter([None, "654321"])
    engine._get_verification_code = lambda otp_purpose=None: next(results)

    send_calls = []

    def _resend_callback():
        send_calls.append("sent")
        return True

    code = engine._wait_verification_code_with_retry(
        otp_purpose="login",
        stage_label="10. 验证码",
        resend_callback=_resend_callback,
        max_attempts=3,
        send_before_first_attempt=False,
    )

    assert code == "654321"
    assert len(send_calls) == 1
    assert any("等待验证码超时" in message for _, message in logs)


def test_wait_verification_code_with_retry_reports_failure_after_three_attempts():
    engine, logs = _build_engine_with_log_collector()

    engine._get_verification_code = lambda otp_purpose=None: None

    send_calls = []

    def _resend_callback():
        send_calls.append("sent")
        return True

    code = engine._wait_verification_code_with_retry(
        otp_purpose="create",
        stage_label="10. 验证码",
        resend_callback=_resend_callback,
        max_attempts=3,
        send_before_first_attempt=False,
    )

    assert code is None
    assert len(send_calls) == 2
    assert any("重试 3 次后仍未获取到验证码" in message for _, message in logs)


def test_get_verification_code_login_ignores_fixed_sender_filter():
    engine, logs = _build_engine_with_log_collector()
    fake_email_service = _FakeEmailService()

    engine.email = "w110715cun931vm6in@2925.com"
    engine.email_info = {"service_id": "mailbox-1"}
    engine._otp_sent_at = 1234567.0
    engine._is_existing_account = True
    engine.email_service = cast(BaseEmailService, fake_email_service)

    code = engine._get_verification_code(otp_purpose="login")

    assert code is None
    assert fake_email_service.config["sender_keyword"] == ""
    assert fake_email_service.calls
    assert fake_email_service.calls[0]["email"] == "w110715cun931vm6in@2925.com"
    assert any("忽略固定发件人过滤" in message for _, message in logs)


def test_fallback_otp_page_logs_send_status_and_uses_retry_helper():
    engine, logs = _build_engine_with_log_collector()
    captured_retry_kwargs: dict[str, Any] = {}

    engine._reset_session_for_fallback_login = lambda: True
    engine._start_oauth = lambda: True
    engine._get_device_id = lambda: "did-1"
    engine._check_sentinel = lambda did: None
    engine._submit_signup_form = lambda *_args, **_kwargs: SignupFormResult(
        success=True,
        page_type="otp",
        is_existing_account=True,
    )
    engine._is_login_password_page_type = lambda page_type: False
    engine._is_otp_page_type = lambda page_type: True
    engine._send_passwordless_login_otp = lambda: True

    def _fake_wait_retry(**kwargs):
        captured_retry_kwargs.update(kwargs)
        return "778899"

    engine._wait_verification_code_with_retry = _fake_wait_retry
    engine._validate_verification_code = lambda code: True
    engine._get_workspace_id = lambda: "ws-1"

    workspace_id = engine._run_second_oauth_login_after_create()

    assert workspace_id == "ws-1"
    assert captured_retry_kwargs["stage_label"] == "13.1 降级登录 OTP"
    assert captured_retry_kwargs["send_before_first_attempt"] is False
    assert any("13.1 降级登录 OTP 发送状态" in message for _, message in logs)
