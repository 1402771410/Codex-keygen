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
    _OTP_CONTEXT_KEYWORDS = (
        "verification code",
        "one-time code",
        "one time code",
        "one-time passcode",
        "one time passcode",
        "otp",
        "code",
        "验证码",
    )

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
            "ignored_codes": list(source.get("ignored_codes") or []),
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
        best_stale_candidate: Optional[tuple[int, int, float, str]] = None

        while time.time() - start < effective_timeout:
            try:
                messages = self._fetch_latest_messages()
                purpose = str(self.config.get("otp_purpose") or "").strip().lower()
                skew_tolerance = max(0, int(self.config.get("clock_skew_tolerance") or 120))
                ignored_codes = {
                    str(code).strip()
                    for code in (self.config.get("ignored_codes") or [])
                    if str(code).strip()
                }
                fresh_candidates: List[tuple[int, int, float, str]] = []
                stale_candidates: List[tuple[int, int, float, str]] = []

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
                    scored_codes = self._extract_scored_codes(subject, body, pattern)
                    for code_score, code in scored_codes:
                        if code in ignored_codes:
                            continue
                        code_ts = timestamp if isinstance(timestamp, float) else 0.0
                        candidate = (purpose_score, code_score, code_ts, code)
                        if message_is_stale:
                            stale_candidates.append(candidate)
                        else:
                            fresh_candidates.append(candidate)

                if fresh_candidates:
                    fresh_candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
                    self.update_status(True)
                    return fresh_candidates[0][3]

                if stale_candidates:
                    stale_candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
                    if not best_stale_candidate or stale_candidates[0] > best_stale_candidate:
                        best_stale_candidate = stale_candidates[0]
            except Exception as exc:
                # 读取失败继续轮询，避免瞬时网络抖动直接终止。
                self.update_status(False, exc)

            time.sleep(poll_interval)

        if best_stale_candidate:
            logger.warning("未命中新邮件时间戳，已回退使用最新可匹配验证码")
            self.update_status(True)
            return best_stale_candidate[3]

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

    def _extract_scored_codes(self, subject: str, body: str, pattern: str) -> List[tuple[int, str]]:
        code_scores: Dict[str, int] = {}
        keyword_group = r"(?:verification\s*code|one[-\s]?time\s*(?:pass)?code|otp|验证码|code)"

        for source, text in (("subject", subject), ("body", body)):
            if not text:
                continue

            text_norm = self._normalize_text(text)

            for match in re.finditer(pattern, text):
                if match.lastindex:
                    code = match.group(1)
                    span_start, span_end = match.span(1)
                else:
                    code = match.group(0)
                    span_start, span_end = match.span(0)

                code = str(code or "").strip()
                if not code:
                    continue

                pre_ctx = self._normalize_text(text[max(0, span_start - 32):span_start])
                post_ctx = self._normalize_text(text[span_end:min(len(text), span_end + 32)])
                around_ctx = self._normalize_text(text[max(0, span_start - 56):min(len(text), span_end + 56)])

                score = 1
                if source == "body":
                    score += 1
                else:
                    score -= 1

                if "openai" in around_ctx or "chatgpt" in around_ctx:
                    score += 1

                if any(keyword in pre_ctx for keyword in self._OTP_CONTEXT_KEYWORDS):
                    score += 4
                if any(keyword in post_ctx for keyword in self._OTP_CONTEXT_KEYWORDS):
                    score += 3

                if re.search(r"\b(id|order|ticket|summary|reference|ref)\b", pre_ctx):
                    score -= 2

                code_escaped = re.escape(code)
                if re.search(rf"{keyword_group}\D{{0,20}}{code_escaped}", text_norm):
                    score += 8
                elif re.search(rf"{code_escaped}\D{{0,20}}{keyword_group}", text_norm):
                    score += 6

                prev_score = code_scores.get(code, -10**9)
                if score > prev_score:
                    code_scores[code] = score

        if not code_scores:
            return []

        return sorted(((score, code) for code, score in code_scores.items()), key=lambda item: item[0], reverse=True)

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
