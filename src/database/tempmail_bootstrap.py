"""临时邮箱预置服务初始化与同步。"""

from typing import Any, Dict, Optional

from .models import EmailService
from ..config.constants import EmailServiceType, TEMPMAIL_GLOBAL_BUILTIN_KEY
from ..services.tempmail_catalog import build_tempmail_builtin_specs, build_tempmail_config


def ensure_builtin_tempmail_services(db, settings: Any) -> None:
    """确保系统预置临时邮箱服务存在。"""
    specs = build_tempmail_builtin_specs(settings)

    for spec in specs:
        service = db.query(EmailService).filter(EmailService.builtin_key == spec["builtin_key"]).first()
        if service is None:
            service = EmailService(
                service_type=EmailServiceType.TEMPMAIL.value,
                provider=spec["provider"],
                name=spec["name"],
                config=spec["config"],
                enabled=spec["enabled"],
                priority=spec["priority"],
                is_builtin=spec["is_builtin"],
                is_immutable=spec["is_immutable"],
                builtin_key=spec["builtin_key"],
            )
            db.add(service)
            continue

        service.service_type = EmailServiceType.TEMPMAIL.value
        service.provider = spec["provider"]
        service.is_builtin = True
        service.is_immutable = bool(spec["is_immutable"])
        service.builtin_key = spec["builtin_key"]

        if service.is_immutable:
            # 固定项完全由系统维护，避免被历史数据污染。
            service.name = spec["name"]
            service.priority = spec["priority"]
            service.enabled = spec["enabled"]
            service.config = spec["config"]
        elif not service.config:
            # 仅在历史配置为空时填充默认值，避免覆盖用户手工调整。
            service.config = spec["config"]

    db.commit()


def sync_global_tempmail_service(
    db,
    settings: Any,
    *,
    base_url: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> None:
    """同步固定全局临时邮箱条目。"""
    service = db.query(EmailService).filter(EmailService.builtin_key == TEMPMAIL_GLOBAL_BUILTIN_KEY).first()
    if service is None:
        ensure_builtin_tempmail_services(db, settings)
        service = db.query(EmailService).filter(EmailService.builtin_key == TEMPMAIL_GLOBAL_BUILTIN_KEY).first()
        if service is None:
            return

    normalized_config = build_tempmail_config(
        {
            "provider": service.provider or "tempmail_lol",
            "base_url": (base_url or getattr(settings, "tempmail_base_url", "") or "").strip(),
            "timeout": int(getattr(settings, "tempmail_timeout", 30) or 30),
            "max_retries": int(getattr(settings, "tempmail_max_retries", 3) or 3),
        },
        settings,
        provider_hint=service.provider or "tempmail_lol",
    )

    service.service_type = EmailServiceType.TEMPMAIL.value
    service.provider = normalized_config.get("provider") or "tempmail_lol"
    service.name = "全局临时邮箱（固定）"
    service.is_builtin = True
    service.is_immutable = True
    service.builtin_key = TEMPMAIL_GLOBAL_BUILTIN_KEY
    service.priority = 0
    service.config = normalized_config
    service.enabled = bool(getattr(settings, "tempmail_enabled", True) if enabled is None else enabled)

    db.commit()


def is_global_tempmail_service(service: EmailService) -> bool:
    """判断是否为固定全局临时邮箱条目。"""
    return str(service.builtin_key or "") == TEMPMAIL_GLOBAL_BUILTIN_KEY


def mutable_fields_for_update(payload: Dict[str, Any]) -> Dict[str, Any]:
    """返回允许更新的字段。"""
    return {
        key: value
        for key, value in payload.items()
        if key in {"name", "config", "enabled", "priority"}
    }
