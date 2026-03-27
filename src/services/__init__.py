"""邮箱服务模块。"""

from .base import (
    BaseEmailService,
    EmailServiceError,
    EmailServiceStatus,
    EmailServiceFactory,
    EmailServiceType,
    create_email_service,
)
from .pop3_email import Pop3EmailService
from .tempmail import TempmailService

# 邮箱服务注册
EmailServiceFactory.register(EmailServiceType.TEMPMAIL, TempmailService)
EmailServiceFactory.register(EmailServiceType.POP3, Pop3EmailService)

__all__ = [
    "BaseEmailService",
    "EmailServiceError",
    "EmailServiceStatus",
    "EmailServiceFactory",
    "create_email_service",
    "EmailServiceType",
    "Pop3EmailService",
    "TempmailService",
]
