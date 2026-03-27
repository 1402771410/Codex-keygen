from src.services.tempmail import TempmailService


def test_pop3_alias_create_email_generates_plus_alias():
    service = TempmailService(
        {
            "provider": "pop3_alias",
            "base_email": "123456@225.com",
            "pop3_host": "pop.225.com",
            "pop3_port": 995,
            "pop3_username": "123456@225.com",
            "pop3_password": "secret",
            "alias_length": 8,
            "use_ssl": True,
        }
    )

    email_info = service.create_email()
    assert email_info["email"].startswith("123456+")
    assert email_info["email"].endswith("@225.com")
    assert email_info["provider"] == "pop3_alias"


def test_pop3_alias_create_email_supports_digits_charset():
    service = TempmailService(
        {
            "provider": "pop3_alias",
            "base_email": "123456@225.com",
            "pop3_host": "pop.225.com",
            "pop3_port": 995,
            "pop3_username": "123456@225.com",
            "pop3_password": "secret",
            "alias_length": 6,
            "alias_charset": "digits",
            "use_ssl": True,
        }
    )

    email_info = service.create_email()
    local = email_info["email"].split("@", 1)[0]
    suffix = local.split("+", 1)[1]
    assert len(suffix) == 6
    assert suffix.isdigit()
    assert email_info["alias_charset"] == "digits"


def test_pop3_alias_get_code_delegates_to_pop3_service(monkeypatch):
    service = TempmailService(
        {
            "provider": "pop3_alias",
            "base_email": "123456@225.com",
            "pop3_host": "pop.225.com",
            "pop3_port": 995,
            "pop3_username": "123456@225.com",
            "pop3_password": "secret",
            "alias_length": 8,
            "use_ssl": True,
        }
    )

    email_info = service.create_email()

    pop3_service = service._build_pop3_service(service._resolve_runtime_config())
    monkeypatch.setattr(pop3_service, "get_verification_code", lambda **kwargs: "654321")
    monkeypatch.setattr(service, "_build_pop3_service", lambda runtime_config: pop3_service)

    code = service.get_verification_code(
        email=email_info["email"],
        timeout=60,
    )

    assert code == "654321"
