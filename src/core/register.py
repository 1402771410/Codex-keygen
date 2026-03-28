"""
注册流程引擎
从 main.py 中提取并重构的注册流程
"""

import re
import json
import time
import logging
import secrets
import string
from collections import deque
from threading import Lock
from typing import Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass
from datetime import datetime

from curl_cffi import requests as cffi_requests

from .openai.oauth import OAuthManager, OAuthStart
from .http_client import OpenAIHTTPClient, HTTPClientError
from ..services import EmailServiceFactory, BaseEmailService, EmailServiceType
from ..database import crud
from ..database.session import get_db
from ..config.constants import (
    OPENAI_API_ENDPOINTS,
    OPENAI_PAGE_TYPES,
    generate_random_user_info,
    OTP_CODE_PATTERN,
    DEFAULT_PASSWORD_LENGTH,
    PASSWORD_CHARSET,
    AccountStatus,
    TaskStatus,
)
from ..config.settings import get_settings


logger = logging.getLogger(__name__)

# 全局临时邮箱限流：5 分钟最多创建 25 个邮箱地址
GLOBAL_TEMPMAIL_LIMIT_WINDOW_SECONDS = 5 * 60
GLOBAL_TEMPMAIL_LIMIT_MAX_CREATES = 25
_global_tempmail_create_timestamps: deque[float] = deque()
_global_tempmail_limit_lock = Lock()


@dataclass
class RegistrationResult:
    """注册结果"""
    success: bool
    email: str = ""
    password: str = ""  # 注册密码
    account_id: str = ""
    workspace_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""  # 会话令牌
    cookies: str = ""  # 完整 Cookie 串
    error_message: str = ""
    logs: Optional[list] = None
    metadata: Optional[Dict[str, Any]] = None
    source: str = "register"  # 'register' 或 'login'，区分账号来源

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "workspace_id": self.workspace_id,
            "access_token": self.access_token[:20] + "..." if self.access_token else "",
            "refresh_token": self.refresh_token[:20] + "..." if self.refresh_token else "",
            "id_token": self.id_token[:20] + "..." if self.id_token else "",
            "session_token": self.session_token[:20] + "..." if self.session_token else "",
            "error_message": self.error_message,
            "logs": self.logs or [],
            "metadata": self.metadata or {},
            "source": self.source,
        }


@dataclass
class SignupFormResult:
    """提交注册表单的结果"""
    success: bool
    page_type: str = ""  # 响应中的 page.type 字段
    is_existing_account: bool = False  # 是否为已注册账号
    response_data: Optional[Dict[str, Any]] = None  # 完整的响应数据
    error_message: str = ""


@dataclass
class OTPProbeResult:
    """真实 OTP 探测结果。"""

    success: bool
    stage: str
    message: str
    email: str = ""
    is_existing_account: bool = False


class RegistrationEngine:
    """
    注册引擎
    负责协调邮箱服务、OAuth 流程和 OpenAI API 调用
    """

    OTP_RETRY_MAX_ATTEMPTS = 3

    def __init__(
        self,
        email_service: BaseEmailService,
        proxy_url: Optional[str] = None,
        callback_logger: Optional[Callable[[str], None]] = None,
        task_uuid: Optional[str] = None,
        use_global_tempmail_limit: bool = False,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ):
        """
        初始化注册引擎

        Args:
            email_service: 邮箱服务实例
            proxy_url: 代理 URL
            callback_logger: 日志回调函数
            task_uuid: 任务 UUID（用于数据库记录）
            use_global_tempmail_limit: 是否启用全局临时邮箱限流
            check_cancelled: 检查任务取消状态回调
        """
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda msg: logger.info(msg))
        self.task_uuid = task_uuid
        self.use_global_tempmail_limit = bool(use_global_tempmail_limit)
        self.check_cancelled = check_cancelled

        # 创建 HTTP 客户端
        self.http_client = OpenAIHTTPClient(proxy_url=proxy_url)

        # 创建 OAuth 管理器
        settings = get_settings()
        self.oauth_manager = OAuthManager(
            client_id=settings.openai_client_id,
            auth_url=settings.openai_auth_url,
            token_url=settings.openai_token_url,
            redirect_uri=settings.openai_redirect_uri,
            scope=settings.openai_scope,
            proxy_url=proxy_url  # 传递代理配置
        )

        # 状态变量
        self.email: Optional[str] = None
        self.password: Optional[str] = None  # 注册密码
        self.email_info: Optional[Dict[str, Any]] = None
        self.oauth_start: Optional[OAuthStart] = None
        self.session: Optional[cffi_requests.Session] = None
        self.session_token: Optional[str] = None  # 会话令牌
        self.logs: list = []
        self._otp_sent_at: Optional[float] = None  # OTP 发送时间戳
        self._is_existing_account: bool = False  # 是否为已注册账号（用于自动登录）

    def _is_cancelled(self) -> bool:
        """检查任务是否已取消。"""
        if not self.check_cancelled:
            return False

        try:
            return bool(self.check_cancelled())
        except Exception as exc:
            logger.warning(f"检查任务取消状态失败: {exc}")
            return False

    def _reserve_global_tempmail_slot(self) -> int:
        """
        尝试预留全局临时邮箱创建配额。

        Returns:
            int: 0 表示可立即创建；>0 表示需等待的秒数。
        """
        now = time.time()
        window_start = now - GLOBAL_TEMPMAIL_LIMIT_WINDOW_SECONDS

        with _global_tempmail_limit_lock:
            while _global_tempmail_create_timestamps and _global_tempmail_create_timestamps[0] <= window_start:
                _global_tempmail_create_timestamps.popleft()

            if len(_global_tempmail_create_timestamps) < GLOBAL_TEMPMAIL_LIMIT_MAX_CREATES:
                _global_tempmail_create_timestamps.append(now)
                return 0

            first_timestamp = _global_tempmail_create_timestamps[0]
            wait_seconds = int(max(1, first_timestamp + GLOBAL_TEMPMAIL_LIMIT_WINDOW_SECONDS - now))
            return wait_seconds

    def _wait_for_global_tempmail_quota(self) -> bool:
        """等待全局临时邮箱配额可用。"""
        while True:
            wait_seconds = self._reserve_global_tempmail_slot()
            if wait_seconds <= 0:
                return True

            self._log(
                f"全局临时邮箱达到限流（5 分钟最多 {GLOBAL_TEMPMAIL_LIMIT_MAX_CREATES} 个），"
                f"等待冷却 {wait_seconds} 秒后重试...",
                "warning",
            )

            remaining = wait_seconds
            while remaining > 0:
                if self._is_cancelled():
                    self._log("任务已取消，终止全局临时邮箱冷却等待", "warning")
                    return False

                sleep_seconds = min(remaining, 5)
                time.sleep(sleep_seconds)
                remaining -= sleep_seconds

            self._log("全局临时邮箱冷却结束，继续创建邮箱")

    def _log(self, message: str, level: str = "info"):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"

        # 添加到日志列表
        self.logs.append(log_message)

        # 调用回调函数
        if self.callback_logger:
            self.callback_logger(log_message)

        # 记录到数据库（如果有关联任务）
        if self.task_uuid:
            try:
                with get_db() as db:
                    crud.append_task_log(db, self.task_uuid, log_message)
            except Exception as e:
                logger.warning(f"记录任务日志失败: {e}")

        # 根据级别记录到日志系统
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _generate_password(self, length: int = DEFAULT_PASSWORD_LENGTH) -> str:
        """生成随机密码"""
        return ''.join(secrets.choice(PASSWORD_CHARSET) for _ in range(length))

    def _check_ip_location(self) -> Tuple[bool, Optional[str]]:
        """检查 IP 地理位置"""
        try:
            return self.http_client.check_ip_location()
        except Exception as e:
            self._log(f"检查 IP 地理位置失败: {e}", "error")
            return False, None

    def _create_email(self) -> bool:
        """创建邮箱"""
        try:
            if self.use_global_tempmail_limit:
                if not self._wait_for_global_tempmail_quota():
                    return False

            self._log(f"正在创建 {self.email_service.service_type.value} 邮箱...")
            self.email_info = self.email_service.create_email()

            if not self.email_info or "email" not in self.email_info:
                self._log("创建邮箱失败: 返回信息不完整", "error")
                return False

            self.email = self.email_info["email"]
            self._log(f"成功创建邮箱: {self.email}")
            return True

        except Exception as e:
            self._log(f"创建邮箱失败: {e}", "error")
            return False

    def _start_oauth(self) -> bool:
        """开始 OAuth 流程"""
        try:
            self._log("开始 OAuth 授权流程...")
            self.oauth_start = self.oauth_manager.start_oauth()
            self._log(f"OAuth URL 已生成: {self.oauth_start.auth_url[:80]}...")
            return True
        except Exception as e:
            self._log(f"生成 OAuth URL 失败: {e}", "error")
            return False

    def _init_session(self) -> bool:
        """初始化会话"""
        try:
            self.session = self.http_client.session
            return True
        except Exception as e:
            self._log(f"初始化会话失败: {e}", "error")
            return False

    def _is_password_page_type(self, page_type: str) -> bool:
        """判断是否为需要提交密码的页面类型。"""
        normalized = (page_type or "").strip().lower()
        candidates = {
            OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION"],
            OPENAI_PAGE_TYPES.get("PASSWORD_REGISTRATION_ALT", ""),
        }
        return normalized in {item for item in candidates if item}

    def _is_login_password_page_type(self, page_type: str) -> bool:
        """判断是否为登录挑战页（需要触发无密 OTP）。"""
        normalized = (page_type or "").strip().lower()
        candidates = {
            OPENAI_PAGE_TYPES["PASSWORD_REGISTRATION"],
            OPENAI_PAGE_TYPES.get("PASSWORD_REGISTRATION_ALT", ""),
            OPENAI_PAGE_TYPES.get("LOGIN_PASSWORD", ""),
        }
        return normalized in {item for item in candidates if item}

    def _is_otp_page_type(self, page_type: str) -> bool:
        """判断是否为 OTP 验证页面。"""
        return (page_type or "").strip().lower() == OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"]

    def _get_device_id(self) -> Optional[str]:
        """获取 Device ID"""
        if not self.oauth_start:
            return None

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                if not self.session:
                    self.session = self.http_client.session

                session = self.session
                if not session:
                    self._log("获取 Device ID 失败: 会话未初始化", "error")
                    return None

                response = session.get(
                    self.oauth_start.auth_url,
                    timeout=20
                )
                did = session.cookies.get("oai-did")

                if did:
                    self._log(f"Device ID: {did}")
                    return did

                self._log(
                    f"获取 Device ID 失败: 未返回 oai-did Cookie (HTTP {response.status_code}, 第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error"
                )
            except Exception as e:
                self._log(
                    f"获取 Device ID 失败: {e} (第 {attempt}/{max_attempts} 次)",
                    "warning" if attempt < max_attempts else "error"
                )

            if attempt < max_attempts:
                time.sleep(attempt)
                self.http_client.close()
                self.session = self.http_client.session

        return None

    def _check_sentinel(self, did: str) -> Optional[str]:
        """检查 Sentinel 拦截"""
        try:
            sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

            response = self.http_client.post(
                OPENAI_API_ENDPOINTS["sentinel"],
                headers={
                    "origin": "https://sentinel.openai.com",
                    "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                    "content-type": "text/plain;charset=UTF-8",
                },
                data=sen_req_body,
            )

            if response.status_code == 200:
                sen_token = response.json().get("token")
                self._log(f"Sentinel token 获取成功")
                return sen_token
            else:
                self._log(f"Sentinel 检查失败: {response.status_code}", "warning")
                return None

        except Exception as e:
            self._log(f"Sentinel 检查异常: {e}", "warning")
            return None

    def _submit_signup_form(
        self,
        did: str,
        sen_token: Optional[str],
        *,
        update_existing_state: bool = True,
        screen_hint: str = "signup",
    ) -> SignupFormResult:
        """
        提交注册表单

        Returns:
            SignupFormResult: 提交结果，包含账号状态判断
        """
        try:
            if not self.session:
                return SignupFormResult(success=False, error_message="会话未初始化")

            session = self.session
            hint = (screen_hint or "signup").strip()
            signup_body = json.dumps({
                "username": {
                    "value": self.email,
                    "kind": "email",
                },
                "screen_hint": hint,
            })

            referer = "https://auth.openai.com/create-account"
            if hint == "login":
                referer = "https://auth.openai.com/log-in"

            headers = {
                "referer": referer,
                "accept": "application/json",
                "content-type": "application/json",
            }

            if sen_token:
                sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'
                headers["openai-sentinel-token"] = sentinel

            response = session.post(
                OPENAI_API_ENDPOINTS["signup"],
                headers=headers,
                data=signup_body,
            )

            self._log(f"提交注册表单状态: {response.status_code}")

            if response.status_code != 200:
                return SignupFormResult(
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}"
                )

            # 解析响应判断账号状态
            try:
                response_data = response.json()
                page_type = response_data.get("page", {}).get("type", "")
                self._log(f"响应页面类型: {page_type}")

                # 判断是否为已注册账号
                is_existing = self._is_otp_page_type(page_type)

                if is_existing:
                    self._log("检测到已注册账号，将自动切换到登录流程")
                    if update_existing_state:
                        self._is_existing_account = True

                return SignupFormResult(
                    success=True,
                    page_type=page_type,
                    is_existing_account=is_existing,
                    response_data=response_data
                )

            except Exception as parse_error:
                self._log(f"解析响应失败: {parse_error}", "warning")
                # 无法解析，默认成功
                return SignupFormResult(success=True)

        except Exception as e:
            self._log(f"提交注册表单失败: {e}", "error")
            return SignupFormResult(success=False, error_message=str(e))

    def _submit_password(self, password: str, *, allow_existing: bool = False) -> bool:
        """提交密码步骤（注册/登录复用）。"""
        try:
            if not self.session:
                self._log("提交密码失败: 会话未初始化", "error")
                return False

            session = self.session
            register_body = json.dumps({
                "password": password,
                "username": self.email
            })

            response = session.post(
                OPENAI_API_ENDPOINTS["register"],
                headers={
                    "referer": "https://auth.openai.com/create-account/password",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                data=register_body,
            )

            self._log(f"提交密码状态: {response.status_code}")

            if response.status_code == 200:
                return True

            error_text = response.text[:500]
            self._log(f"密码提交失败: {error_text}", "warning")

            error_msg = ""
            error_code = ""
            try:
                error_json = response.json()
                error_msg = str(error_json.get("error", {}).get("message", ""))
                error_code = str(error_json.get("error", {}).get("code", ""))
            except Exception:
                pass

            is_existing = (
                "already" in error_msg.lower()
                or "exists" in error_msg.lower()
                or error_code == "user_exists"
            )
            if is_existing:
                if allow_existing:
                    self._log("检测到账号已存在，继续后续登录流程", "warning")
                    return True

                self._log(f"邮箱 {self.email} 可能已在 OpenAI 注册过", "error")
                self._mark_email_as_registered()

            return False

        except Exception as e:
            self._log(f"提交密码失败: {e}", "error")
            return False

    def _register_password(self) -> Tuple[bool, Optional[str]]:
        """注册密码"""
        try:
            # 生成密码
            password = self._generate_password()
            self.password = password  # 保存密码到实例变量
            self._log(f"生成密码: {password}")

            if not self._submit_password(password, allow_existing=False):
                return False, None

            return True, password

        except Exception as e:
            self._log(f"密码注册失败: {e}", "error")
            return False, None

    def _mark_email_as_registered(self):
        """标记邮箱为已注册状态（用于防止重复尝试）"""
        try:
            email = self.email
            if not email:
                return

            with get_db() as db:
                # 检查是否已存在该邮箱的记录
                existing = crud.get_account_by_email(db, email)
                if not existing:
                    # 创建一个失败记录，标记该邮箱已注册过
                    crud.create_account(
                        db,
                        email=email,
                        password="",  # 空密码表示未成功注册
                        email_service=self.email_service.service_type.value,
                        email_service_id=self.email_info.get("service_id") if self.email_info else None,
                        status="failed",
                        extra_data={"register_failed_reason": "email_already_registered_on_openai"}
                    )
                    self._log(f"已在数据库中标记邮箱 {self.email} 为已注册状态")
        except Exception as e:
            logger.warning(f"标记邮箱状态失败: {e}")

    def _send_verification_code(self) -> bool:
        """发送验证码"""
        try:
            if not self.session:
                self._log("发送验证码失败: 会话未初始化", "error")
                return False

            session = self.session

            # 记录发送时间戳
            self._otp_sent_at = time.time()

            response = session.get(
                OPENAI_API_ENDPOINTS["send_otp"],
                headers={
                    "referer": "https://auth.openai.com/create-account/password",
                    "accept": "application/json",
                },
            )

            self._log(f"验证码发送状态: {response.status_code}")
            return response.status_code == 200

        except Exception as e:
            self._log(f"发送验证码失败: {e}", "error")
            return False

    def _get_verification_code(self, otp_purpose: Optional[str] = None) -> Optional[str]:
        """获取验证码"""
        try:
            email = self.email
            if not email:
                self._log("获取验证码失败: 邮箱为空", "error")
                return None

            if otp_purpose:
                effective_purpose = str(otp_purpose).strip().lower()
            else:
                effective_purpose = "login" if self._is_existing_account else "create"

            self._log(f"正在等待邮箱 {email} 的验证码（用途: {effective_purpose}）...")

            email_service_config = getattr(self.email_service, "config", None)
            if isinstance(email_service_config, dict):
                email_service_config["otp_purpose"] = effective_purpose
                sender_keyword = str(email_service_config.get("sender_keyword") or "").strip()
                subject_keyword = str(email_service_config.get("subject_keyword") or "").strip()
                if effective_purpose == "login" and sender_keyword:
                    self._log("登录场景下已忽略固定发件人过滤，避免因发件人变化导致漏码", "warning")
                    email_service_config["sender_keyword"] = ""
                if effective_purpose == "login" and subject_keyword:
                    self._log("登录场景下已忽略固定主题过滤，避免因模板变化导致漏码", "warning")
                    email_service_config["subject_keyword"] = ""

            email_id = self.email_info.get("service_id") if self.email_info else None
            code = self.email_service.get_verification_code(
                email=email,
                email_id=str(email_id or ""),
                timeout=120,
                pattern=OTP_CODE_PATTERN,
                otp_sent_at=self._otp_sent_at,
            )

            if code:
                self._log(f"成功获取验证码: {code}")
                return code
            else:
                self._log("等待验证码超时", "error")
                return None

        except Exception as e:
            self._log(f"获取验证码失败: {e}", "error")
            return None

    def _wait_verification_code_with_retry(
        self,
        *,
        otp_purpose: str,
        stage_label: str,
        resend_callback: Optional[Callable[[], bool]] = None,
        max_attempts: Optional[int] = None,
        send_before_first_attempt: bool = False,
    ) -> Optional[str]:
        """等待验证码，超时后按上限重发。"""
        total_attempts = int(max_attempts or self.OTP_RETRY_MAX_ATTEMPTS or 3)
        if total_attempts < 1:
            total_attempts = 1

        for attempt in range(1, total_attempts + 1):
            should_send = send_before_first_attempt or attempt > 1
            if should_send:
                if not resend_callback:
                    self._log(f"{stage_label} 未配置验证码发送函数，无法重试", "error")
                    return None

                self._log(f"{stage_label} 发送验证码（第 {attempt}/{total_attempts} 次）...")
                send_ok = bool(resend_callback())
                self._log(
                    f"{stage_label} 发送状态（第 {attempt}/{total_attempts} 次）: {'成功' if send_ok else '失败'}"
                )
                if not send_ok:
                    if attempt < total_attempts:
                        self._log(
                            f"{stage_label} 本次发送失败，准备第 {attempt + 1}/{total_attempts} 次重试",
                            "warning",
                        )
                    continue

            code = self._get_verification_code(otp_purpose=otp_purpose)
            if code:
                return code

            if attempt < total_attempts:
                self._log(
                    f"{stage_label} 等待验证码超时，准备重发（第 {attempt + 1}/{total_attempts} 次）",
                    "warning",
                )

        self._log(f"{stage_label} 重试 {total_attempts} 次后仍未获取到验证码", "error")
        return None

    def _validate_verification_code(self, code: str) -> bool:
        """验证验证码"""
        try:
            if not self.session:
                self._log("验证验证码失败: 会话未初始化", "error")
                return False

            session = self.session
            code_body = f'{{"code":"{code}"}}'

            response = session.post(
                OPENAI_API_ENDPOINTS["validate_otp"],
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                data=code_body,
            )

            self._log(f"验证码校验状态: {response.status_code}")
            return response.status_code == 200

        except Exception as e:
            self._log(f"验证验证码失败: {e}", "error")
            return False

    def _create_user_account(self) -> bool:
        """创建用户账户"""
        try:
            session = self.session
            if session is None:
                self._log("创建账户失败: 会话未初始化", "error")
                return False

            user_info = generate_random_user_info()
            self._log(f"生成用户信息: {user_info['name']}, 生日: {user_info['birthdate']}")
            create_account_body = json.dumps(user_info)

            response = session.post(
                OPENAI_API_ENDPOINTS["create_account"],
                headers={
                    "referer": "https://auth.openai.com/about-you",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                data=create_account_body,
            )

            self._log(f"账户创建状态: {response.status_code}")

            if response.status_code != 200:
                self._log(f"账户创建失败: {response.text[:200]}", "warning")
                return False

            return True

        except Exception as e:
            self._log(f"创建账户失败: {e}", "error")
            return False

    def _reset_session_for_fallback_login(self) -> bool:
        """重置为全新 HTTP 会话，避免注册态 Cookie 干扰降级登录。"""
        try:
            self.http_client.close()
        except Exception:
            pass

        try:
            self.http_client = OpenAIHTTPClient(proxy_url=self.proxy_url)
            self.session = self.http_client.session
            return True
        except Exception as e:
            self._log(f"重置降级登录会话失败: {e}", "error")
            return False

    def _send_passwordless_login_otp(self) -> bool:
        """登录挑战页触发无密 OTP（不提交密码）。"""
        try:
            session = self.session
            email = self.email
            if session is None or not email:
                self._log("触发无密 OTP 失败: 会话或邮箱缺失", "error")
                return False

            payload_variants = [
                {},
                {"email": email},
                {"username": email},
                {"username": {"value": email, "kind": "email"}},
            ]

            last_status = 0
            last_body = ""
            for idx, payload in enumerate(payload_variants, start=1):
                response = session.post(
                    OPENAI_API_ENDPOINTS["send_otp_passwordless"],
                    headers={
                        "referer": "https://auth.openai.com/log-in",
                        "accept": "application/json",
                        "content-type": "application/json",
                    },
                    data=json.dumps(payload),
                )
                last_status = response.status_code
                last_body = response.text[:200]
                self._log(f"登录无密 OTP 触发状态: {response.status_code} (尝试 {idx}/{len(payload_variants)})")
                if response.status_code == 200:
                    self._otp_sent_at = time.time()
                    return True

            self._log(
                f"登录无密 OTP 触发失败: HTTP {last_status}, 响应: {last_body}",
                "warning",
            )
            return False

        except Exception as e:
            self._log(f"触发无密 OTP 失败: {e}", "error")
            return False

    def _resend_verification_code_from_otp_page(self) -> bool:
        """在 OTP 页面上下文下重发验证码。"""
        try:
            if not self.session:
                self._log("OTP 页面重发失败: 会话未初始化", "error")
                return False

            response = self.session.get(
                OPENAI_API_ENDPOINTS["send_otp"],
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                },
            )

            self._log(f"OTP 页面重发状态: {response.status_code}")
            if response.status_code == 200:
                self._otp_sent_at = time.time()
                return True
            return False

        except Exception as e:
            self._log(f"OTP 页面重发失败: {e}", "error")
            return False

    def _run_second_oauth_login_after_create(self) -> Optional[str]:
        """Workspace 缺失时执行降级登录，刷新含 workspace 的授权 Cookie。"""
        self._log("13.1 Workspace 缺失，触发降级登录流程（新 OAuth + 新会话 + OTP）...")

        if not self._reset_session_for_fallback_login():
            self._log("降级登录失败：无法初始化新会话", "error")
            return None

        if not self._start_oauth():
            self._log("降级登录失败：开始 OAuth 流程失败", "error")
            return None

        did = self._get_device_id()
        if not did:
            self._log("降级登录失败：获取 Device ID 失败", "error")
            return None

        sen_token = self._check_sentinel(did)
        if sen_token:
            self._log("降级登录: Sentinel 检查通过")
        else:
            self._log("降级登录: Sentinel 检查失败或未启用", "warning")

        signup_result = self._submit_signup_form(
            did,
            sen_token,
            update_existing_state=False,
            screen_hint="login",
        )
        if not signup_result.success:
            self._log(f"降级登录失败：提交邮箱失败: {signup_result.error_message}", "error")
            return None

        if self._is_login_password_page_type(signup_result.page_type):
            self._log("降级登录: 进入 login_password 流程，直接触发无密 OTP")
            if not self._send_passwordless_login_otp():
                self._log("降级登录失败：无密 OTP 触发失败", "error")
                return None
            self._log("13.1 降级登录 OTP 发送状态: 已通过无密触发接口发送")
            fallback_resend_callback: Callable[[], bool] = self._resend_verification_code_from_otp_page
            fallback_send_before_first = False
        elif self._is_otp_page_type(signup_result.page_type) or signup_result.is_existing_account:
            self._log(
                f"降级登录: 页面类型 {signup_result.page_type or 'unknown'}，按 OTP 验证流程继续",
                "warning",
            )
            self._otp_sent_at = time.time()
            self._log("13.1 降级登录 OTP 发送状态: 已由页面自动触发")
            fallback_resend_callback = self._resend_verification_code_from_otp_page
            fallback_send_before_first = False
        else:
            self._log(
                f"降级登录失败：不支持的页面类型 {signup_result.page_type or 'unknown'}",
                "error",
            )
            return None

        self._log("降级登录: 等待验证码...")
        code = self._wait_verification_code_with_retry(
            otp_purpose="login",
            stage_label="13.1 降级登录 OTP",
            resend_callback=fallback_resend_callback,
            max_attempts=self.OTP_RETRY_MAX_ATTEMPTS,
            send_before_first_attempt=fallback_send_before_first,
        )
        if not code:
            self._log("降级登录失败：获取验证码失败（已达到重试上限）", "error")
            return None

        self._log("降级登录: 验证验证码...")
        if not self._validate_verification_code(code):
            self._log("降级登录失败：验证验证码失败", "error")
            return None

        workspace_id = self._get_workspace_id()
        if not workspace_id:
            self._log("降级登录失败：刷新后仍无法获取 Workspace ID", "error")
            return None

        self._log(f"降级登录完成，已获取 Workspace ID: {workspace_id}")
        return workspace_id

    def _get_workspace_id(self) -> Optional[str]:
        """获取 Workspace ID"""
        try:
            session = self.session
            if session is None:
                self._log("获取 Workspace ID 失败: 会话未初始化", "error")
                return None

            auth_cookie = session.cookies.get("oai-client-auth-session")
            if not auth_cookie:
                self._log("未能获取到授权 Cookie", "error")
                return None

            # 解码 JWT
            import base64
            import json as json_module

            try:
                segments = auth_cookie.split(".")
                if len(segments) < 1:
                    self._log("授权 Cookie 格式错误", "error")
                    return None

                # 解码第一个 segment
                payload = segments[0]
                pad = "=" * ((4 - (len(payload) % 4)) % 4)
                decoded = base64.urlsafe_b64decode((payload + pad).encode("ascii"))
                auth_json = json_module.loads(decoded.decode("utf-8"))

                workspaces = auth_json.get("workspaces") or []
                if not workspaces:
                    self._log("授权 Cookie 里没有 workspace 信息", "error")
                    return None

                workspace_id = str((workspaces[0] or {}).get("id") or "").strip()
                if not workspace_id:
                    self._log("无法解析 workspace_id", "error")
                    return None

                self._log(f"Workspace ID: {workspace_id}")
                return workspace_id

            except Exception as e:
                self._log(f"解析授权 Cookie 失败: {e}", "error")
                return None

        except Exception as e:
            self._log(f"获取 Workspace ID 失败: {e}", "error")
            return None

    def _select_workspace(self, workspace_id: str) -> Optional[str]:
        """选择 Workspace"""
        try:
            session = self.session
            if session is None:
                self._log("选择 Workspace 失败: 会话未初始化", "error")
                return None

            select_body = f'{{"workspace_id":"{workspace_id}"}}'

            response = session.post(
                OPENAI_API_ENDPOINTS["select_workspace"],
                headers={
                    "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                    "content-type": "application/json",
                },
                data=select_body,
            )

            if response.status_code != 200:
                self._log(f"选择 workspace 失败: {response.status_code}", "error")
                self._log(f"响应: {response.text[:200]}", "warning")
                return None

            continue_url = str((response.json() or {}).get("continue_url") or "").strip()
            if not continue_url:
                self._log("workspace/select 响应里缺少 continue_url", "error")
                return None

            self._log(f"Continue URL: {continue_url[:100]}...")
            return continue_url

        except Exception as e:
            self._log(f"选择 Workspace 失败: {e}", "error")
            return None

    def _follow_redirects(self, start_url: str) -> Optional[str]:
        """跟随重定向链，寻找回调 URL"""
        try:
            session = self.session
            if session is None:
                self._log("跟随重定向失败: 会话未初始化", "error")
                return None

            current_url = start_url
            max_redirects = 6

            for i in range(max_redirects):
                self._log(f"重定向 {i+1}/{max_redirects}: {current_url[:100]}...")

                response = session.get(
                    current_url,
                    allow_redirects=False,
                    timeout=15
                )

                location = response.headers.get("Location") or ""

                # 如果不是重定向状态码，停止
                if response.status_code not in [301, 302, 303, 307, 308]:
                    self._log(f"非重定向状态码: {response.status_code}")
                    break

                if not location:
                    self._log("重定向响应缺少 Location 头")
                    break

                # 构建下一个 URL
                import urllib.parse
                next_url = urllib.parse.urljoin(current_url, location)

                # 检查是否包含回调参数
                if "code=" in next_url and "state=" in next_url:
                    self._log(f"找到回调 URL: {next_url[:100]}...")
                    return next_url

                current_url = next_url

            self._log("未能在重定向链中找到回调 URL", "error")
            return None

        except Exception as e:
            self._log(f"跟随重定向失败: {e}", "error")
            return None

    def _handle_oauth_callback(self, callback_url: str) -> Optional[Dict[str, Any]]:
        """处理 OAuth 回调"""
        try:
            if not self.oauth_start:
                self._log("OAuth 流程未初始化", "error")
                return None

            self._log("处理 OAuth 回调...")
            token_info = self.oauth_manager.handle_callback(
                callback_url=callback_url,
                expected_state=self.oauth_start.state,
                code_verifier=self.oauth_start.code_verifier
            )

            self._log("OAuth 授权成功")
            return token_info

        except Exception as e:
            self._log(f"处理 OAuth 回调失败: {e}", "error")
            return None

    def run_otp_probe(self) -> OTPProbeResult:
        """执行最小化真实 OTP 探测（收到验证码即结束）。"""
        stage = "start"

        try:
            self._log("=" * 60)
            self._log("开始真实 OTP 探测流程（收到 OTP 即停止）")
            self._log("=" * 60)

            stage = "check_ip"
            self._log("探测 1/10: 检查 IP 地理位置...")
            ip_ok, location = self._check_ip_location()
            if not ip_ok:
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message=f"IP 地理位置不支持: {location}",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            self._log(f"探测 IP 位置: {location}")

            stage = "create_email"
            self._log("探测 2/10: 创建邮箱...")
            if not self._create_email():
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message="创建邮箱失败",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            stage = "init_session"
            self._log("探测 3/10: 初始化会话...")
            if not self._init_session():
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message="初始化会话失败",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            stage = "start_oauth"
            self._log("探测 4/10: 开始 OAuth 授权流程...")
            if not self._start_oauth():
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message="开始 OAuth 流程失败",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            stage = "get_device_id"
            self._log("探测 5/10: 获取 Device ID...")
            did = self._get_device_id()
            if not did:
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message="获取 Device ID 失败",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            stage = "check_sentinel"
            self._log("探测 6/10: 检查 Sentinel 拦截...")
            sen_token = self._check_sentinel(did)
            if sen_token:
                self._log("探测 Sentinel 检查通过")
            else:
                self._log("探测 Sentinel 检查失败或未启用", "warning")

            stage = "submit_signup"
            self._log("探测 7/10: 提交注册表单...")
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message=f"提交注册表单失败: {signup_result.error_message}",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            if self._is_existing_account:
                stage = "send_otp"
                self._log("探测 8/10: 已注册账号，跳过发送验证码，使用自动发送 OTP")
                self._otp_sent_at = time.time()
            else:
                stage = "register_password"
                self._log("探测 8/10: 注册密码...")
                password_ok, _password = self._register_password()
                if not password_ok:
                    return OTPProbeResult(
                        success=False,
                        stage=stage,
                        message="注册密码失败",
                        email=self.email or "",
                        is_existing_account=self._is_existing_account,
                    )

                stage = "send_otp"
                self._log("探测 9/10: 发送验证码...")
                if not self._send_verification_code():
                    return OTPProbeResult(
                        success=False,
                        stage=stage,
                        message="发送验证码失败",
                        email=self.email or "",
                        is_existing_account=self._is_existing_account,
                    )

            stage = "wait_otp"
            self._log("探测 10/10: 等待验证码...")
            otp_purpose = "login" if self._is_existing_account else "create"
            resend_callback = self._resend_verification_code_from_otp_page if self._is_existing_account else self._send_verification_code
            code = self._wait_verification_code_with_retry(
                otp_purpose=otp_purpose,
                stage_label="探测 10/10 验证码",
                resend_callback=resend_callback,
                max_attempts=self.OTP_RETRY_MAX_ATTEMPTS,
                send_before_first_attempt=False,
            )
            if not code:
                return OTPProbeResult(
                    success=False,
                    stage=stage,
                    message=f"获取验证码失败（已重试 {self.OTP_RETRY_MAX_ATTEMPTS} 次）",
                    email=self.email or "",
                    is_existing_account=self._is_existing_account,
                )

            return OTPProbeResult(
                success=True,
                stage="otp_received",
                message="已确认收到真实 OpenAI OTP",
                email=self.email or "",
                is_existing_account=self._is_existing_account,
            )

        except Exception as e:
            failed_stage = stage if stage != "start" else "unexpected"
            self._log(f"真实 OTP 探测异常({failed_stage}): {e}", "error")
            return OTPProbeResult(
                success=False,
                stage=failed_stage,
                message=str(e),
                email=self.email or "",
                is_existing_account=self._is_existing_account,
            )

    def run(self) -> RegistrationResult:
        """
        执行完整的注册流程

        支持已注册账号自动登录：
        - 如果检测到邮箱已注册，自动切换到登录流程
        - 已注册账号跳过：设置密码、发送验证码、创建用户账户
        - 共用步骤：获取验证码、验证验证码、Workspace 和 OAuth 回调

        Returns:
            RegistrationResult: 注册结果
        """
        result = RegistrationResult(success=False, logs=self.logs)

        try:
            self._log("=" * 60)
            self._log("开始注册流程")
            self._log("=" * 60)

            # 1. 检查 IP 地理位置
            self._log("1. 检查 IP 地理位置...")
            ip_ok, location = self._check_ip_location()
            if not ip_ok:
                result.error_message = f"IP 地理位置不支持: {location}"
                self._log(f"IP 检查失败: {location}", "error")
                return result

            self._log(f"IP 位置: {location}")

            # 2. 创建邮箱
            self._log("2. 创建邮箱...")
            if not self._create_email():
                result.error_message = "创建邮箱失败"
                return result

            result.email = self.email or ""

            # 3. 初始化会话
            self._log("3. 初始化会话...")
            if not self._init_session():
                result.error_message = "初始化会话失败"
                return result

            # 4. 开始 OAuth 流程
            self._log("4. 开始 OAuth 授权流程...")
            if not self._start_oauth():
                result.error_message = "开始 OAuth 流程失败"
                return result

            # 5. 获取 Device ID
            self._log("5. 获取 Device ID...")
            did = self._get_device_id()
            if not did:
                result.error_message = "获取 Device ID 失败"
                return result

            # 6. 检查 Sentinel 拦截
            self._log("6. 检查 Sentinel 拦截...")
            sen_token = self._check_sentinel(did)
            if sen_token:
                self._log("Sentinel 检查通过")
            else:
                self._log("Sentinel 检查失败或未启用", "warning")

            # 7. 提交注册表单 + 解析响应判断账号状态
            self._log("7. 提交注册表单...")
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                result.error_message = f"提交注册表单失败: {signup_result.error_message}"
                return result

            # 8. [已注册账号跳过] 注册密码
            if self._is_existing_account:
                self._log("8. [已注册账号] 跳过密码设置，OTP 已自动发送")
            else:
                self._log("8. 注册密码...")
                password_ok, password = self._register_password()
                if not password_ok:
                    result.error_message = "注册密码失败"
                    return result

            # 9. [已注册账号跳过] 发送验证码
            if self._is_existing_account:
                self._log("9. [已注册账号] 跳过发送验证码，使用自动发送的 OTP")
                # 已注册账号的 OTP 在提交表单时已自动发送，记录时间戳
                self._otp_sent_at = time.time()
            else:
                self._log("9. 发送验证码...")
                if not self._send_verification_code():
                    result.error_message = "发送验证码失败"
                    return result

            # 10. 获取验证码
            self._log("10. 等待验证码...")
            otp_purpose = "login" if self._is_existing_account else "create"
            resend_callback = self._resend_verification_code_from_otp_page if self._is_existing_account else self._send_verification_code
            code = self._wait_verification_code_with_retry(
                otp_purpose=otp_purpose,
                stage_label="10. 验证码",
                resend_callback=resend_callback,
                max_attempts=self.OTP_RETRY_MAX_ATTEMPTS,
                send_before_first_attempt=False,
            )
            if not code:
                result.error_message = f"获取验证码失败（已重试 {self.OTP_RETRY_MAX_ATTEMPTS} 次）"
                return result

            # 11. 验证验证码
            self._log("11. 验证验证码...")
            if not self._validate_verification_code(code):
                result.error_message = "验证验证码失败"
                return result

            workspace_id: Optional[str] = None
            account_created = False

            # 12. [已注册账号跳过] 创建用户账户
            if self._is_existing_account:
                self._log("12. [已注册账号] 跳过创建用户账户")
            else:
                self._log("12. 创建用户账户...")
                if not self._create_user_account():
                    result.error_message = "创建用户账户失败"
                    return result
                account_created = True

            # 13. 获取 Workspace ID
            self._log("13. 获取 Workspace ID...")
            workspace_id = self._get_workspace_id()
            if not workspace_id and account_created:
                self._log("13. 授权 Cookie 缺少 workspace，尝试降级登录刷新...")
                workspace_id = self._run_second_oauth_login_after_create()

            if not workspace_id:
                if account_created:
                    result.metadata = {
                        "account_created": True,
                        "error_stage": "workspace_fallback_login",
                    }
                result.error_message = "获取 Workspace ID 失败"
                return result

            result.workspace_id = workspace_id

            # 14. 选择 Workspace
            self._log("14. 选择 Workspace...")
            continue_url = self._select_workspace(workspace_id)
            if not continue_url:
                result.error_message = "选择 Workspace 失败"
                return result

            # 15. 跟随重定向链
            self._log("15. 跟随重定向链...")
            callback_url = self._follow_redirects(continue_url)
            if not callback_url:
                result.error_message = "跟随重定向链失败"
                return result

            # 16. 处理 OAuth 回调
            self._log("16. 处理 OAuth 回调...")
            token_info = self._handle_oauth_callback(callback_url)
            if not token_info:
                result.error_message = "处理 OAuth 回调失败"
                return result

            # 提取账户信息
            result.account_id = token_info.get("account_id", "")
            result.access_token = token_info.get("access_token", "")
            result.refresh_token = token_info.get("refresh_token", "")
            result.id_token = token_info.get("id_token", "")
            result.password = self.password or ""  # 保存密码（已注册账号为空）

            # 设置来源标记
            result.source = "login" if self._is_existing_account else "register"

            # 尝试获取 session_token 从 cookie
            session = self.session
            session_cookie = session.cookies.get("__Secure-next-auth.session-token") if session else None
            if session_cookie:
                self.session_token = session_cookie
                result.session_token = session_cookie
                self._log(f"获取到 Session Token")

            # 记录 Cookie 快照，避免后续会话变更影响持久化内容。
            result.cookies = self._serialize_session_cookies()

            # 17. 完成
            self._log("=" * 60)
            if self._is_existing_account:
                self._log("登录成功! (已注册账号)")
            else:
                self._log("注册成功!")
            self._log(f"邮箱: {result.email}")
            self._log(f"Account ID: {result.account_id}")
            self._log(f"Workspace ID: {result.workspace_id}")
            self._log("=" * 60)

            result.success = True
            result.metadata = {
                "email_service": self.email_service.service_type.value,
                "proxy_used": self.proxy_url,
                "registered_at": datetime.now().isoformat(),
                "is_existing_account": self._is_existing_account,
            }

            return result

        except Exception as e:
            self._log(f"注册过程中发生未预期错误: {e}", "error")
            result.error_message = str(e)
            return result

    def save_to_database(self, result: RegistrationResult) -> bool:
        """
        保存注册结果到数据库

        Args:
            result: 注册结果

        Returns:
            是否保存成功
        """
        if not result.success:
            return False

        try:
            # 获取默认 client_id
            settings = get_settings()
            cookies = result.cookies or self._serialize_session_cookies()

            with get_db() as db:
                # 保存账户信息
                account = crud.create_account(
                    db,
                    email=result.email,
                    password=result.password,
                    client_id=settings.openai_client_id,
                    session_token=result.session_token,
                    email_service=self.email_service.service_type.value,
                    email_service_id=self.email_info.get("service_id") if self.email_info else None,
                    account_id=result.account_id,
                    workspace_id=result.workspace_id,
                    access_token=result.access_token,
                    refresh_token=result.refresh_token,
                    id_token=result.id_token,
                    cookies=cookies or None,
                    proxy_used=self.proxy_url,
                    extra_data=result.metadata,
                    source=result.source
                )

                self._log(f"账户已保存到数据库，ID: {account.id}")
                return True

        except Exception as e:
            self._log(f"保存到数据库失败: {e}", "error")
            return False

    def _serialize_session_cookies(self) -> str:
        """序列化当前会话 Cookie 为 `k=v; k2=v2` 字符串。"""
        if not self.session:
            return ""

        cookie_jar = getattr(self.session, "cookies", None)
        if not cookie_jar:
            return ""

        cookie_map: Dict[str, Any] = {}
        if hasattr(cookie_jar, "get_dict"):
            try:
                cookie_map = cookie_jar.get_dict() or {}
            except Exception:
                cookie_map = {}
        elif hasattr(cookie_jar, "items"):
            try:
                cookie_map = dict(cookie_jar.items())
            except Exception:
                cookie_map = {}

        if not cookie_map:
            return ""

        cookie_pairs = []
        for key in sorted(cookie_map.keys()):
            value = cookie_map.get(key)
            if value is None:
                continue
            name = str(key).strip()
            if not name:
                continue
            cookie_pairs.append(f"{name}={value}")

        return "; ".join(cookie_pairs)
