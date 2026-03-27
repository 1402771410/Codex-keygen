import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional

from src.services.tempmail import TempmailService
from src.services.tempmail_catalog import build_tempmail_config


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: Optional[str] = None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> Any:
        return self._payload


class ScenarioHTTPClient:
    def __init__(
        self,
        get_handler: Optional[Callable[[str, Dict[str, Any]], FakeResponse]] = None,
        post_handler: Optional[Callable[[str, Dict[str, Any]], FakeResponse]] = None,
    ):
        self._get_handler = get_handler
        self._post_handler = post_handler
        self.calls = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        if not self._get_handler:
            raise AssertionError(f"Unexpected GET call: {url}")
        return self._get_handler(url, kwargs)

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        if not self._post_handler:
            raise AssertionError(f"Unexpected POST call: {url}")
        return self._post_handler(url, kwargs)


def _settings_stub() -> SimpleNamespace:
    return SimpleNamespace(
        tempmail_base_url="https://api.tempmail.lol/v2",
        tempmail_timeout=30,
        tempmail_max_retries=3,
    )


def test_build_tempmail_config_keeps_auth_and_call_rule_fields() -> None:
    config = build_tempmail_config(
        {
            "provider": "mail_tm",
            "api_url": "https://custom.example/api",
            "api_key": "secret-key",
            "auth_style": "api_key",
            "auth_placement": "query",
            "auth_header_name": "X-Custom-Key",
            "auth_query_key": "access_key",
            "auth_scheme": "Bearer",
            "create_path": "/v1/accounts",
            "domains_path": "/v1/domains",
            "token_path": "/v1/token",
            "messages_path": "/v1/messages",
            "fallback_domain": "fallback.example",
            "api_key_header": "X-Alt-Key",
            "api_key_query_key": "alt_key",
        },
        _settings_stub(),
    )

    assert config["provider"] == "mail_tm"
    assert config["base_url"] == "https://custom.example/api"
    assert config["api_key"] == "secret-key"
    assert config["auth_style"] == "api_key"
    assert config["auth_placement"] == "query"
    assert config["auth_header_name"] == "X-Custom-Key"
    assert config["auth_query_key"] == "access_key"
    assert config["auth_scheme"] == "Bearer"
    assert config["create_path"] == "/v1/accounts"
    assert config["domains_path"] == "/v1/domains"
    assert config["token_path"] == "/v1/token"
    assert config["messages_path"] == "/v1/messages"
    assert config["fallback_domain"] == "fallback.example"
    assert config["api_key_header"] == "X-Alt-Key"
    assert config["api_key_query_key"] == "alt_key"


def test_build_tempmail_config_pop3_alias_strips_http_fields() -> None:
    config = build_tempmail_config(
        {
            "provider": "pop3_alias",
            "timeout": 45,
            "max_retries": 9,
            "base_url": "https://should-not-keep.example",
            "address_prefix": "legacy-prefix",
            "preferred_domain": "legacy-domain.test",
            "base_email": "123456@225.com",
            "pop3_host": "pop.225.com",
            "pop3_port": 995,
            "pop3_username": "123456@225.com",
            "pop3_password": "secret",
            "use_ssl": True,
            "alias_length": 10,
            "alias_charset": "loweralnum",
            "poll_interval": 6,
            "max_messages": 20,
        },
        _settings_stub(),
    )

    assert config["provider"] == "pop3_alias"
    assert config["timeout"] == 45
    assert config["base_email"] == "123456@225.com"
    assert config["pop3_host"] == "pop.225.com"
    assert config["pop3_port"] == 995
    assert config["pop3_username"] == "123456@225.com"
    assert config["pop3_password"] == "secret"
    assert config["use_ssl"] is True
    assert config["alias_length"] == 10
    assert config["alias_charset"] == "loweralnum"
    assert config["poll_interval"] == 6
    assert config["max_messages"] == 20

    assert "base_url" not in config
    assert "max_retries" not in config
    assert "address_prefix" not in config
    assert "preferred_domain" not in config


def test_onesecmail_create_falls_back_after_403_random_mailbox() -> None:
    service = TempmailService({"provider": "onesecmail", "base_url": "https://www.1secmail.com/api/v1/"})

    def _get_handler(_url: str, kwargs: Dict[str, Any]) -> FakeResponse:
        params = kwargs.get("params") or {}
        action = params.get("action")
        if action == "genRandomMailbox":
            return FakeResponse(403, text="forbidden")
        if action == "getDomainList":
            return FakeResponse(200, ["safe-domain.test"])
        raise AssertionError(f"Unexpected action: {action}")

    setattr(service, "http_client", ScenarioHTTPClient(get_handler=_get_handler))
    created = service.create_email({"address_prefix": "otp"})

    assert created["provider"] == "onesecmail"
    assert created["domain"] == "safe-domain.test"
    assert created["email"].endswith("@safe-domain.test")
    assert created["login"].startswith("otp")


def test_mail_tm_like_supports_query_api_key_and_alt_domain_shape() -> None:
    service = TempmailService(
        {
            "provider": "mail_tm",
            "base_url": "https://custom-mail.example",
            "api_key": "custom-key",
            "auth_style": "api_key",
            "auth_placement": "query",
            "auth_query_key": "access_key",
        }
    )

    def _get_handler(url: str, kwargs: Dict[str, Any]) -> FakeResponse:
        params = kwargs.get("params") or {}
        if url.endswith("/domains"):
            assert params.get("access_key") == "custom-key"
            return FakeResponse(200, {"domains": [{"name": "relay-domain.test"}]})
        raise AssertionError(f"Unexpected GET url: {url}")

    def _post_handler(url: str, kwargs: Dict[str, Any]) -> FakeResponse:
        params = kwargs.get("params") or {}
        assert params.get("access_key") == "custom-key"

        if url.endswith("/accounts"):
            address = (kwargs.get("json") or {}).get("address")
            assert isinstance(address, str) and address.endswith("@relay-domain.test")
            return FakeResponse(201, {"id": "acc-1"})

        if url.endswith("/token"):
            return FakeResponse(200, {"token": "jwt-token"})

        raise AssertionError(f"Unexpected POST url: {url}")

    setattr(service, "http_client", ScenarioHTTPClient(get_handler=_get_handler, post_handler=_post_handler))
    created = service.create_email()

    assert created["provider"] == "mail_tm"
    assert created["domain"] == "relay-domain.test"
    assert created["token"] == "jwt-token"


def test_mail_tm_like_prefers_config_domain_when_domains_endpoint_empty() -> None:
    for provider, base_url in (("mail_tm", "https://api.mail.tm"), ("mail_gw", "https://api.mail.gw")):
        service = TempmailService({"provider": provider, "base_url": base_url})

        def _get_handler(url: str, _kwargs: Dict[str, Any]) -> FakeResponse:
            if url.endswith("/domains"):
                return FakeResponse(200, {"hydra:member": []})
            raise AssertionError(f"Unexpected GET url: {url}")

        def _post_handler(url: str, kwargs: Dict[str, Any]) -> FakeResponse:
            if url.endswith("/accounts"):
                address = (kwargs.get("json") or {}).get("address")
                assert isinstance(address, str) and address.endswith("@preferred-domain.test")
                return FakeResponse(201, {"id": "acc-2"})
            if url.endswith("/token"):
                return FakeResponse(200, {"token": "token-2"})
            raise AssertionError(f"Unexpected POST url: {url}")

        setattr(service, "http_client", ScenarioHTTPClient(get_handler=_get_handler, post_handler=_post_handler))
        created = service.create_email({"preferred_domain": "preferred-domain.test"})

        assert created["provider"] == provider
        assert created["domain"] == "preferred-domain.test"
        assert created["email"].endswith("@preferred-domain.test")
