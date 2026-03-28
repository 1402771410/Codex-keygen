import time

from src.services.pop3_email import Pop3EmailService


def test_pop3_create_email_returns_manual_mailbox_info():
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
        }
    )

    email_info = service.create_email()
    assert email_info["email"] == "user@example.com"
    assert email_info["host"] == "pop.example.com"
    assert email_info["port"] == 995
    assert email_info["use_ssl"] is True


def test_pop3_get_verification_code_extracts_otp(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
        }
    )

    service.create_email()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI",
                "from": "noreply@openai.com",
                "to": "user@example.com",
                "delivered_to": "user@example.com",
                "body": "Your code is 123456",
                "timestamp": time.time(),
            }
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "123456"


def test_pop3_get_verification_code_returns_none_on_timeout(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 15,
        }
    )

    service.create_email()
    monkeypatch.setattr(service, "_fetch_latest_messages", lambda: [])

    code = service.get_verification_code(email="user@example.com", timeout=15)
    assert code is None


def test_pop3_get_verification_code_matches_delivered_to_recipient(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
        }
    )

    service.create_email()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI",
                "from": "noreply@openai.com",
                "to": "",
                "delivered_to": "user@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "Your code is 234567",
                "timestamp": time.time(),
            }
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "234567"


def test_pop3_get_verification_code_filters_recipient_and_otp_purpose(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
            "otp_purpose": "login",
        }
    )

    service.create_email()
    now = time.time()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI verification",
                "from": "noreply@openai.com",
                "to": "other@example.com",
                "delivered_to": "other@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "If you were not trying to log in to OpenAI, code is 111111",
                "timestamp": now,
            },
            {
                "subject": "OpenAI verification",
                "from": "noreply@openai.com",
                "to": "user@example.com",
                "delivered_to": "user@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "Please ignore this email if this wasn’t you trying to create a ChatGPT account. code is 222222",
                "timestamp": now + 1,
            },
            {
                "subject": "OpenAI verification",
                "from": "noreply@openai.com",
                "to": "user@example.com",
                "delivered_to": "user@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "If you were not trying to log in to OpenAI, code is 333333",
                "timestamp": now + 2,
            },
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "333333"

    service.config["otp_purpose"] = "create"
    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "222222"


def test_pop3_get_verification_code_accepts_body_recipient_when_headers_missing(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
            "otp_purpose": "login",
        }
    )

    service.create_email()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI verification",
                "from": "notify-random@openai-mail.example",
                "to": "",
                "delivered_to": "",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "If you were not trying to log in to OpenAI. Recipient: user@example.com, code 456789",
                "timestamp": time.time(),
            }
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "456789"


def test_pop3_get_verification_code_falls_back_to_stale_message_when_no_new_message(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
            "otp_purpose": "login",
            "clock_skew_tolerance": 120,
        }
    )

    service.create_email()
    now = time.time()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI verification",
                "from": "sender-a@openai-mail.example",
                "to": "user@example.com",
                "delivered_to": "user@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": "If you were not trying to log in to OpenAI. code is 987654",
                "timestamp": now - 500,
            }
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30, otp_sent_at=now)
    assert code == "987654"


def test_pop3_get_verification_code_prefers_code_near_otp_context(monkeypatch):
    service = Pop3EmailService(
        {
            "email": "user@example.com",
            "host": "pop.example.com",
            "port": 995,
            "username": "user@example.com",
            "password": "secret",
            "use_ssl": True,
            "poll_interval": 2,
            "timeout": 30,
            "otp_purpose": "login",
        }
    )

    service.create_email()

    monkeypatch.setattr(
        service,
        "_fetch_latest_messages",
        lambda: [
            {
                "subject": "OpenAI activity summary 654321",
                "from": "updates@openai-mail.example",
                "to": "user@example.com",
                "delivered_to": "user@example.com",
                "x_original_to": "",
                "envelope_to": "",
                "cc": "",
                "resent_to": "",
                "resent_cc": "",
                "body": (
                    "Account overview id 654321. "
                    "If you were not trying to log in to OpenAI, your verification code is 112233"
                ),
                "timestamp": time.time(),
            }
        ],
    )

    code = service.get_verification_code(email="user@example.com", timeout=30)
    assert code == "112233"
