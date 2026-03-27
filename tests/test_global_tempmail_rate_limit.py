import time

from src.core.register import RegistrationEngine
from src.services import EmailServiceType


class _DummyEmailService:
    def __init__(self, payload=None):
        self.service_type = EmailServiceType.TEMPMAIL
        self.payload = payload or {"email": "demo@example.com"}
        self.calls = 0

    def create_email(self):
        self.calls += 1
        return self.payload


def _build_engine_stub():
    engine = RegistrationEngine.__new__(RegistrationEngine)
    engine.logs = []
    engine.email = None
    engine.email_info = None
    engine.check_cancelled = None
    engine.use_global_tempmail_limit = True

    def _log(message, level="info"):
        engine.logs.append((level, message))

    engine._log = _log
    return engine


def test_wait_for_global_tempmail_quota_waits_then_passes(monkeypatch):
    engine = _build_engine_stub()
    engine.check_cancelled = lambda: False

    waits = iter([3, 0])
    monkeypatch.setattr(engine, "_reserve_global_tempmail_slot", lambda: next(waits))

    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    ok = RegistrationEngine._wait_for_global_tempmail_quota(engine)
    assert ok is True
    assert slept == [3]
    assert any("达到限流" in msg for _, msg in engine.logs)
    assert any("冷却结束" in msg for _, msg in engine.logs)


def test_wait_for_global_tempmail_quota_honors_cancel(monkeypatch):
    engine = _build_engine_stub()
    engine.check_cancelled = lambda: True
    monkeypatch.setattr(engine, "_reserve_global_tempmail_slot", lambda: 5)

    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    ok = RegistrationEngine._wait_for_global_tempmail_quota(engine)
    assert ok is False
    assert slept == []
    assert any("终止全局临时邮箱冷却等待" in msg for _, msg in engine.logs)


def test_create_email_skips_creation_when_quota_wait_failed(monkeypatch):
    engine = _build_engine_stub()
    service = _DummyEmailService()
    engine.email_service = service

    monkeypatch.setattr(engine, "_wait_for_global_tempmail_quota", lambda: False)

    ok = RegistrationEngine._create_email(engine)
    assert ok is False
    assert service.calls == 0


def test_create_email_success_with_quota_wait(monkeypatch):
    engine = _build_engine_stub()
    service = _DummyEmailService({"email": "ok@example.com"})
    engine.email_service = service

    monkeypatch.setattr(engine, "_wait_for_global_tempmail_quota", lambda: True)

    ok = RegistrationEngine._create_email(engine)
    assert ok is True
    assert service.calls == 1
    assert engine.email == "ok@example.com"
