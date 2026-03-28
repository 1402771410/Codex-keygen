from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.upload.sub2api_upload import upload_to_sub2api


class _FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json_data


def _build_account():
    return SimpleNamespace(
        email="demo@example.com",
        access_token="token-1",
        account_id="acc-1",
        client_id="client-1",
        expires_at=datetime.now(timezone.utc),
        workspace_id="org-1",
        refresh_token="refresh-1",
    )


def test_upload_to_sub2api_includes_group_name(monkeypatch):
    captured_payload = {}

    def _fake_post(url, json, headers, proxies, timeout, impersonate):
        captured_payload.update(json)
        return _FakeResponse(201)

    monkeypatch.setattr("src.core.upload.sub2api_upload.cffi_requests.post", _fake_post)

    success, message = upload_to_sub2api(
        [_build_account()],
        api_url="https://example.com",
        api_key="k-1",
        group_name="Group-A",
    )

    assert success is True
    assert "成功上传" in message
    assert captured_payload.get("group_name") == "Group-A"


def test_upload_to_sub2api_retry_without_group_on_400(monkeypatch):
    payloads = []

    def _fake_post(url, json, headers, proxies, timeout, impersonate):
        payloads.append(json)
        if len(payloads) == 1:
            return _FakeResponse(400, json_data={"message": "unknown field: group_name"})
        return _FakeResponse(201)

    monkeypatch.setattr("src.core.upload.sub2api_upload.cffi_requests.post", _fake_post)

    success, message = upload_to_sub2api(
        [_build_account()],
        api_url="https://example.com",
        api_key="k-1",
        group_name="Group-B",
    )

    assert success is True
    assert "已忽略分组" in message
    assert payloads[0].get("group_name") == "Group-B"
    assert "group_name" not in payloads[1]
