"""临时邮箱服务管理 API 路由。"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...config.settings import get_settings, update_settings
from ...database.models import EmailService as EmailServiceModel
from ...database.session import get_db
from ...database.tempmail_bootstrap import ensure_builtin_tempmail_services, is_global_tempmail_service
from ...services import EmailServiceFactory, EmailServiceType
from ...services.tempmail_catalog import (
    build_tempmail_config,
    get_tempmail_provider_meta,
    list_tempmail_provider_options,
    normalize_tempmail_provider,
)

logger = logging.getLogger(__name__)
router = APIRouter()


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


class EmailServiceCreate(BaseModel):
    """创建临时邮箱服务请求。"""

    service_type: str = EmailServiceType.TEMPMAIL.value
    provider: str = "tempmail_lol"
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
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
    with get_db() as db:
        _ensure_builtin_seeded(db)
        total = db.query(EmailServiceModel).filter(EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value).count()
        enabled_count = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value,
            EmailServiceModel.enabled == True,
        ).count()

    settings = get_settings()
    return {
        "tempmail_count": total,
        "enabled_count": enabled_count,
        "global_enabled": settings.tempmail_enabled,
        "selection_mode": settings.tempmail_selection_mode,
        "single_service_id": settings.tempmail_single_service_id,
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

        services = query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()
        return EmailServiceListResponse(total=len(services), services=[_service_to_response(item) for item in services])


@router.get("/{service_id}", response_model=EmailServiceResponse)
async def get_email_service(service_id: int):
    """获取临时邮箱服务详情。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)
        return _service_to_response(service)


@router.get("/{service_id}/full")
async def get_email_service_full(service_id: int):
    """获取临时邮箱服务完整详情。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        response = _service_to_response(service)
        return response.model_dump()


@router.post("", response_model=EmailServiceResponse)
async def create_email_service(request: EmailServiceCreate):
    """创建临时邮箱服务。"""
    _ensure_tempmail_type(request.service_type)
    provider = normalize_tempmail_provider(request.provider or request.config.get("provider"))

    with get_db() as db:
        _ensure_builtin_seeded(db)

        exists = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.name).first()
        if exists:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        normalized_config = _normalize_tempmail_config(request.config, provider_hint=provider)
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
        if not service:
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
            merged.update(incoming)
            service.config = _normalize_tempmail_config(merged, provider_hint=service.provider)

        if request.enabled is not None:
            service.enabled = request.enabled
            if is_global_tempmail_service(service):
                update_settings(tempmail_enabled=bool(request.enabled))

        if request.priority is not None:
            service.priority = request.priority

        db.commit()
        db.refresh(service)
        return _service_to_response(service)


@router.delete("/{service_id}")
async def delete_email_service(service_id: int):
    """删除临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        if service.is_immutable:
            raise HTTPException(status_code=400, detail="固定内置服务不可删除")

        current_settings = get_settings()
        clear_single_service_id = current_settings.tempmail_single_service_id == service.id
        service_name = service.name
        db.delete(service)
        db.commit()

        if clear_single_service_id:
            update_settings(tempmail_single_service_id=None)

        return {"success": True, "message": f"服务 {service_name} 已删除"}


@router.post("/{service_id}/test", response_model=ServiceTestResult)
async def test_email_service(service_id: int):
    """测试临时邮箱服务可用性。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        if service.is_immutable:
            raise HTTPException(status_code=400, detail="固定内置服务仅允许启用/禁用")

        try:
            config = _normalize_tempmail_config(service.config, provider_hint=service.provider)
            email_service = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config, name=service.name)
            health = email_service.check_health()
            if health:
                return ServiceTestResult(success=True, message="服务连接正常")
            return ServiceTestResult(success=False, message="服务连接失败")
        except Exception as exc:
            logger.error("测试临时邮箱服务失败: %s", exc)
            return ServiceTestResult(success=False, message=f"测试失败: {exc}")


@router.post("/{service_id}/enable")
async def enable_email_service(service_id: int):
    """启用临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service.enabled = True
        db.commit()

        if is_global_tempmail_service(service):
            update_settings(tempmail_enabled=True)

        return {"success": True, "message": f"服务 {service.name} 已启用"}


@router.post("/{service_id}/disable")
async def disable_email_service(service_id: int):
    """禁用临时邮箱服务。"""
    with get_db() as db:
        _ensure_builtin_seeded(db)

        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service.enabled = False
        db.commit()

        settings = get_settings()
        updates: Dict[str, Any] = {}

        if is_global_tempmail_service(service):
            updates["tempmail_enabled"] = False

        if settings.tempmail_single_service_id == service.id:
            updates["tempmail_single_service_id"] = None

        if updates:
            update_settings(**updates)

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
