"""邮箱服务模块。"""

from .base import (
    BaseEmailService,
    EmailServiceError,
    EmailServiceStatus,
    EmailServiceFactory,
    EmailServiceType,
    create_email_service,
)
from .tempmail import TempmailService

# 仅保留临时邮箱服务
EmailServiceFactory.register(EmailServiceType.TEMPMAIL, TempmailService)

__all__ = [
    "BaseEmailService",
    "EmailServiceError",
    "EmailServiceStatus",
    "EmailServiceFactory",
    "create_email_service",
    "EmailServiceType",
    "TempmailService",
]
