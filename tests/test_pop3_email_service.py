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
        lambda: [{"subject": "OpenAI", "from": "noreply@openai.com", "body": "Your code is 123456"}],
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
