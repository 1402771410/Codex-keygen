"""临时邮箱服务管理 API 路由。"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...config.settings import get_settings
from ...core.register import RegistrationEngine
from ...database.models import EmailService as EmailServiceModel
from ...database.session import get_db
from ...database.tempmail_bootstrap import (
    ensure_builtin_tempmail_services,
    get_tempmail_runtime_state,
    update_tempmail_runtime_state,
)
from ...services import EmailServiceFactory, EmailServiceType
from ...services.tempmail_catalog import (
    build_tempmail_config,
    get_tempmail_provider_meta,
    list_tempmail_provider_options,
    normalize_tempmail_provider,
)

logger = logging.getLogger(__name__)
router = APIRouter()
_OFFLINE_PROVIDER_MARKERS = {
    "pop3",
    "pop3_alias",
    "pop3_plus",
    "pop3plus",
    "plus_alias",
    "guerrilla",
    "guerrillamail",
}


def _normalize_provider_marker(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "")


def _is_offline_provider(value: Any) -> bool:
    return _normalize_provider_marker(value) in _OFFLINE_PROVIDER_MARKERS


def _service_is_offline_provider(service: EmailServiceModel) -> bool:
    config = dict(service.config or {})
    return any(
        _is_offline_provider(candidate)
        for candidate in [service.provider, config.get("provider"), config.get("type")]
    )


def _normalize_tempmail_config(raw: Optional[Dict[str, Any]], provider_hint: Optional[str] = None) -> Dict[str, Any]:
    """标准化临时邮箱配置。"""
    settings = get_settings()
    return build_tempmail_config(raw, settings, provider_hint=provider_hint)


def _ensure_tempmail_type(service_type: str) -> None:
    if service_type != EmailServiceType.TEMPMAIL.value:
        raise HTTPException(status_code=400, detail="仅支持 tempmail 类型")


def _ensure_builtin_seeded(db) -> None:
    settings = get_settings()
    ensure_builtin_tempmail_services(db, settings)

    stale_services = db.query(EmailServiceModel).filter(
        EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value
    ).all()
    removed_ids: List[int] = []
    for stale in stale_services:
        if not _service_is_offline_provider(stale):
            continue
        removed_ids.append(int(stale.id))
        db.delete(stale)

    if removed_ids:
        db.commit()
        runtime_state = get_tempmail_runtime_state(db, settings)
        if runtime_state.get("single_service_id") in set(removed_ids):
            update_tempmail_runtime_state(db, settings, single_service_id=None)
        logger.warning("已清理下线邮箱规则: %s", removed_ids)


def _compose_stage_message(stage: str, message: str) -> str:
    """拼接阶段化测试消息，供 DB 存储与前端展示。"""
    normalized_stage = (stage or "unknown").strip() or "unknown"
    return f"[{normalized_stage}] {message}"


def _is_tempmail_service_available(service: EmailServiceModel) -> bool:
    """判断临时邮箱服务是否通过真实 OTP 可用性校验。"""
    test_status = str(service.last_test_status or "").strip().lower()
    test_message = str(service.last_test_message or "").strip().lower()
    return test_status == "success" and "[otp_received]" in test_message


def _ensure_service_can_enable(service: EmailServiceModel) -> None:
    """仅允许启用通过真实 OTP 探测的服务。"""
    if _is_tempmail_service_available(service):
        return
    raise HTTPException(status_code=400, detail="服务未通过真实 OTP 测试，暂不可启用")


class EmailServiceCreate(BaseModel):
    """创建临时邮箱服务请求。"""

    service_type: str = EmailServiceType.TEMPMAIL.value
    provider: str = "tempmail_lol"
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    priority: int = 0


class EmailServiceUpdate(BaseModel):
    """更新临时邮箱服务请求。"""

    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class EmailServiceResponse(BaseModel):
    """临时邮箱服务响应。"""

    id: int
    service_type: str
    provider: str
    provider_label: str
    name: str
    enabled: bool
    is_builtin: bool
    is_immutable: bool
    builtin_key: Optional[str] = None
    priority: int
    config: Dict[str, Any] = Field(default_factory=dict)
    provider_runtime_meta: Dict[str, Any] = Field(default_factory=dict)
    available: bool
    availability_status: str
    last_test_status: Optional[str] = None
    last_tested_at: Optional[str] = None
    last_test_message: Optional[str] = None
    last_used: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class EmailServiceListResponse(BaseModel):
    """临时邮箱服务列表响应。"""

    total: int
    services: List[EmailServiceResponse]


class ServiceTestResult(BaseModel):
    """服务测试结果。"""

    success: bool
    status: str
    stage: str
    message: str
    details: Optional[Dict[str, Any]] = None


class TempmailTestRequest(BaseModel):
    """临时邮箱测试请求。"""

    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_url: Optional[str] = None


def _service_to_response(service: EmailServiceModel) -> EmailServiceResponse:
    provider = normalize_tempmail_provider(service.provider or (service.config or {}).get("provider"))
    provider_meta = get_tempmail_provider_meta(provider)
    config = _normalize_tempmail_config(service.config, provider_hint=provider)
    provider_runtime_meta = dict(service.provider_runtime_meta or {})
    if not provider_runtime_meta:
        provider_runtime_meta = {
            "provider": provider,
            "provider_label": str(provider_meta.get("label") or provider),
            "call_style": str(provider_meta.get("call_style") or ""),
            "default_base_url": str(provider_meta.get("default_base_url") or ""),
        }

    available = _is_tempmail_service_available(service)
    return EmailServiceResponse(
        id=service.id,
        service_type=service.service_type,
        provider=provider,
        provider_label=str(provider_meta.get("label") or provider),
        name=service.name,
        enabled=service.enabled,
        is_builtin=bool(service.is_builtin),
        is_immutable=bool(service.is_immutable),
        builtin_key=service.builtin_key,
        priority=service.priority,
        config=config,
        provider_runtime_meta=provider_runtime_meta,
        available=available,
        availability_status="available" if available else "unavailable",
        last_test_status=service.last_test_status,
        last_tested_at=service.last_tested_at.isoformat() if service.last_tested_at else None,
        last_test_message=service.last_test_message,
        last_used=service.last_used.isoformat() if service.last_used else None,
        created_at=service.created_at.isoformat() if service.created_at else None,
        updated_at=service.updated_at.isoformat() if service.updated_at else None,
    )


def _guard_immutable_update(service: EmailServiceModel, request: EmailServiceUpdate) -> None:
    if not service.is_immutable:
        return

    if request.name is not None or request.config is not None or request.priority is not None:
        raise HTTPException(status_code=400, detail="固定内置服务不可编辑，仅允许启用/禁用")


@router.get("/stats")
async def get_email_services_stats():
    """获取临时邮箱服务统计。"""
    settings = get_settings()
    with get_db() as db:
        _ensure_builtin_seeded(db)
        runtime_state = get_tempmail_runtime_state(db, settings)
        total = db.query(EmailServiceModel).filter(EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value).count()
        enabled_count = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value,
            EmailServiceModel.enabled == True,
        ).count()

    return {
        "tempmail_count": total,
        "enabled_count": enabled_count,
        "global_enabled": runtime_state["global_enabled"],
        "selection_mode": runtime_state["selection_mode"],
        "single_service_id": runtime_state["single_service_id"],
    }


@router.get("/types")
async def get_service_types():
    """获取支持的邮箱服务类型与供应商元信息。"""
    providers = list_tempmail_provider_options()
    return {
        "types": [
            {
                "value": "tempmail",
                "label": "临时邮箱",
                "description": "支持多临时邮箱供应商，并可配置单服务或多服务轮询",
                "providers": providers,
                "config_fields": [
                    {
                        "name": "provider",
                        "label": "供应商",
                        "required": True,
                        "default": "tempmail_lol",
                        "options": providers,
                    },
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "按供应商自动填充"},
                    {"name": "address_prefix", "label": "邮箱前缀规则", "required": False, "placeholder": "例如：openai"},
                    {"name": "preferred_domain", "label": "优先域名", "required": False, "placeholder": "例如：mail.tld"},
                    {"name": "api_key", "label": "API Key（如供应商需要）", "required": False, "placeholder": "可选"},
                    {"name": "timeout", "label": "超时时间（秒）", "required": False, "default": 30},
                    {"name": "max_retries", "label": "最大重试次数", "required": False, "default": 3},
                ],
            }
        ]
    }


@router.get("", response_model=EmailServiceListResponse)
async def list_email_services(
    service_type: Optional[str] = Query(None, description="服务类型筛选"),
    enabled_only: bool = Query(False, description="只显示启用的服务"),
):
    """获取临时邮箱服务列表。"""
    if service_type:
        _ensure_tempmail_type(service_type)

    with get_db() as db:
        _ensure_builtin_seeded(db)

        query = db.query(EmailServiceModel).filter(EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value)
        if enabled_only:
            query = query.filter(EmailServiceModel.enabled == True)

        services = [
            item
            for item in query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()
            if not _service_is_offline_provider(item)
        ]
        return EmailServiceListResponse(total=len(services), services=[_service_to_response(item) for item in services])


@router.get("/{service_id}", response_model=EmailServiceResponse)
async def get_email_service(service_id: int):
    """获取临时邮箱服务详情。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)
        return _service_to_response(service)


@router.get("/{service_id}/full")
async def get_email_service_full(service_id: int):
    """获取临时邮箱服务完整详情。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        response = _service_to_response(service)
        return response.model_dump()


@router.post("", response_model=EmailServiceResponse)
async def create_email_service(request: EmailServiceCreate):
    """创建临时邮箱服务。"""
    _ensure_tempmail_type(request.service_type)
    requested_provider = request.provider or request.config.get("provider")
    if _is_offline_provider(requested_provider):
        raise HTTPException(status_code=400, detail="该邮箱供应商已下线，请使用其他临时邮箱供应商")
    provider = normalize_tempmail_provider(requested_provider)

    with get_db() as db:
        _ensure_builtin_seeded(db)

        exists = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.name).first()
        if exists:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        if request.enabled:
            raise HTTPException(status_code=400, detail="新增规则需先测试通过后才能启用")

        normalized_config = _normalize_tempmail_config(request.config, provider_hint=provider)
        provider_meta = get_tempmail_provider_meta(provider)
        provider_runtime_meta = {
            "provider": provider,
            "provider_label": str(provider_meta.get("label") or provider),
            "call_style": str(provider_meta.get("call_style") or ""),
            "default_base_url": str(provider_meta.get("default_base_url") or ""),
        }
        service = EmailServiceModel(
            service_type=EmailServiceType.TEMPMAIL.value,
            provider=provider,
            name=request.name,
            config=normalized_config,
            enabled=request.enabled,
            priority=request.priority,
            is_builtin=False,
            is_immutable=False,
            builtin_key=None,
            provider_runtime_meta=provider_runtime_meta,
        )
        db.add(service)
        db.commit()
        db.refresh(service)
        return _service_to_response(service)


@router.patch("/{service_id}", response_model=EmailServiceResponse)
async def update_email_service(service_id: int, request: EmailServiceUpdate):
    """更新临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)
        _guard_immutable_update(service, request)

        if request.name is not None:
            duplicated = db.query(EmailServiceModel).filter(
                EmailServiceModel.name == request.name,
                EmailServiceModel.id != service.id,
            ).first()
            if duplicated:
                raise HTTPException(status_code=400, detail="服务名称已存在")
            service.name = request.name

        if request.config is not None:
            merged = dict(service.config or {})
            incoming = dict(request.config)
            # provider 由创建阶段决定，避免误改调用方式
            incoming.pop("provider", None)
            if _is_offline_provider(incoming.get("type")):
                raise HTTPException(status_code=400, detail="该邮箱供应商已下线，请使用其他临时邮箱供应商")
            merged.update(incoming)
            service.config = _normalize_tempmail_config(merged, provider_hint=service.provider)

        if request.enabled is not None:
            if request.enabled:
                _ensure_service_can_enable(service)
            service.enabled = request.enabled

        if request.priority is not None:
            service.priority = request.priority

        provider_meta = get_tempmail_provider_meta(service.provider)
        service.provider_runtime_meta = {
            "provider": service.provider,
            "provider_label": str(provider_meta.get("label") or service.provider),
            "call_style": str(provider_meta.get("call_style") or ""),
            "default_base_url": str(provider_meta.get("default_base_url") or ""),
        }

        db.commit()
        db.refresh(service)
        return _service_to_response(service)


@router.delete("/{service_id}")
async def delete_email_service(service_id: int):
    """删除临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        if service.is_immutable:
            raise HTTPException(status_code=400, detail="固定内置服务不可删除")

        settings = get_settings()
        runtime_state = get_tempmail_runtime_state(db, settings)
        clear_single_service_id = runtime_state["single_service_id"] == service.id
        service_name = service.name
        db.delete(service)
        db.commit()

        if clear_single_service_id:
            update_tempmail_runtime_state(db, settings, single_service_id=None)

        return {"success": True, "message": f"服务 {service_name} 已删除"}


@router.post("/{service_id}/test", response_model=ServiceTestResult)
async def test_email_service(service_id: int):
    """执行真实 OpenAI OTP 探测并记录结果。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        try:
            config = _normalize_tempmail_config(service.config, provider_hint=service.provider)
            email_service = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config, name=service.name)

            probe_engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=config.get("proxy_url"),
                callback_logger=lambda msg: logger.info("[OTP探测][%s] %s", service.name, msg),
            )
            probe_result = probe_engine.run_otp_probe()

            service.last_tested_at = datetime.utcnow()
            service.last_test_status = "success" if probe_result.success else "failed"
            service.last_test_message = _compose_stage_message(probe_result.stage, probe_result.message)
            db.commit()

            return ServiceTestResult(
                success=probe_result.success,
                status=service.last_test_status,
                stage=probe_result.stage,
                message=probe_result.message,
                details={
                    "email": probe_result.email,
                    "is_existing_account": probe_result.is_existing_account,
                    "persisted_message": service.last_test_message,
                },
            )
        except Exception as exc:
            logger.error("真实 OTP 探测失败: %s", exc)
            service.last_test_status = "failed"
            service.last_tested_at = datetime.utcnow()
            service.last_test_message = _compose_stage_message("probe_exception", str(exc))
            db.commit()
            return ServiceTestResult(
                success=False,
                status=service.last_test_status,
                stage="probe_exception",
                message=f"真实 OTP 探测失败: {exc}",
                details={"persisted_message": service.last_test_message},
            )


@router.post("/{service_id}/enable")
async def enable_email_service(service_id: int):
    """启用临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)
        _ensure_service_can_enable(service)

        service.enabled = True
        db.commit()

        settings = get_settings()
        runtime_state = get_tempmail_runtime_state(db, settings)
        if runtime_state["global_service_id"] == service.id:
            update_tempmail_runtime_state(db, settings, global_enabled=True)

        return {"success": True, "message": f"服务 {service.name} 已启用"}


@router.post("/{service_id}/disable")
async def disable_email_service(service_id: int):
    """禁用临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service or _service_is_offline_provider(service):
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service.enabled = False
        db.commit()

        settings = get_settings()
        runtime_state = get_tempmail_runtime_state(db, settings)
        runtime_updates: Dict[str, Any] = {}

        if runtime_state["global_service_id"] == service.id:
            runtime_updates["global_enabled"] = False

        if runtime_state["single_service_id"] == service.id:
            runtime_updates["single_service_id"] = None

        if runtime_updates:
            update_tempmail_runtime_state(db, settings, **runtime_updates)

        return {"success": True, "message": f"服务 {service.name} 已禁用"}


@router.post("/reorder")
async def reorder_services(service_ids: List[int]):
    """调整临时邮箱服务轮询顺序（priority 越小越优先）。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        mutable_index = 1
        for service_id in service_ids:
            service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
            if not service or service.service_type != EmailServiceType.TEMPMAIL.value:
                continue
            if service.is_immutable:
                continue

            service.priority = mutable_index
            service.updated_at = datetime.utcnow()
            mutable_index += 1

        db.commit()
    return {"success": True, "message": "优先级已更新"}


@router.post("/test-tempmail")
async def test_tempmail_service(request: TempmailTestRequest):
    """测试全局临时邮箱配置是否可用。"""
    try:
        settings = get_settings()
        provider = normalize_tempmail_provider(request.provider or "tempmail_lol")

        config = _normalize_tempmail_config(
            {
                "provider": provider,
                "base_url": request.base_url or request.api_url,
                "timeout": settings.tempmail_timeout,
                "max_retries": settings.tempmail_max_retries,
            },
            provider_hint=provider,
        )

        tempmail = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config)
        health = tempmail.check_health()
        if health:
            return {"success": True, "message": "临时邮箱连接正常"}
        return {"success": False, "message": "临时邮箱连接失败"}
    except Exception as exc:
        logger.error("测试临时邮箱失败: %s", exc)
        return {"success": False, "message": f"测试失败: {exc}"}
