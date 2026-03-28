"""临时邮箱预置服务初始化与同步。"""

from typing import Any, Dict, Optional

from .models import EmailService
from ..config.constants import (
    EmailServiceType,
    TEMPMAIL_GLOBAL_BUILTIN_KEY,
    TEMPMAIL_SELECTION_MODES,
)
from ..services.tempmail_catalog import (
    build_tempmail_builtin_specs,
    build_tempmail_config,
    get_tempmail_provider_meta,
    normalize_tempmail_provider,
)

_RUNTIME_UNSET = object()


def _normalize_selection_mode(value: Optional[str]) -> str:
    normalized = str(value or "single").strip().lower()
    return normalized if normalized in TEMPMAIL_SELECTION_MODES else "single"


def _normalize_single_service_id(value: Any) -> Optional[int]:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _build_provider_runtime_meta(provider: Optional[str]) -> Dict[str, Any]:
    normalized_provider = normalize_tempmail_provider(provider)
    meta = get_tempmail_provider_meta(normalized_provider)
    return {
        "provider": normalized_provider,
        "provider_label": str(meta.get("label") or normalized_provider),
        "call_style": str(meta.get("call_style") or ""),
        "default_base_url": str(meta.get("default_base_url") or ""),
    }


def _normalize_global_service_runtime(service: EmailService, settings: Any) -> Dict[str, Any]:
    raw_config = dict(service.config or {})
    if "api_url" in raw_config and "base_url" not in raw_config:
        raw_config["base_url"] = raw_config.pop("api_url")

    provider = normalize_tempmail_provider(service.provider or raw_config.get("provider"))
    raw_config["provider"] = provider
    if not str(raw_config.get("base_url") or "").strip():
        provider_meta = get_tempmail_provider_meta(provider)
        raw_config["base_url"] = str(provider_meta.get("default_base_url") or "").strip()

    normalized_config = build_tempmail_config(raw_config, settings, provider_hint=provider)

    legacy_rule_missing = service.selection_mode is None and "selection_mode" not in raw_config
    selection_mode = _normalize_selection_mode(service.selection_mode or raw_config.get("selection_mode"))
    single_service_id = _normalize_single_service_id(
        service.single_service_id if service.single_service_id is not None else raw_config.get("single_service_id")
    )

    if legacy_rule_missing:
        selection_mode = _normalize_selection_mode(getattr(settings, "tempmail_selection_mode", "single"))
        if single_service_id is None:
            single_service_id = _normalize_single_service_id(getattr(settings, "tempmail_single_service_id", None))

    return {
        "provider": provider,
        "config": normalized_config,
        "selection_mode": selection_mode,
        "single_service_id": single_service_id,
    }


def get_global_tempmail_service(db, settings: Any, *, ensure_exists: bool = True) -> Optional[EmailService]:
    """获取全局固定临时邮箱服务。"""
    service = db.query(EmailService).filter(EmailService.builtin_key == TEMPMAIL_GLOBAL_BUILTIN_KEY).first()
    if service is None and ensure_exists:
        ensure_builtin_tempmail_services(db, settings)
        service = db.query(EmailService).filter(EmailService.builtin_key == TEMPMAIL_GLOBAL_BUILTIN_KEY).first()
    return service


def ensure_builtin_tempmail_services(db, settings: Any) -> None:
    """确保系统预置临时邮箱服务存在。"""
    specs = build_tempmail_builtin_specs(settings)
    valid_builtin_keys = {str(spec.get("builtin_key") or "") for spec in specs}

    # 清理已下线 POP 规则（包括历史 pop3_alias 记录）。
    offline_provider_markers = {"pop3", "pop3_alias", "pop3_plus", "pop3plus", "plus_alias"}
    tempmail_services = db.query(EmailService).filter(EmailService.service_type == EmailServiceType.TEMPMAIL.value).all()
    for service in tempmail_services:
        config = dict(service.config or {})
        provider_candidates = {
            str(service.provider or "").strip().lower().replace("-", "_").replace(" ", ""),
            str(config.get("provider") or "").strip().lower().replace("-", "_").replace(" ", ""),
            str(config.get("type") or "").strip().lower().replace("-", "_").replace(" ", ""),
        }
        if provider_candidates & offline_provider_markers:
            db.delete(service)

    # 清理已下线的历史预置项（例如不再提供的免费邮箱供应商）
    stale_builtin_services = db.query(EmailService).filter(EmailService.is_builtin == True).all()
    for stale_service in stale_builtin_services:
        stale_key = str(stale_service.builtin_key or "")
        if not stale_key:
            continue
        if stale_key in valid_builtin_keys:
            continue
        if stale_key == TEMPMAIL_GLOBAL_BUILTIN_KEY:
            continue
        db.delete(stale_service)

    for spec in specs:
        service = db.query(EmailService).filter(EmailService.builtin_key == spec["builtin_key"]).first()
        provider = normalize_tempmail_provider(spec.get("provider"))
        runtime_meta = _build_provider_runtime_meta(provider)

        if service is None:
            service = EmailService(
                service_type=EmailServiceType.TEMPMAIL.value,
                provider=provider,
                name=spec["name"],
                config=spec["config"],
                enabled=spec["enabled"],
                priority=spec["priority"],
                is_builtin=spec["is_builtin"],
                is_immutable=spec["is_immutable"],
                builtin_key=spec["builtin_key"],
                provider_runtime_meta=runtime_meta,
            )

            if str(spec.get("builtin_key") or "") == TEMPMAIL_GLOBAL_BUILTIN_KEY:
                service.selection_mode = _normalize_selection_mode(getattr(settings, "tempmail_selection_mode", "single"))
                service.single_service_id = _normalize_single_service_id(getattr(settings, "tempmail_single_service_id", None))

            db.add(service)
            continue

        service.service_type = EmailServiceType.TEMPMAIL.value
        service.provider = normalize_tempmail_provider(service.provider or provider)
        service.provider_runtime_meta = _build_provider_runtime_meta(service.provider)
        service.is_builtin = True
        service.is_immutable = bool(spec["is_immutable"])
        service.builtin_key = spec["builtin_key"]

        if service.is_immutable:
            # 固定项保留运行时状态，仅同步标识字段。
            service.name = spec["name"]
            service.priority = spec["priority"]
            normalized_runtime = _normalize_global_service_runtime(service, settings)
            service.provider = normalized_runtime["provider"]
            service.config = normalized_runtime["config"]
            service.selection_mode = normalized_runtime["selection_mode"]
            service.single_service_id = normalized_runtime["single_service_id"]
            if service.enabled is None:
                service.enabled = bool(spec["enabled"])
        else:
            service.selection_mode = None
            service.single_service_id = None
            if not service.config:
                # 仅在历史配置为空时填充默认值，避免覆盖用户手工调整。
                service.config = spec["config"]

    global_service = db.query(EmailService).filter(EmailService.builtin_key == TEMPMAIL_GLOBAL_BUILTIN_KEY).first()
    if global_service is not None:
        normalized_runtime = _normalize_global_service_runtime(global_service, settings)
        global_service.provider = normalized_runtime["provider"]
        global_service.provider_runtime_meta = _build_provider_runtime_meta(normalized_runtime["provider"])
        global_service.config = normalized_runtime["config"]
        global_service.selection_mode = normalized_runtime["selection_mode"]
        global_service.single_service_id = normalized_runtime["single_service_id"]

        # 若单服务目标已被清理或不可用，则回退为空，避免指向无效项。
        if global_service.single_service_id:
            selected_service = db.query(EmailService).filter(
                EmailService.id == global_service.single_service_id,
                EmailService.service_type == EmailServiceType.TEMPMAIL.value,
            ).first()
            if selected_service is None or str(selected_service.builtin_key or "") == TEMPMAIL_GLOBAL_BUILTIN_KEY:
                global_service.single_service_id = None

    db.commit()


def get_tempmail_runtime_state(db, settings: Any) -> Dict[str, Any]:
    """返回临时邮箱运行时状态（来源：全局固定服务记录）。"""
    service = get_global_tempmail_service(db, settings, ensure_exists=True)
    if service is None:
        return {
            "global_service_id": None,
            "global_enabled": False,
            "selection_mode": "single",
            "single_service_id": None,
            "provider": "tempmail_lol",
            "config": {},
        }

    normalized_runtime = _normalize_global_service_runtime(service, settings)
    provider_runtime_meta = _build_provider_runtime_meta(normalized_runtime["provider"])

    changed = False
    if service.provider != normalized_runtime["provider"]:
        service.provider = normalized_runtime["provider"]
        changed = True
    if dict(service.config or {}) != normalized_runtime["config"]:
        service.config = normalized_runtime["config"]
        changed = True
    if service.selection_mode != normalized_runtime["selection_mode"]:
        service.selection_mode = normalized_runtime["selection_mode"]
        changed = True
    if service.single_service_id != normalized_runtime["single_service_id"]:
        service.single_service_id = normalized_runtime["single_service_id"]
        changed = True
    if dict(service.provider_runtime_meta or {}) != provider_runtime_meta:
        service.provider_runtime_meta = provider_runtime_meta
        changed = True

    if changed:
        db.commit()
        db.refresh(service)

    return {
        "global_service_id": service.id,
        "global_enabled": bool(service.enabled),
        "selection_mode": service.selection_mode or "single",
        "single_service_id": _normalize_single_service_id(service.single_service_id),
        "provider": service.provider or normalized_runtime["provider"],
        "config": dict(service.config or {}),
    }


def update_tempmail_runtime_state(
    db,
    settings: Any,
    *,
    global_enabled: Optional[bool] = None,
    selection_mode: Optional[str] = None,
    single_service_id: Any = _RUNTIME_UNSET,
) -> Dict[str, Any]:
    """更新临时邮箱运行时状态。"""
    service = get_global_tempmail_service(db, settings, ensure_exists=True)
    if service is None:
        raise RuntimeError("全局临时邮箱服务不存在")

    normalized_runtime = _normalize_global_service_runtime(service, settings)

    changed = False
    if service.provider != normalized_runtime["provider"]:
        service.provider = normalized_runtime["provider"]
        changed = True
    if dict(service.config or {}) != normalized_runtime["config"]:
        service.config = normalized_runtime["config"]
        changed = True

    normalized_mode = _normalize_selection_mode(selection_mode) if selection_mode is not None else normalized_runtime["selection_mode"]
    if service.selection_mode != normalized_mode:
        service.selection_mode = normalized_mode
        changed = True

    if single_service_id is not _RUNTIME_UNSET:
        normalized_single_service_id = _normalize_single_service_id(single_service_id)
    else:
        normalized_single_service_id = normalized_runtime["single_service_id"]

    if service.single_service_id != normalized_single_service_id:
        service.single_service_id = normalized_single_service_id
        changed = True

    if global_enabled is not None and bool(service.enabled) != bool(global_enabled):
        service.enabled = bool(global_enabled)
        changed = True

    provider_runtime_meta = _build_provider_runtime_meta(service.provider)
    if dict(service.provider_runtime_meta or {}) != provider_runtime_meta:
        service.provider_runtime_meta = provider_runtime_meta
        changed = True

    if changed:
        db.commit()
        db.refresh(service)

    return get_tempmail_runtime_state(db, settings)


def sync_global_tempmail_service(
    db,
    settings: Any,
    *,
    base_url: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> None:
    """兼容旧调用：同步全局固定临时邮箱条目。"""
    service = get_global_tempmail_service(db, settings, ensure_exists=True)
    if service is None:
        return

    normalized_runtime = _normalize_global_service_runtime(service, settings)
    config = dict(normalized_runtime["config"])

    if base_url is not None:
        config["base_url"] = str(base_url or "").strip()
        config = build_tempmail_config(config, settings, provider_hint=normalized_runtime["provider"])

    service.provider = normalized_runtime["provider"]
    service.config = config
    service.selection_mode = normalized_runtime["selection_mode"]
    service.single_service_id = normalized_runtime["single_service_id"]
    service.provider_runtime_meta = _build_provider_runtime_meta(normalized_runtime["provider"])

    if enabled is not None:
        service.enabled = bool(enabled)

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
