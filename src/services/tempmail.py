"""临时邮箱服务实现（支持多供应商调用方式）。"""

import logging
import re
import secrets
import string
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from .tempmail_catalog import get_tempmail_provider_meta, normalize_tempmail_provider
from ..config.constants import OTP_CODE_PATTERN
from ..core.http_client import HTTPClient, RequestConfig

logger = logging.getLogger(__name__)


class TempmailService(BaseEmailService):
    """多供应商临时邮箱服务。"""

    _MAIL_TM_LIKE_PROVIDERS = {"mail_tm", "mail_gw"}
    _POLL_INTERVAL = 3

    def __init__(self, config: Optional[Dict[str, Any]] = None, name: Optional[str] = None):
        super().__init__(EmailServiceType.TEMPMAIL, str(name or "tempmail_service"))

        source = dict(config or {})
        provider = normalize_tempmail_provider(source.get("provider"))
        provider_meta = get_tempmail_provider_meta(provider)
        base_url = str(source.get("base_url") or provider_meta.get("default_base_url") or "").strip()

        default_config = {
            "provider": provider,
            "base_url": base_url,
            "timeout": int(source.get("timeout") or 30),
            "max_retries": int(source.get("max_retries") or 3),
            "proxy_url": source.get("proxy_url"),
        }

        self.config = {**default_config, **source}
        self.config["provider"] = provider
        self.config["base_url"] = base_url

        http_config = RequestConfig(
            timeout=int(self.config.get("timeout") or 30),
            max_retries=int(self.config.get("max_retries") or 3),
        )
        self.http_client = HTTPClient(proxy_url=self.config.get("proxy_url"), config=http_config)

        self._email_cache: Dict[str, Dict[str, Any]] = {}

    def create_email(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """创建临时邮箱。"""
        provider = normalize_tempmail_provider((config or {}).get("provider") or self.config.get("provider"))
        try:
            if provider == "tempmail_lol":
                email_info = self._create_email_tempmail_lol()
            elif provider in self._MAIL_TM_LIKE_PROVIDERS:
                email_info = self._create_email_mail_tm_like(provider, config)
            elif provider == "onesecmail":
                email_info = self._create_email_onesecmail()
            elif provider == "guerrillamail":
                email_info = self._create_email_guerrillamail(config)
            else:
                raise EmailServiceError(f"不支持的临时邮箱供应商: {provider}")

            self._email_cache[email_info["email"]] = email_info
            self.update_status(True)
            return email_info
        except Exception as exc:
            self.update_status(False, exc)
            if isinstance(exc, EmailServiceError):
                raise
            raise EmailServiceError(f"创建临时邮箱失败: {exc}")

    def get_verification_code(
        self,
        email: str,
        email_id: Optional[str] = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        """从目标临时邮箱中轮询获取验证码。"""
        provider = normalize_tempmail_provider(self.config.get("provider"))
        email_info = self._email_cache.get(email) or {}

        if provider == "tempmail_lol":
            token = str(email_id or email_info.get("token") or "").strip()
            return self._poll_tempmail_lol(email, token, timeout, pattern)

        if provider in self._MAIL_TM_LIKE_PROVIDERS:
            token = str(email_info.get("token") or "").strip()
            if not token:
                logger.warning("mail.tm/mail.gw 缺少 token，无法获取验证码")
                return None
            return self._poll_mail_tm_like(email, token, timeout, pattern, otp_sent_at)

        if provider == "onesecmail":
            login = str(email_info.get("login") or "").strip()
            domain = str(email_info.get("domain") or "").strip()
            if not login or not domain:
                logger.warning("1secmail 缺少 login/domain，无法获取验证码")
                return None
            return self._poll_onesecmail(email, login, domain, timeout, pattern)

        if provider == "guerrillamail":
            sid_token = str(email_id or email_info.get("token") or "").strip()
            if not sid_token:
                logger.warning("GuerrillaMail 缺少 sid_token，无法获取验证码")
                return None
            return self._poll_guerrillamail(email, sid_token, timeout, pattern)

        logger.warning("未知供应商 %s，无法获取验证码", provider)
        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        """返回当前实例已创建邮箱缓存。"""
        return list(self._email_cache.values())

    def delete_email(self, email_id: str) -> bool:
        """删除缓存邮箱记录（多数供应商无真实删除接口）。"""
        to_delete: List[str] = []
        for email, info in self._email_cache.items():
            service_id = str(info.get("service_id") or "").strip()
            token = str(info.get("token") or "").strip()
            if email_id and (email_id == service_id or email_id == token):
                to_delete.append(email)

        for email in to_delete:
            del self._email_cache[email]

        return len(to_delete) > 0

    def check_health(self) -> bool:
        """检查当前供应商可用性。"""
        provider = normalize_tempmail_provider(self.config.get("provider"))
        base_url = self._base_url
        if not base_url:
            self.update_status(False, EmailServiceError("base_url 为空"))
            return False

        try:
            if provider == "tempmail_lol":
                self.http_client.get(f"{base_url}/inbox/create", timeout=10)
            elif provider in self._MAIL_TM_LIKE_PROVIDERS:
                self.http_client.get(f"{base_url}/domains", timeout=10)
            elif provider == "onesecmail":
                self.http_client.get(base_url, params={"action": "getDomainList"}, timeout=10)
            elif provider == "guerrillamail":
                self.http_client.get(base_url, params={"f": "get_email_address"}, timeout=10)
            else:
                self.update_status(False, EmailServiceError(f"未知供应商: {provider}"))
                return False

            self.update_status(True)
            return True
        except Exception as exc:
            logger.warning("临时邮箱健康检查失败(provider=%s): %s", provider, exc)
            self.update_status(False, exc)
            return False

    def get_inbox(self, token: str) -> Optional[Dict[str, Any]]:
        """兼容旧流程：仅对 tempmail.lol 返回 inbox 数据。"""
        provider = normalize_tempmail_provider(self.config.get("provider"))
        if provider != "tempmail_lol":
            return None

        try:
            response = self.http_client.get(
                f"{self._base_url}/inbox",
                params={"token": token},
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                return None
            return response.json()
        except Exception as exc:
            logger.error("获取 tempmail.lol inbox 失败: %s", exc)
            return None

    def wait_for_verification_code_with_callback(
        self,
        email: str,
        token: str,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        timeout: int = 120,
    ) -> Optional[str]:
        """带回调的验证码等待接口。"""
        if callback:
            callback({"status": "checking", "email": email, "message": "开始轮询验证码"})

        code = self.get_verification_code(
            email=email,
            email_id=token,
            timeout=timeout,
            pattern=OTP_CODE_PATTERN,
        )

        if callback:
            if code:
                callback({"status": "found", "email": email, "code": code, "message": "找到验证码"})
            else:
                callback({"status": "timeout", "email": email, "message": "等待验证码超时"})

        return code

    @property
    def _base_url(self) -> str:
        return str(self.config.get("base_url") or "").strip().rstrip("/")

    def _create_email_tempmail_lol(self) -> Dict[str, Any]:
        response = self.http_client.post(
            f"{self._base_url}/inbox/create",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={},
        )

        if response.status_code not in (200, 201):
            raise EmailServiceError(f"Tempmail.lol 创建邮箱失败，状态码: {response.status_code}")

        data = response.json()
        email = str(data.get("address") or "").strip()
        token = str(data.get("token") or "").strip()
        if not email or not token:
            raise EmailServiceError("Tempmail.lol 返回数据不完整")

        logger.info("创建 tempmail.lol 邮箱成功: %s", email)
        return {
            "email": email,
            "service_id": token,
            "token": token,
            "provider": "tempmail_lol",
            "created_at": time.time(),
        }

    def _create_email_mail_tm_like(self, provider: str, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        base_url = self._base_url
        preferred_domain = str((config or {}).get("preferred_domain") or self.config.get("preferred_domain") or "").strip()
        address_prefix = str((config or {}).get("address_prefix") or self.config.get("address_prefix") or "").strip()

        domain = self._resolve_mail_tm_domain(base_url, preferred_domain)
        password = self._generate_secret(16)

        create_error = ""
        for _ in range(6):
            local_part = self._generate_local_part(prefix=address_prefix)
            address = f"{local_part}@{domain}"

            response = self.http_client.post(
                f"{base_url}/accounts",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"address": address, "password": password},
            )

            if response.status_code in (200, 201):
                token_response = self.http_client.post(
                    f"{base_url}/token",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={"address": address, "password": password},
                )
                if token_response.status_code != 200:
                    raise EmailServiceError(
                        f"{provider} 获取 token 失败，状态码: {token_response.status_code}"
                    )

                token_data = token_response.json()
                token = str(token_data.get("token") or "").strip()
                if not token:
                    raise EmailServiceError(f"{provider} token 响应缺少 token")

                logger.info("创建 %s 邮箱成功: %s", provider, address)
                return {
                    "email": address,
                    "service_id": address,
                    "token": token,
                    "provider": provider,
                    "login": local_part,
                    "domain": domain,
                    "account_password": password,
                    "created_at": time.time(),
                }

            if response.status_code in (400, 409, 422):
                create_error = f"{provider} 邮箱地址冲突或参数无效"
                continue

            create_error = f"{provider} 创建邮箱失败，状态码: {response.status_code}"
            break

        raise EmailServiceError(create_error or f"{provider} 创建邮箱失败")

    def _create_email_onesecmail(self) -> Dict[str, Any]:
        response = self.http_client.get(
            self._base_url,
            params={"action": "genRandomMailbox", "count": 1},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise EmailServiceError(f"1secmail 创建邮箱失败，状态码: {response.status_code}")

        data = response.json()
        email = ""
        if isinstance(data, list) and data:
            email = str(data[0] or "").strip()

        if "@" not in email:
            raise EmailServiceError("1secmail 返回邮箱地址无效")

        login, domain = email.split("@", 1)
        logger.info("创建 1secmail 邮箱成功: %s", email)
        return {
            "email": email,
            "service_id": email,
            "provider": "onesecmail",
            "login": login,
            "domain": domain,
            "created_at": time.time(),
        }

    def _create_email_guerrillamail(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        base_url = self._base_url
        address_prefix = str((config or {}).get("address_prefix") or self.config.get("address_prefix") or "").strip()

        response = self.http_client.get(
            base_url,
            params={"f": "get_email_address", "agent": "Codex-keygen"},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise EmailServiceError(f"GuerrillaMail 创建会话失败，状态码: {response.status_code}")

        data = response.json() if response.text else {}
        sid_token = str(data.get("sid_token") or "").strip()
        email = str(data.get("email_addr") or "").strip()

        if address_prefix and sid_token:
            set_user_resp = self.http_client.get(
                base_url,
                params={
                    "f": "set_email_user",
                    "email_user": address_prefix,
                    "sid_token": sid_token,
                    "agent": "Codex-keygen",
                },
                headers={"Accept": "application/json"},
            )
            if set_user_resp.status_code == 200:
                set_data = set_user_resp.json() if set_user_resp.text else {}
                sid_token = str(set_data.get("sid_token") or sid_token).strip()
                email = str(set_data.get("email_addr") or email).strip()

        if not sid_token or "@" not in email:
            raise EmailServiceError("GuerrillaMail 返回邮箱数据无效")

        logger.info("创建 GuerrillaMail 邮箱成功: %s", email)
        return {
            "email": email,
            "service_id": sid_token,
            "token": sid_token,
            "provider": "guerrillamail",
            "created_at": time.time(),
        }

    def _poll_tempmail_lol(self, email: str, token: str, timeout: int, pattern: str) -> Optional[str]:
        if not token:
            logger.warning("tempmail.lol token 为空，无法获取验证码")
            return None

        start_time = time.time()
        seen_ids: Set[Any] = set()
        while time.time() - start_time < timeout:
            try:
                response = self.http_client.get(
                    f"{self._base_url}/inbox",
                    params={"token": token},
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    time.sleep(self._POLL_INTERVAL)
                    continue

                data = response.json()
                if not data:
                    return None

                email_list = data.get("emails") if isinstance(data, dict) else []
                if not isinstance(email_list, list):
                    time.sleep(self._POLL_INTERVAL)
                    continue

                for item in email_list:
                    if not isinstance(item, dict):
                        continue
                    msg_id = item.get("date") or item.get("id")
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    code = self._extract_verification_code(
                        sender=str(item.get("from") or ""),
                        subject=str(item.get("subject") or ""),
                        body=str(item.get("body") or ""),
                        html=str(item.get("html") or ""),
                        pattern=pattern,
                    )
                    if code:
                        logger.info("邮箱 %s 获取验证码成功", email)
                        self.update_status(True)
                        return code

            except Exception as exc:
                logger.debug("轮询 tempmail.lol inbox 出错: %s", exc)

            time.sleep(self._POLL_INTERVAL)

        return None

    def _poll_mail_tm_like(
        self,
        email: str,
        token: str,
        timeout: int,
        pattern: str,
        otp_sent_at: Optional[float],
    ) -> Optional[str]:
        base_url = self._base_url
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        start_time = time.time()
        seen_ids: Set[str] = set()

        while time.time() - start_time < timeout:
            try:
                response = self.http_client.get(f"{base_url}/messages", headers=headers)
                if response.status_code != 200:
                    time.sleep(self._POLL_INTERVAL)
                    continue

                data = response.json()
                messages = self._extract_mail_tm_messages(data)
                for item in messages:
                    msg_id = str(item.get("id") or "").strip()
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    detail_resp = self.http_client.get(f"{base_url}/messages/{msg_id}", headers=headers)
                    if detail_resp.status_code != 200:
                        continue

                    detail = detail_resp.json()
                    if otp_sent_at and not self._is_message_new_enough(detail, otp_sent_at):
                        continue

                    sender = self._normalize_sender(detail.get("from"))
                    subject = str(detail.get("subject") or item.get("subject") or "")
                    body = str(detail.get("text") or detail.get("intro") or item.get("intro") or "")
                    html = self._flatten_html(detail.get("html"))

                    code = self._extract_verification_code(sender, subject, body, html, pattern)
                    if code:
                        logger.info("邮箱 %s 获取验证码成功", email)
                        self.update_status(True)
                        return code

            except Exception as exc:
                logger.debug("轮询 mail.tm/mail.gw 出错: %s", exc)

            time.sleep(self._POLL_INTERVAL)

        return None

    def _poll_onesecmail(self, email: str, login: str, domain: str, timeout: int, pattern: str) -> Optional[str]:
        base_url = self._base_url
        start_time = time.time()
        seen_ids: Set[str] = set()

        while time.time() - start_time < timeout:
            try:
                response = self.http_client.get(
                    base_url,
                    params={"action": "getMessages", "login": login, "domain": domain},
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    time.sleep(self._POLL_INTERVAL)
                    continue

                messages = response.json()
                if not isinstance(messages, list):
                    time.sleep(self._POLL_INTERVAL)
                    continue

                for item in messages:
                    if not isinstance(item, dict):
                        continue
                    msg_id = str(item.get("id") or "").strip()
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    detail_resp = self.http_client.get(
                        base_url,
                        params={
                            "action": "readMessage",
                            "login": login,
                            "domain": domain,
                            "id": msg_id,
                        },
                        headers={"Accept": "application/json"},
                    )
                    if detail_resp.status_code != 200:
                        continue

                    detail = detail_resp.json() if detail_resp.text else {}
                    sender = str(detail.get("from") or item.get("from") or "")
                    subject = str(detail.get("subject") or item.get("subject") or "")
                    body = str(detail.get("textBody") or detail.get("body") or "")
                    html = str(detail.get("htmlBody") or "")

                    code = self._extract_verification_code(sender, subject, body, html, pattern)
                    if code:
                        logger.info("邮箱 %s 获取验证码成功", email)
                        self.update_status(True)
                        return code

            except Exception as exc:
                logger.debug("轮询 1secmail 出错: %s", exc)

            time.sleep(self._POLL_INTERVAL)

        return None

    def _poll_guerrillamail(self, email: str, sid_token: str, timeout: int, pattern: str) -> Optional[str]:
        base_url = self._base_url
        start_time = time.time()
        seen_ids: Set[str] = set()

        while time.time() - start_time < timeout:
            try:
                response = self.http_client.get(
                    base_url,
                    params={"f": "get_email_list", "sid_token": sid_token, "offset": 0},
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    time.sleep(self._POLL_INTERVAL)
                    continue

                data = response.json() if response.text else {}
                messages = data.get("list") if isinstance(data, dict) else []
                if not isinstance(messages, list):
                    time.sleep(self._POLL_INTERVAL)
                    continue

                for item in messages:
                    if not isinstance(item, dict):
                        continue
                    msg_id = str(item.get("mail_id") or "").strip()
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    detail_resp = self.http_client.get(
                        base_url,
                        params={"f": "fetch_email", "sid_token": sid_token, "email_id": msg_id},
                        headers={"Accept": "application/json"},
                    )
                    if detail_resp.status_code != 200:
                        continue

                    detail = detail_resp.json() if detail_resp.text else {}
                    sender = str(detail.get("mail_from") or item.get("mail_from") or "")
                    subject = str(detail.get("mail_subject") or item.get("mail_subject") or "")
                    body = str(detail.get("mail_body") or "")
                    html = str(detail.get("mail_body") or "")

                    code = self._extract_verification_code(sender, subject, body, html, pattern)
                    if code:
                        logger.info("邮箱 %s 获取验证码成功", email)
                        self.update_status(True)
                        return code

            except Exception as exc:
                logger.debug("轮询 GuerrillaMail 出错: %s", exc)

            time.sleep(self._POLL_INTERVAL)

        return None

    def _resolve_mail_tm_domain(self, base_url: str, preferred_domain: str) -> str:
        response = self.http_client.get(f"{base_url}/domains", headers={"Accept": "application/json"})
        if response.status_code != 200:
            raise EmailServiceError(f"获取邮箱域名失败，状态码: {response.status_code}")

        data = response.json()
        domains: List[str] = []
        if isinstance(data, dict):
            members = data.get("hydra:member") or data.get("domains") or []
            if isinstance(members, list):
                for item in members:
                    if not isinstance(item, dict):
                        continue
                    if item.get("isActive") is False:
                        continue
                    domain = str(item.get("domain") or "").strip()
                    if domain:
                        domains.append(domain)

        if preferred_domain and preferred_domain in domains:
            return preferred_domain
        if domains:
            return domains[0]
        raise EmailServiceError("可用邮箱域名为空")

    @staticmethod
    def _extract_mail_tm_messages(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            members = data.get("hydra:member") or data.get("messages") or []
            if isinstance(members, list):
                return [item for item in members if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_sender(raw_sender: Any) -> str:
        if isinstance(raw_sender, dict):
            return str(raw_sender.get("address") or raw_sender.get("name") or "")
        return str(raw_sender or "")

    @staticmethod
    def _flatten_html(html: Any) -> str:
        if isinstance(html, str):
            return html
        if isinstance(html, list):
            return "\n".join(str(item) for item in html)
        return str(html or "")

    @staticmethod
    def _extract_verification_code(sender: str, subject: str, body: str, html: str, pattern: str) -> Optional[str]:
        content = "\n".join([str(sender or ""), str(subject or ""), str(body or ""), str(html or "")])
        lowered = content.lower()
        if "openai" not in lowered:
            return None

        match = re.search(pattern, content)
        if not match:
            return None

        return match.group(1)

    @staticmethod
    def _is_message_new_enough(message: Dict[str, Any], otp_sent_at: float) -> bool:
        created_at = message.get("createdAt") or message.get("created_at") or message.get("updatedAt")
        if not created_at:
            return True

        if isinstance(created_at, (int, float)):
            return float(created_at) >= otp_sent_at

        text = str(created_at).strip()
        if not text:
            return True

        text = text.replace("Z", "+00:00")
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(text)
            return dt.timestamp() >= otp_sent_at
        except Exception:
            return True

    @staticmethod
    def _generate_secret(length: int) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _generate_local_part(prefix: str = "", random_length: int = 10) -> str:
        clean_prefix = "".join(ch for ch in (prefix or "").lower() if ch.isalnum())
        if clean_prefix:
            clean_prefix = clean_prefix[:16]

        alphabet = string.ascii_lowercase + string.digits
        random_part = "".join(secrets.choice(alphabet) for _ in range(random_length))
        return f"{clean_prefix}{random_part}" if clean_prefix else random_part
