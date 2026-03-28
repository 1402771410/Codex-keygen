"""普通邮箱（POP3）服务实现。"""

from __future__ import annotations

import poplib
import re
import socket
import time
import logging
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Dict, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..config.constants import OTP_CODE_PATTERN


logger = logging.getLogger(__name__)


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

    _RECIPIENT_HEADER_FIELDS = (
        "to",
        "delivered_to",
        "x_original_to",
        "envelope_to",
        "cc",
        "resent_to",
        "resent_cc",
    )
    _LOGIN_PURPOSE_HINTS = (
        "if you were not trying to log in to openai",
    )
    _CREATE_PURPOSE_HINTS = (
        "please ignore this email if this wasn't you trying to create a chatgpt account",
    )
    _EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

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
            "otp_purpose": str(source.get("otp_purpose") or "").strip().lower(),
            "clock_skew_tolerance": max(0, int(source.get("clock_skew_tolerance") or 120)),
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
                purpose = str(self.config.get("otp_purpose") or "").strip().lower()
                skew_tolerance = max(0, int(self.config.get("clock_skew_tolerance") or 120))
                matched_codes: List[tuple[int, int, float, str]] = []

                for message in messages:
                    if email and not self._message_targets_email(message, email):
                        continue

                    purpose_score = self._purpose_score(message, purpose)
                    if purpose_score < 0:
                        continue

                    if not self._match_filters(message):
                        continue

                    timestamp = message.get("timestamp")
                    message_is_stale = bool(
                        otp_sent_at
                        and isinstance(timestamp, float)
                        and timestamp < (otp_sent_at - skew_tolerance)
                    )

                    body = str(message.get("body") or "")
                    subject = str(message.get("subject") or "")
                    search_text = f"{subject}\n{body}" if subject else body
                    match = re.search(pattern, search_text)
                    if match:
                        code = match.group(1) if match.lastindex else match.group(0)
                        code_ts = timestamp if isinstance(timestamp, float) else 0.0
                        stale_rank = 0 if message_is_stale else 1
                        matched_codes.append((stale_rank, purpose_score, code_ts, code))

                if matched_codes:
                    matched_codes.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
                    self.update_status(True)
                    best = matched_codes[0]
                    if best[0] == 0 and otp_sent_at:
                        logger.warning("检测到验证码邮件时间戳早于 otp_sent_at，已回退使用最新可匹配邮件")
                    return best[3]
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
                    header_values = {
                        "to": ", ".join([str(item) for item in (message.get_all("to") or [])]),
                        "delivered_to": ", ".join([str(item) for item in (message.get_all("delivered-to") or [])]),
                        "x_original_to": ", ".join([str(item) for item in (message.get_all("x-original-to") or [])]),
                        "envelope_to": ", ".join([str(item) for item in (message.get_all("envelope-to") or [])]),
                        "cc": ", ".join([str(item) for item in (message.get_all("cc") or [])]),
                        "resent_to": ", ".join([str(item) for item in (message.get_all("resent-to") or [])]),
                        "resent_cc": ", ".join([str(item) for item in (message.get_all("resent-cc") or [])]),
                    }
                    messages.append(
                        {
                            "subject": str(message.get("subject") or ""),
                            "from": str(message.get("from") or ""),
                            **header_values,
                            "recipients": self._extract_recipient_addresses(header_values),
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
        subject_keyword = self._normalize_text(str(self.config.get("subject_keyword") or ""))
        sender_keyword = self._normalize_text(str(self.config.get("sender_keyword") or ""))

        subject = self._normalize_text(str(message.get("subject") or ""))
        sender = self._normalize_text(str(message.get("from") or ""))

        if subject_keyword and subject_keyword not in subject:
            return False
        if sender_keyword and sender_keyword not in sender:
            return False
        return True

    def _purpose_score(self, message: Dict[str, Any], purpose: str) -> int:
        purpose_norm = self._normalize_text(purpose)
        if purpose_norm not in {"login", "create", "register"}:
            return 0

        content = self._normalize_text(
            f"{message.get('subject') or ''}\n{message.get('body') or ''}"
        )
        if not content:
            return 0

        login_hit = any(hint in content for hint in self._LOGIN_PURPOSE_HINTS)
        create_hit = any(hint in content for hint in self._CREATE_PURPOSE_HINTS)

        if purpose_norm == "login":
            if create_hit and not login_hit:
                return -1
            return 1 if login_hit else 0

        if login_hit and not create_hit:
            return -1
        return 1 if create_hit else 0

    def _message_targets_email(self, message: Dict[str, Any], target_email: str) -> bool:
        target_norm = self._normalize_email(target_email)
        mailbox_norm = self._normalize_email(self.config.get("email"))
        candidate_targets = [item for item in [target_norm, mailbox_norm] if item]
        if not candidate_targets:
            return True

        recipients = self._extract_recipient_addresses(message)
        if not recipients:
            referenced = self._extract_text_email_addresses(message)
            if not referenced:
                return False
            return any(ref in candidate_targets for ref in referenced)

        return any(self._normalize_email(item) in candidate_targets for item in recipients)

    def _extract_recipient_addresses(self, message: Dict[str, Any]) -> List[str]:
        recipients: List[str] = []
        direct_recipients = message.get("recipients")
        if isinstance(direct_recipients, list):
            for item in direct_recipients:
                normalized = self._normalize_email(item)
                if normalized and normalized not in recipients:
                    recipients.append(normalized)
            if recipients:
                return recipients

        candidate_values: List[str] = []
        for field in self._RECIPIENT_HEADER_FIELDS:
            value = message.get(field)
            if value:
                candidate_values.append(str(value))

        if not candidate_values:
            return []

        parsed = [self._normalize_email(address) for _, address in getaddresses(candidate_values) if address]
        for item in parsed:
            if item and item not in recipients:
                recipients.append(item)

        if recipients:
            return recipients

        for raw in candidate_values:
            for found in self._EMAIL_PATTERN.findall(raw):
                normalized = self._normalize_email(found)
                if normalized and normalized not in recipients:
                    recipients.append(normalized)

        return recipients

    def _extract_text_email_addresses(self, message: Dict[str, Any]) -> List[str]:
        text_parts = [
            str(message.get("subject") or ""),
            str(message.get("body") or ""),
            str(message.get("to") or ""),
            str(message.get("delivered_to") or ""),
            str(message.get("x_original_to") or ""),
        ]
        combined = "\n".join(text_parts)
        candidates: List[str] = []
        for found in self._EMAIL_PATTERN.findall(combined):
            normalized = self._normalize_email(found)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    @staticmethod
    def _normalize_email(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = str(value or "")
        text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()

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
