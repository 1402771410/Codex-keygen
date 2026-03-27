"""
账号管理 API 路由
"""
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...config.constants import AccountStatus
from ...config.settings import get_settings
from ...core.openai.token_refresh import refresh_account_token as do_refresh
from ...core.openai.token_refresh import validate_account_token as do_validate
from ...core.upload.cpa_upload import generate_token_json, batch_upload_to_cpa, upload_to_cpa
from ...core.upload.team_manager_upload import upload_to_team_manager, batch_upload_to_team_manager
from ...core.upload.sub2api_upload import batch_upload_to_sub2api, upload_to_sub2api

from ...core.dynamic_proxy import get_proxy_url_for_task
from ...database import crud
from ...database.models import Account
from ...database.session import get_db
from ...database.tempmail_bootstrap import ensure_builtin_tempmail_services, get_tempmail_runtime_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_proxy(request_proxy: Optional[str] = None) -> Optional[str]:
    """获取代理 URL，策略与注册流程一致：代理列表 → 动态代理 → 静态配置"""
    if request_proxy:
        return request_proxy
    with get_db() as db:
        proxy = crud.get_random_proxy(db)
        if proxy:
            return proxy.proxy_url
    proxy_url = get_proxy_url_for_task()
    if proxy_url:
        return proxy_url
    return get_settings().proxy_url


# ============== Pydantic Models ==============

class AccountResponse(BaseModel):
    """账号响应模型"""
    id: int
    email: str
    password: Optional[str] = None
    client_id: Optional[str] = None
    email_service: str
    account_id: Optional[str] = None
    workspace_id: Optional[str] = None
    registered_at: Optional[str] = None
    last_refresh: Optional[str] = None
    expires_at: Optional[str] = None
    status: str
    proxy_used: Optional[str] = None
    cpa_uploaded: bool = False
    cpa_uploaded_at: Optional[str] = None
    cookies: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class AccountListResponse(BaseModel):
    """账号列表响应"""
    total: int
    accounts: List[AccountResponse]


class AccountUpdateRequest(BaseModel):
    """账号更新请求"""
    status: Optional[str] = None
    metadata: Optional[dict] = None
    cookies: Optional[str] = None  # 完整 cookie 字符串，用于支付请求


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None


class BatchUpdateRequest(BaseModel):
    """批量更新请求"""
    ids: List[int]
    status: str


# ============== Helper Functions ==============

def resolve_account_ids(
    db,
    ids: List[int],
    select_all: bool = False,
    status_filter: Optional[str] = None,
    email_service_filter: Optional[str] = None,
    search_filter: Optional[str] = None,
    start_time_filter: Optional[str] = None,
    end_time_filter: Optional[str] = None,
    email_list_filter: Optional[str] = None,
) -> List[int]:
    """当 select_all=True 时查询全部符合条件的 ID，否则直接返回传入的 ids"""
    if not select_all:
        return ids
    query = db.query(Account.id)
    if status_filter:
        query = query.filter(Account.status == status_filter)
    if email_service_filter:
        query = query.filter(Account.email_service == email_service_filter)
    if search_filter:
        pattern = f"%{search_filter}%"
        query = query.filter(
            (Account.email.ilike(pattern)) | (Account.account_id.ilike(pattern))
        )

    start_time, _ = _parse_time_filter(start_time_filter, "start_time")
    end_time, end_time_date_only = _parse_time_filter(end_time_filter, "end_time")
    email_list = _parse_email_list_filter(email_list_filter)

    if start_time is not None:
        query = query.filter(Account.created_at >= start_time)

    if end_time is not None:
        if end_time_date_only:
            query = query.filter(Account.created_at < end_time + timedelta(days=1))
        else:
            query = query.filter(Account.created_at <= end_time)

    if email_list:
        query = query.filter(Account.email.in_(email_list))

    return [row[0] for row in query.all()]


def _parse_time_filter(value: Optional[str], field_name: str) -> tuple[Optional[datetime], bool]:
    """解析时间筛选字段，返回 (时间, 是否为仅日期格式)。"""
    if not value:
        return None, False

    raw = str(value).strip()
    if not raw:
        return None, False

    if len(raw) == 10:
        try:
            return datetime.strptime(raw, "%Y-%m-%d"), True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field_name} 日期格式错误，应为 YYYY-MM-DD") from exc

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None), False
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} 时间格式错误，应为 ISO8601") from exc


def _parse_email_list_filter(value: Optional[str]) -> List[str]:
    """解析邮箱列表筛选字段（逗号/分号/换行/空白分隔）。"""
    if not value:
        return []

    raw = str(value).strip()
    if not raw:
        return []

    candidates = [item.strip() for item in re.split(r"[,;\n\s]+", raw) if item.strip()]
    unique_items: List[str] = []
    seen = set()
    for email in candidates:
        lowered = email.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique_items.append(email)

    if len(unique_items) > 200:
        raise HTTPException(status_code=400, detail="邮箱列表最多支持 200 项")

    return unique_items


def account_to_response(account: Account) -> AccountResponse:
    """转换 Account 模型为响应模型"""
    return AccountResponse(
        id=account.id,
        email=account.email,
        password=account.password,
        client_id=account.client_id,
        email_service=account.email_service,
        account_id=account.account_id,
        workspace_id=account.workspace_id,
        registered_at=account.registered_at.isoformat() if account.registered_at else None,
        last_refresh=account.last_refresh.isoformat() if account.last_refresh else None,
        expires_at=account.expires_at.isoformat() if account.expires_at else None,
        status=account.status,
        proxy_used=account.proxy_used,
        cpa_uploaded=account.cpa_uploaded or False,
        cpa_uploaded_at=account.cpa_uploaded_at.isoformat() if account.cpa_uploaded_at else None,
        cookies=account.cookies,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


# ============== API Endpoints ==============

@router.get("", response_model=AccountListResponse)
async def list_accounts(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="状态筛选"),
    email_service: Optional[str] = Query(None, description="邮箱服务筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    start_time: Optional[str] = Query(None, description="起始时间（ISO8601 或 YYYY-MM-DD）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO8601 或 YYYY-MM-DD）"),
    email_list: Optional[str] = Query(None, description="邮箱列表（逗号/换行分隔）"),
):
    """
    获取账号列表

    支持分页、状态筛选、邮箱服务筛选、搜索、时间范围与邮箱列表筛选
    """
    start_time_value, _ = _parse_time_filter(start_time, "start_time")
    end_time_value, end_time_date_only = _parse_time_filter(end_time, "end_time")
    email_filter_list = _parse_email_list_filter(email_list)

    with get_db() as db:
        # 构建查询
        query = db.query(Account)

        # 状态筛选
        if status:
            query = query.filter(Account.status == status)

        # 邮箱服务筛选
        if email_service:
            query = query.filter(Account.email_service == email_service)

        # 搜索
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Account.email.ilike(search_pattern)) |
                (Account.account_id.ilike(search_pattern))
            )

        if start_time_value is not None:
            query = query.filter(Account.created_at >= start_time_value)

        if end_time_value is not None:
            if end_time_date_only:
                query = query.filter(Account.created_at < end_time_value + timedelta(days=1))
            else:
                query = query.filter(Account.created_at <= end_time_value)

        if email_filter_list:
            query = query.filter(Account.email.in_(email_filter_list))

        # 统计总数
        total = query.count()

        # 分页
        offset = (page - 1) * page_size
        accounts = query.order_by(Account.created_at.desc()).offset(offset).limit(page_size).all()

        return AccountListResponse(
            total=total,
            accounts=[account_to_response(acc) for acc in accounts]
        )


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: int):
    """获取单个账号详情"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return account_to_response(account)


@router.get("/{account_id}/tokens")
async def get_account_tokens(account_id: int):
    """获取账号的 Token 信息"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        return {
            "id": account.id,
            "email": account.email,
            "access_token": account.access_token,
            "refresh_token": account.refresh_token,
            "id_token": account.id_token,
            "has_tokens": bool(account.access_token and account.refresh_token),
        }


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, request: AccountUpdateRequest):
    """更新账号状态"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        update_data = {}
        if request.status:
            if request.status not in [e.value for e in AccountStatus]:
                raise HTTPException(status_code=400, detail="无效的状态值")
            update_data["status"] = request.status

        if request.metadata:
            current_metadata = account.metadata or {}
            current_metadata.update(request.metadata)
            update_data["metadata"] = current_metadata

        if request.cookies is not None:
            # 留空则清空，非空则更新
            update_data["cookies"] = request.cookies or None

        account = crud.update_account(db, account_id, **update_data)
        return account_to_response(account)


@router.get("/{account_id}/cookies")
async def get_account_cookies(account_id: int):
    """获取账号的 cookie 字符串（仅供支付使用）"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"account_id": account_id, "cookies": account.cookies or ""}


@router.delete("/{account_id}")
async def delete_account(account_id: int):
    """删除单个账号"""
    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        crud.delete_account(db, account_id)
        return {"success": True, "message": f"账号 {account.email} 已删除"}


@router.post("/batch-delete")
async def batch_delete_accounts(request: BatchDeleteRequest):
    """批量删除账号"""
    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )
        deleted_count = 0
        errors = []

        for account_id in ids:
            try:
                account = crud.get_account_by_id(db, account_id)
                if account:
                    crud.delete_account(db, account_id)
                    deleted_count += 1
            except Exception as e:
                errors.append(f"ID {account_id}: {str(e)}")

        return {
            "success": True,
            "deleted_count": deleted_count,
            "errors": errors if errors else None
        }


@router.post("/batch-update")
async def batch_update_accounts(request: BatchUpdateRequest):
    """批量更新账号状态"""
    if request.status not in [e.value for e in AccountStatus]:
        raise HTTPException(status_code=400, detail="无效的状态值")

    with get_db() as db:
        updated_count = 0
        errors = []

        for account_id in request.ids:
            try:
                account = crud.get_account_by_id(db, account_id)
                if account:
                    crud.update_account(db, account_id, status=request.status)
                    updated_count += 1
            except Exception as e:
                errors.append(f"ID {account_id}: {str(e)}")

        return {
            "success": True,
            "updated_count": updated_count,
            "errors": errors if errors else None
        }


class BatchExportRequest(BaseModel):
    """批量导出请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None
    export_mode: Optional[str] = "single_file"  # single_file | per_account_zip


def _normalize_export_mode(export_mode: Optional[str]) -> str:
    """统一导出模式字段，兼容历史别名。"""
    raw = (export_mode or "single_file").strip().lower()
    alias_map = {
        "single": "single_file",
        "single_file": "single_file",
        "all_in_one": "single_file",
        "per_account": "per_account_zip",
        "per_account_zip": "per_account_zip",
        "zip": "per_account_zip",
    }
    normalized = alias_map.get(raw)
    if not normalized:
        raise HTTPException(status_code=400, detail="无效的导出模式，支持: single_file / per_account_zip")
    return normalized


def _safe_filename(source: str, fallback: str = "account") -> str:
    """将任意文本转换为安全文件名。"""
    cleaned = re.sub(r"[^0-9a-zA-Z._-]+", "_", (source or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def _serialize_account(acc: Account) -> dict:
    """统一账号导出字段。"""
    return {
        "email": acc.email,
        "password": acc.password,
        "client_id": acc.client_id,
        "account_id": acc.account_id,
        "workspace_id": acc.workspace_id,
        "access_token": acc.access_token,
        "refresh_token": acc.refresh_token,
        "id_token": acc.id_token,
        "session_token": acc.session_token,
        "email_service": acc.email_service,
        "registered_at": acc.registered_at.isoformat() if acc.registered_at else None,
        "last_refresh": acc.last_refresh.isoformat() if acc.last_refresh else None,
        "expires_at": acc.expires_at.isoformat() if acc.expires_at else None,
        "status": acc.status,
    }


def _build_sub2api_account_entry(acc: Account) -> dict:
    """构造单账号 Sub2API 格式实体。"""
    expires_at = int(acc.expires_at.timestamp()) if acc.expires_at else 0
    return {
        "name": acc.email,
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": acc.access_token or "",
            "chatgpt_account_id": acc.account_id or "",
            "chatgpt_user_id": "",
            "client_id": acc.client_id or "",
            "expires_at": expires_at,
            "expires_in": 863999,
            "model_mapping": {
                "gpt-5.1": "gpt-5.1",
                "gpt-5.1-codex": "gpt-5.1-codex",
                "gpt-5.1-codex-max": "gpt-5.1-codex-max",
                "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
                "gpt-5.2": "gpt-5.2",
                "gpt-5.2-codex": "gpt-5.2-codex",
                "gpt-5.3": "gpt-5.3",
                "gpt-5.3-codex": "gpt-5.3-codex",
                "gpt-5.4": "gpt-5.4"
            },
            "organization_id": acc.workspace_id or "",
            "refresh_token": acc.refresh_token or ""
        },
        "extra": {},
        "concurrency": 10,
        "priority": 1,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True
    }


@router.post("/export/json")
async def export_accounts_json(request: BatchExportRequest):
    """导出账号为 JSON 格式"""
    export_mode = _normalize_export_mode(request.export_mode)

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if export_mode == "single_file":
            export_data = [_serialize_account(acc) for acc in accounts]
            filename = f"accounts_{timestamp}.json"
            content = json.dumps(export_data, ensure_ascii=False, indent=2)
            return StreamingResponse(
                iter([content]),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                account_data = _serialize_account(acc)
                content = json.dumps(account_data, ensure_ascii=False, indent=2)
                zf.writestr(f"{_safe_filename(acc.email)}.json", content)

        zip_buffer.seek(0)
        zip_filename = f"accounts_json_{timestamp}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )


@router.post("/export/csv")
async def export_accounts_csv(request: BatchExportRequest):
    """导出账号为 CSV 格式"""
    import csv

    export_mode = _normalize_export_mode(request.export_mode)

    header = [
        "ID", "Email", "Password", "Client ID",
        "Account ID", "Workspace ID",
        "Access Token", "Refresh Token", "ID Token", "Session Token",
        "Email Service", "Status", "Registered At", "Last Refresh", "Expires At"
    ]

    def _write_single_csv(output: io.StringIO, account: Account):
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerow([
            account.id,
            account.email,
            account.password or "",
            account.client_id or "",
            account.account_id or "",
            account.workspace_id or "",
            account.access_token or "",
            account.refresh_token or "",
            account.id_token or "",
            account.session_token or "",
            account.email_service,
            account.status,
            account.registered_at.isoformat() if account.registered_at else "",
            account.last_refresh.isoformat() if account.last_refresh else "",
            account.expires_at.isoformat() if account.expires_at else ""
        ])

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if export_mode == "single_file":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(header)
            for acc in accounts:
                writer.writerow([
                    acc.id,
                    acc.email,
                    acc.password or "",
                    acc.client_id or "",
                    acc.account_id or "",
                    acc.workspace_id or "",
                    acc.access_token or "",
                    acc.refresh_token or "",
                    acc.id_token or "",
                    acc.session_token or "",
                    acc.email_service,
                    acc.status,
                    acc.registered_at.isoformat() if acc.registered_at else "",
                    acc.last_refresh.isoformat() if acc.last_refresh else "",
                    acc.expires_at.isoformat() if acc.expires_at else ""
                ])

            filename = f"accounts_{timestamp}.csv"
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                output = io.StringIO()
                _write_single_csv(output, acc)
                zf.writestr(f"{_safe_filename(acc.email)}.csv", output.getvalue())

        zip_buffer.seek(0)
        zip_filename = f"accounts_csv_{timestamp}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )


@router.post("/export/sub2api")
async def export_accounts_sub2api(request: BatchExportRequest):
    """导出账号为 Sub2Api 格式。"""
    export_mode = _normalize_export_mode(request.export_mode)

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if export_mode == "single_file":
            payload = {
                "proxies": [],
                "accounts": [_build_sub2api_account_entry(acc) for acc in accounts]
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2)
            filename = f"sub2api_tokens_{timestamp}.json"
            return StreamingResponse(
                iter([content]),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                payload = {
                    "proxies": [],
                    "accounts": [_build_sub2api_account_entry(acc)]
                }
                content = json.dumps(payload, ensure_ascii=False, indent=2)
                zf.writestr(f"{_safe_filename(acc.email)}_sub2api.json", content)

        zip_buffer.seek(0)
        zip_filename = f"sub2api_tokens_{timestamp}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )


@router.post("/export/cpa")
async def export_accounts_cpa(request: BatchExportRequest):
    """导出账号为 CPA Token JSON 格式。"""
    export_mode = _normalize_export_mode(request.export_mode)

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )
        accounts = db.query(Account).filter(Account.id.in_(ids)).all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if export_mode == "single_file":
            payload = {
                "generated_at": datetime.utcnow().isoformat(),
                "accounts": [generate_token_json(acc) for acc in accounts],
            }
            content = json.dumps(payload, ensure_ascii=False, indent=2)
            filename = f"cpa_tokens_{timestamp}.json"
            return StreamingResponse(
                iter([content]),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for acc in accounts:
                token_data = generate_token_json(acc)
                content = json.dumps(token_data, ensure_ascii=False, indent=2)
                zf.writestr(f"{_safe_filename(acc.email)}.json", content)

        zip_buffer.seek(0)
        zip_filename = f"cpa_tokens_{timestamp}.zip"
        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
        )


@router.get("/stats/summary")
async def get_accounts_stats():
    """获取账号统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 总数
        total = db.query(func.count(Account.id)).scalar()

        # 按状态统计
        status_stats = db.query(
            Account.status,
            func.count(Account.id)
        ).group_by(Account.status).all()

        # 按邮箱服务统计
        service_stats = db.query(
            Account.email_service,
            func.count(Account.id)
        ).group_by(Account.email_service).all()

        return {
            "total": total,
            "by_status": {status: count for status, count in status_stats},
            "by_email_service": {service: count for service, count in service_stats}
        }


# ============== Token 刷新相关 ==============

class TokenRefreshRequest(BaseModel):
    """Token 刷新请求"""
    proxy: Optional[str] = None


class BatchRefreshRequest(BaseModel):
    """批量刷新请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None


class TokenValidateRequest(BaseModel):
    """Token 验证请求"""
    proxy: Optional[str] = None


class BatchValidateRequest(BaseModel):
    """批量验证请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None


@router.post("/batch-refresh")
async def batch_refresh_tokens(request: BatchRefreshRequest, background_tasks: BackgroundTasks):
    """批量刷新账号 Token"""
    proxy = _get_proxy(request.proxy)

    results = {
        "success_count": 0,
        "failed_count": 0,
        "errors": []
    }

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )

    for account_id in ids:
        try:
            result = do_refresh(account_id, proxy)
            if result.success:
                results["success_count"] += 1
            else:
                results["failed_count"] += 1
                results["errors"].append({"id": account_id, "error": result.error_message})
        except Exception as e:
            results["failed_count"] += 1
            results["errors"].append({"id": account_id, "error": str(e)})

    return results


@router.post("/{account_id}/refresh")
async def refresh_account_token(account_id: int, request: Optional[TokenRefreshRequest] = Body(default=None)):
    """刷新单个账号的 Token"""
    proxy = _get_proxy(request.proxy if request else None)
    result = do_refresh(account_id, proxy)

    if result.success:
        return {
            "success": True,
            "message": "Token 刷新成功",
            "expires_at": result.expires_at.isoformat() if result.expires_at else None
        }
    else:
        return {
            "success": False,
            "error": result.error_message
        }


@router.post("/batch-validate")
async def batch_validate_tokens(request: BatchValidateRequest):
    """批量验证账号 Token 有效性"""
    proxy = _get_proxy(request.proxy)

    results = {
        "valid_count": 0,
        "invalid_count": 0,
        "details": []
    }

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )

    for account_id in ids:
        try:
            is_valid, error = do_validate(account_id, proxy)
            results["details"].append({
                "id": account_id,
                "valid": is_valid,
                "error": error
            })
            if is_valid:
                results["valid_count"] += 1
            else:
                results["invalid_count"] += 1
        except Exception as e:
            results["invalid_count"] += 1
            results["details"].append({
                "id": account_id,
                "valid": False,
                "error": str(e)
            })

    return results


@router.post("/{account_id}/validate")
async def validate_account_token(account_id: int, request: Optional[TokenValidateRequest] = Body(default=None)):
    """验证单个账号的 Token 有效性"""
    proxy = _get_proxy(request.proxy if request else None)
    is_valid, error = do_validate(account_id, proxy)

    return {
        "id": account_id,
        "valid": is_valid,
        "error": error
    }


# ============== CPA 上传相关 ==============

class CPAUploadRequest(BaseModel):
    """CPA 上传请求"""
    proxy: Optional[str] = None
    cpa_service_id: Optional[int] = None  # 指定 CPA 服务 ID，不传则使用全局配置


class BatchCPAUploadRequest(BaseModel):
    """批量 CPA 上传请求"""
    ids: List[int] = []
    proxy: Optional[str] = None
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None
    cpa_service_id: Optional[int] = None  # 指定 CPA 服务 ID，不传则使用全局配置


@router.post("/batch-upload-cpa")
async def batch_upload_accounts_to_cpa(request: BatchCPAUploadRequest):
    """批量上传账号到 CPA"""

    proxy = request.proxy

    # 解析指定的 CPA 服务
    cpa_api_url = None
    cpa_api_token = None
    include_proxy_url = False
    if request.cpa_service_id:
        with get_db() as db:
            svc = crud.get_cpa_service_by_id(db, request.cpa_service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 CPA 服务不存在")
            cpa_api_url = svc.api_url
            cpa_api_token = svc.api_token
            include_proxy_url = bool(svc.include_proxy_url)

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )

    results = batch_upload_to_cpa(
        ids,
        proxy,
        api_url=cpa_api_url,
        api_token=cpa_api_token,
        include_proxy_url=include_proxy_url,
    )
    return results


@router.post("/{account_id}/upload-cpa")
async def upload_account_to_cpa(account_id: int, request: Optional[CPAUploadRequest] = Body(default=None)):
    """上传单个账号到 CPA"""

    proxy = request.proxy if request else None
    cpa_service_id = request.cpa_service_id if request else None

    # 解析指定的 CPA 服务
    cpa_api_url = None
    cpa_api_token = None
    include_proxy_url = False
    if cpa_service_id:
        with get_db() as db:
            svc = crud.get_cpa_service_by_id(db, cpa_service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 CPA 服务不存在")
            cpa_api_url = svc.api_url
            cpa_api_token = svc.api_token
            include_proxy_url = bool(svc.include_proxy_url)

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        if not account.access_token:
            return {
                "success": False,
                "error": "账号缺少 Token，无法上传"
            }

        # 生成 Token JSON
        token_data = generate_token_json(
            account,
            include_proxy_url=include_proxy_url,
            proxy_url=proxy,
        )

        # 上传
        success, message = upload_to_cpa(token_data, proxy, api_url=cpa_api_url, api_token=cpa_api_token)

        if success:
            account.cpa_uploaded = True
            account.cpa_uploaded_at = datetime.utcnow()
            db.commit()
            return {"success": True, "message": message}
        else:
            return {"success": False, "error": message}


class Sub2ApiUploadRequest(BaseModel):
    """单账号 Sub2API 上传请求"""
    service_id: Optional[int] = None
    concurrency: int = 3
    priority: int = 50


class BatchSub2ApiUploadRequest(BaseModel):
    """批量 Sub2API 上传请求"""
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None
    service_id: Optional[int] = None  # 指定 Sub2API 服务 ID，不传则使用第一个启用的
    concurrency: int = 3
    priority: int = 50


@router.post("/batch-upload-sub2api")
async def batch_upload_accounts_to_sub2api(request: BatchSub2ApiUploadRequest):
    """批量上传账号到 Sub2API"""

    # 解析指定的 Sub2API 服务
    api_url = None
    api_key = None
    if request.service_id:
        with get_db() as db:
            svc = crud.get_sub2api_service_by_id(db, request.service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 Sub2API 服务不存在")
            api_url = svc.api_url
            api_key = svc.api_key
    else:
        with get_db() as db:
            svcs = crud.get_sub2api_services(db, enabled=True)
            if svcs:
                api_url = svcs[0].api_url
                api_key = svcs[0].api_key

    if not api_url or not api_key:
        raise HTTPException(status_code=400, detail="未找到可用的 Sub2API 服务，请先在设置中配置")

    with get_db() as db:
        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )

    results = batch_upload_to_sub2api(
        ids, api_url, api_key,
        concurrency=request.concurrency,
        priority=request.priority,
    )
    return results


@router.post("/{account_id}/upload-sub2api")
async def upload_account_to_sub2api(account_id: int, request: Optional[Sub2ApiUploadRequest] = Body(default=None)):
    """上传单个账号到 Sub2API"""

    service_id = request.service_id if request else None
    concurrency = request.concurrency if request else 3
    priority = request.priority if request else 50

    api_url = None
    api_key = None
    if service_id:
        with get_db() as db:
            svc = crud.get_sub2api_service_by_id(db, service_id)
            if not svc:
                raise HTTPException(status_code=404, detail="指定的 Sub2API 服务不存在")
            api_url = svc.api_url
            api_key = svc.api_key
    else:
        with get_db() as db:
            svcs = crud.get_sub2api_services(db, enabled=True)
            if svcs:
                api_url = svcs[0].api_url
                api_key = svcs[0].api_key

    if not api_url or not api_key:
        raise HTTPException(status_code=400, detail="未找到可用的 Sub2API 服务，请先在设置中配置")

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not account.access_token:
            return {"success": False, "error": "账号缺少 Token，无法上传"}

        success, message = upload_to_sub2api(
            [account], api_url, api_key,
            concurrency=concurrency, priority=priority
        )
        if success:
            return {"success": True, "message": message}
        else:
            return {"success": False, "error": message}


# ============== Team Manager 上传 ==============

class UploadTMRequest(BaseModel):
    service_id: Optional[int] = None


class BatchUploadTMRequest(BaseModel):
    ids: List[int] = []
    select_all: bool = False
    status_filter: Optional[str] = None
    email_service_filter: Optional[str] = None
    search_filter: Optional[str] = None
    start_time_filter: Optional[str] = None
    end_time_filter: Optional[str] = None
    email_list_filter: Optional[str] = None
    service_id: Optional[int] = None


@router.post("/batch-upload-tm")
async def batch_upload_accounts_to_tm(request: BatchUploadTMRequest):
    """批量上传账号到 Team Manager"""

    with get_db() as db:
        if request.service_id:
            svc = crud.get_tm_service_by_id(db, request.service_id)
        else:
            svcs = crud.get_tm_services(db, enabled=True)
            svc = svcs[0] if svcs else None

        if not svc:
            raise HTTPException(status_code=400, detail="未找到可用的 Team Manager 服务，请先在设置中配置")

        api_url = svc.api_url
        api_key = svc.api_key

        ids = resolve_account_ids(
            db,
            request.ids,
            request.select_all,
            request.status_filter,
            request.email_service_filter,
            request.search_filter,
            request.start_time_filter,
            request.end_time_filter,
            request.email_list_filter,
        )

    results = batch_upload_to_team_manager(ids, api_url, api_key)
    return results


@router.post("/{account_id}/upload-tm")
async def upload_account_to_tm(account_id: int, request: Optional[UploadTMRequest] = Body(default=None)):
    """上传单账号到 Team Manager"""

    service_id = request.service_id if request else None

    with get_db() as db:
        if service_id:
            svc = crud.get_tm_service_by_id(db, service_id)
        else:
            svcs = crud.get_tm_services(db, enabled=True)
            svc = svcs[0] if svcs else None

        if not svc:
            raise HTTPException(status_code=400, detail="未找到可用的 Team Manager 服务，请先在设置中配置")

        api_url = svc.api_url
        api_key = svc.api_key

        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        success, message = upload_to_team_manager(account, api_url, api_key)

    return {"success": success, "message": message}


# ============== Inbox Code ==============

def _build_inbox_config(db, service_type, email_service_id: Optional[str]) -> Optional[dict]:
    """仅为临时邮箱账号构建收件箱查询配置。"""
    from ...database.models import EmailService as EmailServiceModel
    from ...services import EmailServiceType as EST

    if service_type != EST.TEMPMAIL:
        return None

    settings = get_settings()
    ensure_builtin_tempmail_services(db, settings)
    runtime_state = get_tempmail_runtime_state(db, settings)

    def _build_service_config(svc: Optional[EmailServiceModel]) -> Optional[dict]:
        if not svc or not svc.config:
            return None

        service_config = dict(svc.config)
        if "api_url" in service_config and "base_url" not in service_config:
            service_config["base_url"] = service_config.pop("api_url")

        provider = str(svc.provider or service_config.get("provider") or "tempmail_lol")
        service_config["provider"] = provider
        service_config.setdefault("timeout", int(settings.tempmail_timeout or 30))
        service_config.setdefault("max_retries", int(settings.tempmail_max_retries or 3))
        return service_config

    candidate_ids: List[int] = []
    if email_service_id:
        try:
            candidate_ids.append(int(email_service_id))
        except (TypeError, ValueError):
            pass

    single_service_id = runtime_state.get("single_service_id")
    global_service_id = runtime_state.get("global_service_id")
    if isinstance(single_service_id, int) and single_service_id > 0:
        candidate_ids.append(single_service_id)
    if isinstance(global_service_id, int) and global_service_id > 0:
        candidate_ids.append(global_service_id)

    for service_id in dict.fromkeys(candidate_ids):
        svc = db.query(EmailServiceModel).filter(
            EmailServiceModel.id == service_id,
            EmailServiceModel.service_type == EST.TEMPMAIL.value,
        ).first()
        config = _build_service_config(svc)
        if config:
            return config

    fallback_service = db.query(EmailServiceModel).filter(
        EmailServiceModel.service_type == EST.TEMPMAIL.value,
        EmailServiceModel.enabled == True,
    ).order_by(
        EmailServiceModel.priority.asc(),
        EmailServiceModel.id.asc(),
    ).first()
    return _build_service_config(fallback_service)


@router.post("/{account_id}/inbox-code")
async def get_account_inbox_code(account_id: int):
    """查询账号邮箱收件箱最新验证码"""
    from ...services import EmailServiceFactory, EmailServiceType

    with get_db() as db:
        account = crud.get_account_by_id(db, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        try:
            service_type = EmailServiceType(account.email_service)
        except ValueError:
            return {"success": False, "error": "不支持的邮箱服务类型"}

        config = _build_inbox_config(db, service_type, account.email_service_id)
        if config is None:
            return {"success": False, "error": "未找到可用的邮箱服务配置"}

        try:
            svc = EmailServiceFactory.create(service_type, config)
            code = svc.get_verification_code(
                account.email,
                email_id=account.email_service_id,
                timeout=12
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

        if not code:
            return {"success": False, "error": "未收到验证码邮件"}

        return {"success": True, "code": code, "email": account.email}
