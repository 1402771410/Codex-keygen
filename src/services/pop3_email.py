"""普通邮箱（POP3）服务实现。"""

from __future__ import annotations

import poplib
import re
import socket
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..config.constants import OTP_CODE_PATTERN


def _to_bool(value: Any, default: bool = True) -> bool:
    """将不同输入转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


class Pop3EmailService(BaseEmailService):
    """POP3 邮箱验证码读取服务。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, name: Optional[str] = None):
        super().__init__(EmailServiceType.POP3, str(name or "pop3_email_service"))

        source = dict(config or {})
        use_ssl = _to_bool(source.get("use_ssl"), default=True)
        default_port = 995 if use_ssl else 110

        self.config: Dict[str, Any] = {
            "host": str(source.get("host") or "").strip(),
            "port": int(source.get("port") or default_port),
            "username": str(source.get("username") or "").strip(),
            "password": str(source.get("password") or ""),
            "email": str(source.get("email") or source.get("username") or "").strip(),
            "use_ssl": use_ssl,
            "poll_interval": max(2, int(source.get("poll_interval") or 5)),
            "timeout": max(15, int(source.get("timeout") or 120)),
            "max_messages": max(1, int(source.get("max_messages") or 30)),
            "subject_keyword": str(source.get("subject_keyword") or "").strip(),
            "sender_keyword": str(source.get("sender_keyword") or "").strip(),
        }

        self._validate_required()
        self._email_cache: Dict[str, Dict[str, Any]] = {}

    def create_email(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """普通邮箱模式不创建新地址，直接返回用户提供的邮箱信息。"""
        if config:
            runtime = dict(self.config)
            runtime.update(config)
            self.config = runtime
            self._validate_required()

        email_address = str(self.config.get("email") or "").strip()
        service_id = f"pop3:{self.config['username']}@{self.config['host']}:{self.config['port']}"
        info = {
            "email": email_address,
            "service_id": service_id,
            "username": self.config["username"],
            "host": self.config["host"],
            "port": self.config["port"],
            "use_ssl": self.config["use_ssl"],
        }
        self._email_cache[email_address] = info
        self.update_status(True)
        return info

    def get_verification_code(
        self,
        email: str,
        email_id: Optional[str] = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        """轮询 POP3 收件箱并提取验证码。"""
        _ = email_id  # POP3 模式无需 email_id

        poll_interval = max(2, int(self.config.get("poll_interval") or 5))
        effective_timeout = max(15, int(timeout or self.config.get("timeout") or 120))
        start = time.time()

        while time.time() - start < effective_timeout:
            try:
                messages = self._fetch_latest_messages()
                for message in messages:
                    recipient = str(message.get("to") or "")
                    if recipient and email and not self._recipient_matches(recipient, email):
                        continue

                    if not self._match_filters(message):
                        continue

                    timestamp = message.get("timestamp")
                    if otp_sent_at and isinstance(timestamp, float) and timestamp < otp_sent_at:
                        continue

                    body = str(message.get("body") or "")
                    match = re.search(pattern, body)
                    if match:
                        self.update_status(True)
                        return match.group(1)
            except Exception as exc:
                # 读取失败继续轮询，避免瞬时网络抖动直接终止。
                self.update_status(False, exc)

            time.sleep(poll_interval)

        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        _ = kwargs
        return list(self._email_cache.values())

    def delete_email(self, email_id: str) -> bool:
        _ = email_id
        # 普通邮箱不应由系统删除。
        return False

    def check_health(self) -> bool:
        """检查 POP3 连通性与登录可用性。"""
        try:
            client = self._connect()
            try:
                client.stat()
            finally:
                self._safe_quit(client)
            self.update_status(True)
            return True
        except Exception as exc:
            self.update_status(False, exc)
            return False

    def _validate_required(self) -> None:
        required_fields = ["host", "port", "username", "password", "email"]
        missing = [field for field in required_fields if not self.config.get(field)]
        if missing:
            raise EmailServiceError(f"POP3 配置缺少必填项: {', '.join(missing)}")

    def _connect(self):
        host = str(self.config["host"])
        port = int(self.config["port"])
        username = str(self.config["username"])
        password = str(self.config["password"])
        timeout_seconds = max(15, int(self.config.get("timeout") or 120))

        if _to_bool(self.config.get("use_ssl"), default=True):
            client = poplib.POP3_SSL(host, port, timeout=timeout_seconds)
        else:
            client = poplib.POP3(host, port, timeout=timeout_seconds)

        client.user(username)
        client.pass_(password)
        return client

    def _fetch_latest_messages(self) -> List[Dict[str, Any]]:
        client = self._connect()
        messages: List[Dict[str, Any]] = []
        max_messages = max(1, int(self.config.get("max_messages") or 30))

        try:
            _, listings, _ = client.list()
            if not listings:
                return []

            numbers = []
            for item in listings:
                try:
                    numbers.append(int(item.decode("utf-8", errors="ignore").split()[0]))
                except Exception:
                    continue

            for number in sorted(numbers, reverse=True)[:max_messages]:
                try:
                    _, lines, _ = client.retr(number)
                    raw_message = b"\r\n".join(lines)
                    message = BytesParser(policy=policy.default).parsebytes(raw_message)
                    body = self._extract_body_text(message)
                    messages.append(
                        {
                            "subject": str(message.get("subject") or ""),
                            "from": str(message.get("from") or ""),
                            "to": str(message.get("to") or ""),
                            "body": body,
                            "timestamp": self._parse_message_timestamp(message),
                        }
                    )
                except Exception:
                    continue
        finally:
            self._safe_quit(client)

        return messages

    def _match_filters(self, message: Dict[str, Any]) -> bool:
        subject_keyword = str(self.config.get("subject_keyword") or "").strip()
        sender_keyword = str(self.config.get("sender_keyword") or "").strip()

        subject = str(message.get("subject") or "")
        sender = str(message.get("from") or "")

        if subject_keyword and subject_keyword not in subject:
            return False
        if sender_keyword and sender_keyword not in sender:
            return False
        return True

    @staticmethod
    def _recipient_matches(recipient_text: str, target_email: str) -> bool:
        recipient_norm = str(recipient_text or "").strip().lower()
        target_norm = str(target_email or "").strip().lower()
        if not recipient_norm or not target_norm:
            return True
        return target_norm in recipient_norm

    @staticmethod
    def _extract_body_text(message) -> str:
        if message.is_multipart():
            chunks: List[str] = []
            for part in message.walk():
                content_type = str(part.get_content_type() or "").lower()
                if content_type not in {"text/plain", "text/html"}:
                    continue
                try:
                    chunks.append(part.get_content() or "")
                except Exception:
                    continue
            return "\n".join(chunks)

        try:
            return str(message.get_content() or "")
        except Exception:
            payload = message.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode("utf-8", errors="ignore")
            return str(payload or "")

    @staticmethod
    def _parse_message_timestamp(message) -> Optional[float]:
        raw_date = str(message.get("date") or "").strip()
        if not raw_date:
            return None
        try:
            parsed = parsedate_to_datetime(raw_date)
            if isinstance(parsed, datetime):
                return parsed.timestamp()
        except Exception:
            return None
        return None

    @staticmethod
    def _safe_quit(client) -> None:
        try:
            client.quit()
        except (OSError, EOFError, poplib.error_proto, socket.error):
            pass
