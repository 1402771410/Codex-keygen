"""临时邮箱服务管理 API 路由。"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...config.settings import get_settings
from ...database.models import EmailService as EmailServiceModel
from ...database.session import get_db
from ...services import EmailServiceFactory, EmailServiceType

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalize_tempmail_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """标准化临时邮箱配置。"""
    settings = get_settings()
    source = dict(raw or {})

    if "api_url" in source and "base_url" not in source:
        source["base_url"] = source.pop("api_url")

    config: Dict[str, Any] = {
        "base_url": (source.get("base_url") or settings.tempmail_base_url).strip(),
        "timeout": int(source.get("timeout") or settings.tempmail_timeout),
        "max_retries": int(source.get("max_retries") or settings.tempmail_max_retries),
    }

    address_prefix = str(source.get("address_prefix") or "").strip()
    preferred_domain = str(source.get("preferred_domain") or "").strip()

    if address_prefix:
        config["address_prefix"] = address_prefix
    if preferred_domain:
        config["preferred_domain"] = preferred_domain

    return config


def _ensure_tempmail_type(service_type: str) -> None:
    if service_type != EmailServiceType.TEMPMAIL.value:
        raise HTTPException(status_code=400, detail="仅支持 tempmail 类型")


class EmailServiceCreate(BaseModel):
    """创建临时邮箱服务请求。"""

    service_type: str = EmailServiceType.TEMPMAIL.value
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
    name: str
    enabled: bool
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

    base_url: Optional[str] = None
    api_url: Optional[str] = None


def _service_to_response(service: EmailServiceModel) -> EmailServiceResponse:
    config = _normalize_tempmail_config(service.config)
    return EmailServiceResponse(
        id=service.id,
        service_type=service.service_type,
        name=service.name,
        enabled=service.enabled,
        priority=service.priority,
        config=config,
        last_used=service.last_used.isoformat() if service.last_used else None,
        created_at=service.created_at.isoformat() if service.created_at else None,
        updated_at=service.updated_at.isoformat() if service.updated_at else None,
    )


@router.get("/stats")
async def get_email_services_stats():
    """获取临时邮箱服务统计。"""

    with get_db() as db:
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
    }


@router.get("/types")
async def get_service_types():
    """获取支持的邮箱服务类型。"""

    return {
        "types": [
            {
                "value": "tempmail",
                "label": "临时邮箱",
                "description": "仅支持临时邮箱服务，可配置规则并轮询使用",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://api.tempmail.lol/v2"},
                    {"name": "address_prefix", "label": "邮箱前缀规则", "required": False, "placeholder": "例如：openai"},
                    {"name": "preferred_domain", "label": "优先域名", "required": False, "placeholder": "例如：mail.tld"},
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
        query = db.query(EmailServiceModel).filter(EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value)
        if enabled_only:
            query = query.filter(EmailServiceModel.enabled == True)

        services = query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()
        return EmailServiceListResponse(total=len(services), services=[_service_to_response(item) for item in services])


@router.get("/{service_id}", response_model=EmailServiceResponse)
async def get_email_service(service_id: int):
    """获取临时邮箱服务详情。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)
        return _service_to_response(service)


@router.get("/{service_id}/full")
async def get_email_service_full(service_id: int):
    """获取临时邮箱服务完整详情。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        return {
            "id": service.id,
            "service_type": service.service_type,
            "name": service.name,
            "enabled": service.enabled,
            "priority": service.priority,
            "config": _normalize_tempmail_config(service.config),
            "last_used": service.last_used.isoformat() if service.last_used else None,
            "created_at": service.created_at.isoformat() if service.created_at else None,
            "updated_at": service.updated_at.isoformat() if service.updated_at else None,
        }


@router.post("", response_model=EmailServiceResponse)
async def create_email_service(request: EmailServiceCreate):
    """创建临时邮箱服务。"""

    _ensure_tempmail_type(request.service_type)

    with get_db() as db:
        exists = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.name).first()
        if exists:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        service = EmailServiceModel(
            service_type=EmailServiceType.TEMPMAIL.value,
            name=request.name,
            config=_normalize_tempmail_config(request.config),
            enabled=request.enabled,
            priority=request.priority,
        )
        db.add(service)
        db.commit()
        db.refresh(service)
        return _service_to_response(service)


@router.patch("/{service_id}", response_model=EmailServiceResponse)
async def update_email_service(service_id: int, request: EmailServiceUpdate):
    """更新临时邮箱服务。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        if request.name is not None:
            service.name = request.name
        if request.config is not None:
            merged = dict(service.config or {})
            merged.update(request.config)
            service.config = _normalize_tempmail_config(merged)
        if request.enabled is not None:
            service.enabled = request.enabled
        if request.priority is not None:
            service.priority = request.priority

        db.commit()
        db.refresh(service)
        return _service_to_response(service)


@router.delete("/{service_id}")
async def delete_email_service(service_id: int):
    """删除临时邮箱服务。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service_name = service.name
        db.delete(service)
        db.commit()
        return {"success": True, "message": f"服务 {service_name} 已删除"}


@router.post("/{service_id}/test", response_model=ServiceTestResult)
async def test_email_service(service_id: int):
    """测试临时邮箱服务可用性。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        try:
            config = _normalize_tempmail_config(service.config)
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
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service.enabled = True
        db.commit()
        return {"success": True, "message": f"服务 {service.name} 已启用"}


@router.post("/{service_id}/disable")
async def disable_email_service(service_id: int):
    """禁用临时邮箱服务。"""

    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        _ensure_tempmail_type(service.service_type)

        service.enabled = False
        db.commit()
        return {"success": True, "message": f"服务 {service.name} 已禁用"}


@router.post("/reorder")
async def reorder_services(service_ids: List[int]):
    """调整临时邮箱服务轮询顺序（priority 越小越优先）。"""

    with get_db() as db:
        for index, service_id in enumerate(service_ids):
            service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
            if service and service.service_type == EmailServiceType.TEMPMAIL.value:
                service.priority = index
                service.updated_at = datetime.utcnow()
        db.commit()
    return {"success": True, "message": "优先级已更新"}


@router.post("/test-tempmail")
async def test_tempmail_service(request: TempmailTestRequest):
    """测试全局临时邮箱配置是否可用。"""

    try:
        settings = get_settings()
        base_url = (request.base_url or request.api_url or settings.tempmail_base_url).strip()
        config = {
            "base_url": base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
        }

        tempmail = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config)
        health = tempmail.check_health()
        if health:
            return {"success": True, "message": "临时邮箱连接正常"}
        return {"success": False, "message": "临时邮箱连接失败"}
    except Exception as exc:
        logger.error("测试临时邮箱失败: %s", exc)
        return {"success": False, "message": f"测试失败: {exc}"}
