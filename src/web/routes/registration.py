"""
注册任务 API 路由
"""

import asyncio
import logging
import uuid
import random
from threading import Lock
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple, Any, Coroutine, Set, cast

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from ...database import crud
from ...database.session import get_db
from ...database.models import RegistrationTask, Proxy
from ...database.tempmail_bootstrap import ensure_builtin_tempmail_services, get_tempmail_runtime_state, is_global_tempmail_service
from ...core.register import RegistrationEngine, RegistrationResult
from ...services import EmailServiceFactory, EmailServiceType
from ...services.tempmail_catalog import build_tempmail_config, get_tempmail_provider_meta
from ...config.settings import get_settings
from ..task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 任务存储（简单的内存存储，生产环境应使用 Redis）
running_tasks: dict = {}
# 批量任务存储
batch_tasks: Dict[str, dict] = {}
# 与请求生命周期解耦的后台任务引用，避免任务被 GC 或随连接断开中断。
_detached_background_tasks: Set[asyncio.Task[Any]] = set()
# 临时邮箱服务选择锁，确保并发时的轮询选择具备一致性。
_tempmail_selection_lock = Lock()


def _spawn_detached_coroutine(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """启动与 HTTP 请求解耦的后台协程。"""
    loop = task_manager.get_loop()
    try:
        running_loop = asyncio.get_running_loop()
        loop = running_loop
    except RuntimeError:
        if loop is None:
            loop = asyncio.get_event_loop()
            task_manager.set_loop(loop)

    task = loop.create_task(coro)
    _detached_background_tasks.add(task)

    def _cleanup(done_task: asyncio.Task[Any]):
        _detached_background_tasks.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            logger.warning("后台协程被取消")
            return

        if exc:
            logger.error(f"后台协程异常: {exc}")

    task.add_done_callback(_cleanup)
    return task


# ============== Proxy Helper Functions ==============

def get_proxy_for_registration(db) -> Tuple[Optional[str], Optional[int]]:
    """
    获取用于注册的代理

    策略：
    1. 优先从代理列表中随机选择一个启用的代理
    2. 如果代理列表为空且启用了动态代理，调用动态代理 API 获取
    3. 否则使用系统设置中的静态默认代理

    Returns:
        Tuple[proxy_url, proxy_id]: 代理 URL 和代理 ID（如果来自代理列表）
    """
    # 先尝试从代理列表中获取
    proxy = crud.get_random_proxy(db)
    if proxy:
        return proxy.proxy_url, proxy.id

    # 代理列表为空，尝试动态代理或静态代理
    from ...core.dynamic_proxy import get_proxy_url_for_task
    proxy_url = get_proxy_url_for_task()
    if proxy_url:
        return proxy_url, None

    return None, None


def update_proxy_usage(db, proxy_id: Optional[int]):
    """更新代理的使用时间"""
    if proxy_id:
        crud.update_proxy_last_used(db, proxy_id)


# ============== Pydantic Models ==============

class RegistrationTaskCreate(BaseModel):
    """创建注册任务请求"""
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []  # 指定 CPA 服务 ID 列表，空则取第一个启用的
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []  # 指定 Sub2API 服务 ID 列表
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []  # 指定 TM 服务 ID 列表


class BatchRegistrationRequest(BaseModel):
    """批量注册请求"""
    count: int = 1
    registration_mode: str = "batch"  # batch | loop
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    interval_min: int = 5
    interval_max: int = 30
    concurrency: int = 1
    mode: str = "pipeline"
    window_start: Optional[str] = None  # HH:MM，循环模式必填
    window_end: Optional[str] = None    # HH:MM，循环模式必填
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []


class RegistrationTaskResponse(BaseModel):
    """注册任务响应"""
    id: int
    task_uuid: str
    status: str
    email_service_id: Optional[int] = None
    proxy: Optional[str] = None
    logs: Optional[str] = None
    result: Optional[dict] = None
    settings: Optional[dict] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


class BatchRegistrationResponse(BaseModel):
    """批量注册响应"""
    batch_id: str
    count: int
    tasks: List[RegistrationTaskResponse] = Field(default_factory=list)
    registration_mode: str = "batch"
    window_start: Optional[str] = None
    window_end: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[RegistrationTaskResponse]


# ============== Helper Functions ==============

def task_to_response(task: RegistrationTask, settings: Optional[dict] = None) -> RegistrationTaskResponse:
    """转换任务模型为响应"""
    return RegistrationTaskResponse(
        id=task.id,
        task_uuid=task.task_uuid,
        status=task.status,
        email_service_id=task.email_service_id,
        proxy=task.proxy,
        logs=task.logs,
        result=task.result,
        settings=settings,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _normalize_email_service_config(
    service_type: EmailServiceType,
    config: Optional[dict],
    proxy_url: Optional[str] = None
) -> dict:
    """标准化临时邮箱配置。"""
    normalized = dict(config or {})

    if service_type == EmailServiceType.TEMPMAIL:
        settings = get_settings()
        provider_hint = str(normalized.get("provider") or "").strip() or None
        normalized = build_tempmail_config(normalized, settings, provider_hint=provider_hint)
    if proxy_url and 'proxy_url' not in normalized:
        normalized['proxy_url'] = proxy_url

    return normalized


def _parse_hhmm(value: str) -> Tuple[int, int]:
    """解析 HH:MM 文本。"""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("时间格式必须为 HH:MM") from exc
    return parsed.hour, parsed.minute


def _normalize_time_window(window_start: str, window_end: str) -> Tuple[str, str]:
    """标准化时间窗口，返回零填充后的 HH:MM。"""
    start_hour, start_minute = _parse_hhmm(window_start)
    end_hour, end_minute = _parse_hhmm(window_end)
    return f"{start_hour:02d}:{start_minute:02d}", f"{end_hour:02d}:{end_minute:02d}"


def _is_pop3_alias_provider(provider_raw: Any) -> bool:
    return str(provider_raw or "").strip().lower() == "pop3_alias"


def _extract_tempmail_provider(service: Any) -> str:
    config = dict(cast(dict, service.config or {}))
    return str(service.provider or config.get("provider") or "tempmail_lol").strip().lower()


def _get_loop_window_state(
    window_start: str,
    window_end: str,
    now: Optional[datetime] = None,
) -> Tuple[bool, int]:
    """
    计算当前是否在每日循环注册时间窗口内。

    Returns:
        Tuple[是否在窗口内, 距下次窗口开始秒数(在窗口内时为0)]
    """
    start_hour, start_minute = _parse_hhmm(window_start)
    end_hour, end_minute = _parse_hhmm(window_end)

    current = now or datetime.now()
    now_minutes = current.hour * 60 + current.minute
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    # 开始和结束一致时按全天可运行处理。
    if start_minutes == end_minutes:
        return True, 0

    if start_minutes < end_minutes:
        in_window = start_minutes <= now_minutes < end_minutes
    else:
        # 跨午夜窗口，例如 22:00-02:00。
        in_window = now_minutes >= start_minutes or now_minutes < end_minutes

    if in_window:
        return True, 0

    next_start = current.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
    )
    if current >= next_start:
        next_start += timedelta(days=1)

    wait_seconds = int((next_start - current).total_seconds())
    return False, max(wait_seconds, 1)


def _select_tempmail_service(db, settings, explicit_service_id: Optional[int]):
    """根据请求与运行时规则选择临时邮箱服务。"""
    from ...database.models import EmailService as EmailServiceModel

    ensure_builtin_tempmail_services(db, settings)
    runtime_state = get_tempmail_runtime_state(db, settings)

    base_query = db.query(EmailServiceModel).filter(
        EmailServiceModel.service_type == EmailServiceType.TEMPMAIL.value,
        EmailServiceModel.enabled == True,
    )

    if explicit_service_id:
        selected = base_query.filter(EmailServiceModel.id == explicit_service_id).first()
        if not selected:
            raise ValueError(f"临时邮箱服务不存在或已禁用: {explicit_service_id}")
        if _is_pop3_alias_provider(_extract_tempmail_provider(selected)):
            raise ValueError("POP 注册方式已下线，请改用临时邮箱规则（Tempmail.lol/GuerrillaMail）")
        return selected

    selection_mode = str(runtime_state["selection_mode"] or "single").strip().lower()
    if selection_mode == "single":
        single_service_id = runtime_state["single_service_id"]
        if single_service_id:
            selected = base_query.filter(EmailServiceModel.id == single_service_id).first()
            if selected:
                if _is_pop3_alias_provider(_extract_tempmail_provider(selected)):
                    logger.warning("single 模式指定了已下线 POP 规则(ID=%s)，自动回退到临时邮箱规则", single_service_id)
                else:
                    return selected
            else:
                raise ValueError(f"single 模式指定服务不存在或已禁用: {single_service_id}")

        candidates = base_query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()
        for item in candidates:
            if not _is_pop3_alias_provider(_extract_tempmail_provider(item)):
                return item
        return None

    # multi 模式按 last_used + priority 轮询
    candidates = base_query.order_by(
        EmailServiceModel.last_used.is_(None).desc(),
        EmailServiceModel.last_used.asc(),
        EmailServiceModel.priority.asc(),
        EmailServiceModel.id.asc(),
    ).all()
    for item in candidates:
        if not _is_pop3_alias_provider(_extract_tempmail_provider(item)):
            return item
    return None


def _select_and_mark_tempmail_service(db, settings, explicit_service_id: Optional[int]):
    """在锁内完成服务选择与最近使用时间更新，避免并发下重复分配。"""
    with _tempmail_selection_lock:
        selected_service = _select_tempmail_service(db, settings, explicit_service_id)
        if selected_service:
            selected_service.last_used = datetime.utcnow()
            db.commit()
            db.refresh(selected_service)
        return selected_service


def _run_sync_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: Optional[List[int]] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: Optional[List[int]] = None, auto_upload_tm: bool = False, tm_service_ids: Optional[List[int]] = None):
    """
    在线程池中执行的同步注册任务

    这个函数会被 run_in_executor 调用，运行在独立线程中
    """
    status_context: Dict[str, Any] = {}
    if batch_id:
        status_context["batch_id"] = batch_id

    with get_db() as db:
        try:
            # 检查是否已取消
            if task_manager.is_cancelled(task_uuid):
                logger.info(f"任务 {task_uuid} 已取消，跳过执行")
                return

            # 更新任务状态为运行中
            task = crud.update_registration_task(
                db, task_uuid,
                status="running",
                started_at=datetime.utcnow()
            )

            if not task:
                logger.error(f"任务不存在: {task_uuid}")
                return

            # 更新 TaskManager 状态
            task_manager.update_status(task_uuid, "running", **status_context)

            # 确定使用的代理
            # 如果前端传入了代理参数，使用传入的
            # 否则从代理列表或系统设置中获取
            actual_proxy_url = proxy
            proxy_id = None

            if not actual_proxy_url:
                actual_proxy_url, proxy_id = get_proxy_for_registration(db)
                if actual_proxy_url:
                    logger.info(f"任务 {task_uuid} 使用代理: {actual_proxy_url[:50]}...")

            # 更新任务的代理记录
            crud.update_registration_task(db, task_uuid, proxy=actual_proxy_url)

            # 创建邮箱服务（仅临时邮箱规则）
            service_type = EmailServiceType(email_service_type)
            settings = get_settings()
            selected_is_global_tempmail = False

            if service_type == EmailServiceType.TEMPMAIL:
                selected_service = _select_and_mark_tempmail_service(db, settings, email_service_id)

                if not selected_service:
                    raise ValueError("没有可用的启用临时邮箱服务，请先在临时邮箱池中启用至少一个服务")

                selected_config = dict(cast(dict, selected_service.config or {}))
                provider = str(selected_service.provider or selected_config.get("provider") or "tempmail_lol")
                if _is_pop3_alias_provider(provider):
                    raise ValueError("POP 注册方式已下线，请改用临时邮箱规则（Tempmail.lol/GuerrillaMail）")
                selected_config["provider"] = provider
                selected_is_global_tempmail = is_global_tempmail_service(selected_service)
                config = _normalize_email_service_config(service_type, selected_config, actual_proxy_url)
                crud.update_registration_task(db, task_uuid, email_service_id=selected_service.id)
                provider_meta = get_tempmail_provider_meta(provider)
                logger.info(
                    "使用临时邮箱池服务: %s (ID: %s, provider: %s, priority: %s)",
                    selected_service.name,
                    selected_service.id,
                    provider_meta.get("label") or provider,
                    selected_service.priority,
                )
            else:
                raise ValueError("当前仅支持临时邮箱规则注册")

            email_service = EmailServiceFactory.create(service_type, config)

            # 创建注册引擎 - 使用 TaskManager 的日志回调
            log_callback = task_manager.create_log_callback(task_uuid, prefix=log_prefix, batch_id=batch_id)

            engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=actual_proxy_url,
                callback_logger=log_callback,
                task_uuid=task_uuid,
                use_global_tempmail_limit=selected_is_global_tempmail,
                check_cancelled=task_manager.create_check_cancelled_callback(task_uuid),
            )

            # 执行注册
            result = engine.run()

            if result.success:
                # 更新代理使用时间
                update_proxy_usage(db, proxy_id)

                # 保存到数据库
                engine.save_to_database(result)

                # 自动上传到 CPA（可多服务）
                if auto_upload_cpa:
                    try:
                        from ...core.upload.cpa_upload import upload_to_cpa, generate_token_json
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            _cpa_ids = cpa_service_ids or []
                            if not _cpa_ids:
                                # 未指定则取所有启用的服务
                                _cpa_ids = [s.id for s in crud.get_cpa_services(db, enabled=True)]
                            if not _cpa_ids:
                                log_callback("[CPA] 无可用 CPA 服务，跳过上传")
                            for _sid in _cpa_ids:
                                try:
                                    _svc = crud.get_cpa_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    token_data = generate_token_json(
                                        saved_account,
                                        include_proxy_url=bool(_svc.include_proxy_url),
                                    )
                                    log_callback(f"[CPA] 上传到服务: {_svc.name}")
                                    _ok, _msg = upload_to_cpa(token_data, api_url=_svc.api_url, api_token=_svc.api_token)
                                    if _ok:
                                        saved_account.cpa_uploaded = True
                                        saved_account.cpa_uploaded_at = datetime.utcnow()
                                        db.commit()
                                        log_callback(f"[CPA] 上传成功: {_svc.name}")
                                    else:
                                        log_callback(f"[CPA] 上传失败({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[CPA] 异常({_sid}): {_e}")
                    except Exception as cpa_err:
                        log_callback(f"[CPA] 上传异常: {cpa_err}")

                # 自动上传到 Sub2API（可多服务）
                if auto_upload_sub2api:
                    try:
                        from ...core.upload.sub2api_upload import upload_to_sub2api
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            _s2a_ids = sub2api_service_ids or []
                            if not _s2a_ids:
                                _s2a_ids = [s.id for s in crud.get_sub2api_services(db, enabled=True)]
                            if not _s2a_ids:
                                log_callback("[Sub2API] 无可用 Sub2API 服务，跳过上传")
                            for _sid in _s2a_ids:
                                try:
                                    _svc = crud.get_sub2api_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    log_callback(f"[Sub2API] 上传到服务: {_svc.name}")
                                    _ok, _msg = upload_to_sub2api([saved_account], _svc.api_url, _svc.api_key)
                                    log_callback(f"[Sub2API] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[Sub2API] 异常({_sid}): {_e}")
                    except Exception as s2a_err:
                        log_callback(f"[Sub2API] 上传异常: {s2a_err}")

                # 自动上传到 Team Manager（可多服务）
                if auto_upload_tm:
                    try:
                        from ...core.upload.team_manager_upload import upload_to_team_manager
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            _tm_ids = tm_service_ids or []
                            if not _tm_ids:
                                _tm_ids = [s.id for s in crud.get_tm_services(db, enabled=True)]
                            if not _tm_ids:
                                log_callback("[TM] 无可用 Team Manager 服务，跳过上传")
                            for _sid in _tm_ids:
                                try:
                                    _svc = crud.get_tm_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    log_callback(f"[TM] 上传到服务: {_svc.name}")
                                    _ok, _msg = upload_to_team_manager(saved_account, _svc.api_url, _svc.api_key)
                                    log_callback(f"[TM] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[TM] 异常({_sid}): {_e}")
                    except Exception as tm_err:
                        log_callback(f"[TM] 上传异常: {tm_err}")

                # 更新任务状态
                crud.update_registration_task(
                    db, task_uuid,
                    status="completed",
                    completed_at=datetime.utcnow(),
                    result=result.to_dict()
                )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "completed", email=result.email, **status_context)

                logger.info(f"注册任务完成: {task_uuid}, 邮箱: {result.email}")
            else:
                # 更新任务状态为失败
                crud.update_registration_task(
                    db, task_uuid,
                    status="failed",
                    completed_at=datetime.utcnow(),
                    error_message=result.error_message
                )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "failed", error=result.error_message, **status_context)

                logger.warning(f"注册任务失败: {task_uuid}, 原因: {result.error_message}")

        except Exception as e:
            logger.error(f"注册任务异常: {task_uuid}, 错误: {e}")

            try:
                with get_db() as db:
                    crud.update_registration_task(
                        db, task_uuid,
                        status="failed",
                        completed_at=datetime.utcnow(),
                        error_message=str(e)
                    )

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "failed", error=str(e), **status_context)
            except:
                pass


async def run_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: Optional[List[int]] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: Optional[List[int]] = None, auto_upload_tm: bool = False, tm_service_ids: Optional[List[int]] = None):
    """
    异步执行注册任务

    使用 run_in_executor 将同步任务放入线程池执行，避免阻塞主事件循环
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    status_context: Dict[str, Any] = {}
    if batch_id:
        status_context["batch_id"] = batch_id

    # 初始化 TaskManager 状态
    task_manager.update_status(task_uuid, "pending", **status_context)
    task_manager.add_log(task_uuid, f"{log_prefix} [系统] 任务 {task_uuid[:8]} 已加入队列" if log_prefix else f"[系统] 任务 {task_uuid[:8]} 已加入队列")

    try:
        # 在线程池中执行同步任务（传入 log_prefix 和 batch_id 供回调使用）
        await loop.run_in_executor(
            task_manager.executor,
            _run_sync_registration_task,
            task_uuid,
            email_service_type,
            proxy,
            email_service_config,
            email_service_id,
            log_prefix,
            batch_id,
            auto_upload_cpa,
            cpa_service_ids or [],
            auto_upload_sub2api,
            sub2api_service_ids or [],
            auto_upload_tm,
            tm_service_ids or [],
        )
    except Exception as e:
        logger.error(f"线程池执行异常: {task_uuid}, 错误: {e}")
        task_manager.add_log(task_uuid, f"[错误] 线程池执行异常: {str(e)}")
        task_manager.update_status(task_uuid, "failed", error=str(e), **status_context)


def _init_batch_state(
    batch_id: str,
    task_uuids: List[str],
    *,
    registration_mode: str = "batch",
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
):
    """初始化批量任务内存状态（支持 batch/loop）。"""
    existing = batch_tasks.get(batch_id, {})
    now_iso = datetime.utcnow().isoformat()
    total = len(task_uuids)
    task_manager.init_batch(batch_id, total)

    state = {
        "total": total,
        "target_success": int(existing.get("target_success", total)),
        "attempts": int(existing.get("attempts", 0)),
        "completed": int(existing.get("completed", 0)),
        "success": int(existing.get("success", 0)),
        "failed": int(existing.get("failed", 0)),
        "cancelled": bool(existing.get("cancelled", False)),
        "task_uuids": list(task_uuids),
        "current_index": int(existing.get("current_index", 0)),
        "logs": list(existing.get("logs", [])),
        "finished": bool(existing.get("finished", False)),
        "status": str(existing.get("status", "running")),
        "registration_mode": registration_mode,
        "window_start": window_start,
        "window_end": window_end,
        "in_window": bool(existing.get("in_window", registration_mode != "loop")),
        "next_window_seconds": int(existing.get("next_window_seconds", 0)),
        "running": int(existing.get("running", 0)),
        "next_run_at": existing.get("next_run_at"),
        "created_at": existing.get("created_at", now_iso),
        "updated_at": existing.get("updated_at", now_iso),
    }

    for key, value in existing.items():
        if key not in state:
            state[key] = value

    batch_tasks[batch_id] = state
    task_manager.update_batch_status(
        batch_id,
        total=state["total"],
        completed=state["completed"],
        success=state["success"],
        failed=state["failed"],
        current_index=state["current_index"],
        finished=state["finished"],
        cancelled=state["cancelled"],
        status=state["status"],
        registration_mode=state["registration_mode"],
        window_start=state["window_start"],
        window_end=state["window_end"],
        in_window=state["in_window"],
        next_window_seconds=state["next_window_seconds"],
        running=state["running"],
        next_run_at=state["next_run_at"],
        target_success=state["target_success"],
        attempts=state["attempts"],
        created_at=state["created_at"],
        updated_at=state["updated_at"],
    )


def _make_batch_helpers(batch_id: str):
    """返回 add_batch_log 和 update_batch_status 辅助函数"""
    def add_batch_log(msg: str):
        logs = batch_tasks[batch_id]["logs"]
        logs.append(msg)
        # 防止大批量任务日志无限增长导致内存/前端卡顿。
        if len(logs) > 2000:
            del logs[: len(logs) - 2000]
        task_manager.add_batch_log(batch_id, msg)

    def update_batch_status(**kwargs):
        now_iso = datetime.utcnow().isoformat()
        if "created_at" not in batch_tasks[batch_id]:
            batch_tasks[batch_id]["created_at"] = now_iso
        payload = {
            "updated_at": now_iso,
            "created_at": batch_tasks[batch_id]["created_at"],
            **kwargs,
        }
        for key, value in kwargs.items():
            if key in batch_tasks[batch_id]:
                batch_tasks[batch_id][key] = value
        batch_tasks[batch_id]["updated_at"] = payload["updated_at"]
        task_manager.update_batch_status(batch_id, **payload)

    return add_batch_log, update_batch_status


async def run_batch_parallel(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
):
    """
    并行模式：所有任务同时提交，Semaphore 控制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    add_batch_log(f"[系统] 并行模式启动，并发数: {concurrency}，总任务: {len(task_uuids)}")

    async def _run_one(idx: int, uuid: str):
        prefix = f"[任务{idx + 1}]"
        async with semaphore:
            await run_registration_task(
                uuid, email_service_type, proxy, email_service_config, email_service_id,
                log_prefix=prefix, batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
            )
        with get_db() as db:
            t = crud.get_registration_task(db, uuid)
            if t:
                async with counter_lock:
                    new_completed = batch_tasks[batch_id]["completed"] + 1
                    new_success = batch_tasks[batch_id]["success"]
                    new_failed = batch_tasks[batch_id]["failed"]
                    if t.status == "completed":
                        new_success += 1
                        add_batch_log(f"{prefix} [成功] 注册成功")
                    elif t.status == "failed":
                        new_failed += 1
                        add_batch_log(f"{prefix} [失败] 注册失败: {t.error_message}")
                    update_batch_status(completed=new_completed, success=new_success, failed=new_failed)

    try:
        await asyncio.gather(*[_run_one(i, u) for i, u in enumerate(task_uuids)], return_exceptions=True)
        if not task_manager.is_batch_cancelled(batch_id):
            add_batch_log(f"[完成] 批量任务完成！成功: {batch_tasks[batch_id]['success']}, 失败: {batch_tasks[batch_id]['failed']}")
            update_batch_status(finished=True, status="completed")
        else:
            update_batch_status(finished=True, status="cancelled")
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_pipeline(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
):
    """
    流水线模式：每隔 interval 秒启动一个新任务，Semaphore 限制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    running_tasks_list = []
    add_batch_log(f"[系统] 流水线模式启动，并发数: {concurrency}，总任务: {len(task_uuids)}")

    async def _run_and_release(idx: int, uuid: str, pfx: str):
        try:
            await run_registration_task(
                uuid, email_service_type, proxy, email_service_config, email_service_id,
                log_prefix=pfx, batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
            )
            with get_db() as db:
                t = crud.get_registration_task(db, uuid)
                if t:
                    async with counter_lock:
                        new_completed = batch_tasks[batch_id]["completed"] + 1
                        new_success = batch_tasks[batch_id]["success"]
                        new_failed = batch_tasks[batch_id]["failed"]
                        if t.status == "completed":
                            new_success += 1
                            add_batch_log(f"{pfx} [成功] 注册成功")
                        elif t.status == "failed":
                            new_failed += 1
                            add_batch_log(f"{pfx} [失败] 注册失败: {t.error_message}")
                        update_batch_status(completed=new_completed, success=new_success, failed=new_failed)
        finally:
            semaphore.release()

    try:
        for i, task_uuid in enumerate(task_uuids):
            if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
                with get_db() as db:
                    for remaining_uuid in task_uuids[i:]:
                        crud.update_registration_task(db, remaining_uuid, status="cancelled")
                add_batch_log("[取消] 批量任务已取消")
                update_batch_status(finished=True, status="cancelled")
                break

            update_batch_status(current_index=i)
            await semaphore.acquire()
            prefix = f"[任务{i + 1}]"
            add_batch_log(f"{prefix} 开始注册...")
            t = asyncio.create_task(_run_and_release(i, task_uuid, prefix))
            running_tasks_list.append(t)

            if i < len(task_uuids) - 1 and not task_manager.is_batch_cancelled(batch_id):
                wait_time = random.randint(interval_min, interval_max)
                logger.info(f"批量任务 {batch_id}: 等待 {wait_time} 秒后启动下一个任务")
                await asyncio.sleep(wait_time)

        if running_tasks_list:
            await asyncio.gather(*running_tasks_list, return_exceptions=True)

        if not task_manager.is_batch_cancelled(batch_id):
            add_batch_log(f"[完成] 批量任务完成！成功: {batch_tasks[batch_id]['success']}, 失败: {batch_tasks[batch_id]['failed']}")
            update_batch_status(finished=True, status="completed")
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


def _create_registration_task_for_loop(proxy: Optional[str]) -> str:
    """循环注册中动态创建一条待执行任务记录。"""
    task_uuid = str(uuid.uuid4())
    with get_db() as db:
        crud.create_registration_task(
            db,
            task_uuid=task_uuid,
            proxy=proxy,
        )
    return task_uuid


def _format_wait_seconds(seconds: int) -> str:
    """将秒数格式化为易读文本。"""
    if seconds <= 0:
        return "0秒"
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    parts: List[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if sec and not hours:
        # 时段等待日志不需要太细，超过1小时时省略秒。
        parts.append(f"{sec}秒")
    return "".join(parts) or "1秒"


def _build_single_settings_snapshot(request: RegistrationTaskCreate) -> Dict[str, Any]:
    """构建单任务设置快照，供页面重连恢复。"""
    email_service_config = _sanitize_email_service_config_for_snapshot(request.email_service_config)
    return {
        "registration_mode": "single",
        "email_service_type": request.email_service_type,
        "email_service_id": request.email_service_id,
        "email_service_config": email_service_config,
        "proxy": request.proxy,
        "auto_upload_cpa": request.auto_upload_cpa,
        "cpa_service_ids": request.cpa_service_ids,
        "auto_upload_sub2api": request.auto_upload_sub2api,
        "sub2api_service_ids": request.sub2api_service_ids,
        "auto_upload_tm": request.auto_upload_tm,
        "tm_service_ids": request.tm_service_ids,
    }


def _build_batch_settings_snapshot(
    request: BatchRegistrationRequest,
    *,
    normalized_window_start: Optional[str] = None,
    normalized_window_end: Optional[str] = None,
) -> Dict[str, Any]:
    """构建批量/循环任务设置快照。"""
    email_service_config = _sanitize_email_service_config_for_snapshot(request.email_service_config)
    return {
        "registration_mode": request.registration_mode,
        "count": request.count,
        "email_service_type": request.email_service_type,
        "email_service_id": request.email_service_id,
        "email_service_config": email_service_config,
        "proxy": request.proxy,
        "interval_min": request.interval_min,
        "interval_max": request.interval_max,
        "concurrency": request.concurrency,
        "mode": request.mode,
        "window_start": normalized_window_start if normalized_window_start is not None else request.window_start,
        "window_end": normalized_window_end if normalized_window_end is not None else request.window_end,
        "auto_upload_cpa": request.auto_upload_cpa,
        "cpa_service_ids": request.cpa_service_ids,
        "auto_upload_sub2api": request.auto_upload_sub2api,
        "sub2api_service_ids": request.sub2api_service_ids,
        "auto_upload_tm": request.auto_upload_tm,
        "tm_service_ids": request.tm_service_ids,
    }


def _sanitize_email_service_config_for_snapshot(config: Optional[dict]) -> Optional[Dict[str, Any]]:
    """对邮箱配置进行脱敏后再写入任务快照。"""
    if not config:
        return None

    sanitized = dict(config)
    for secret_key in ("password", "api_key", "api_token", "token"):
        if secret_key in sanitized:
            sanitized[secret_key] = ""
    return sanitized


def _is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled"}


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """解析 ISO 时间字符串，失败时返回 None。"""
    if not value or not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _batch_active_sort_key(item: Dict[str, Any]) -> Tuple[int, int, datetime, int]:
    """批量任务活跃排序键：优先有运行中 worker 的任务，再按状态/时间/活动度排序。"""
    status = str(item.get("status", ""))
    status_score = {
        "running": 4,
        "target_reached": 3,
        "waiting_window": 2,
        "pending": 1,
    }.get(status, 0)
    running_score = 1 if int(item.get("running", 0)) > 0 else 0
    ts = _parse_iso_datetime(item.get("updated_at"))
    if ts is None:
        ts = _parse_iso_datetime(item.get("next_run_at"))
    if ts is None:
        ts = _parse_iso_datetime(item.get("created_at"))
    if ts is None:
        ts = datetime.min
    activity = int(item.get("attempts", 0)) + int(item.get("current_index", 0))
    return running_score, status_score, ts, activity


def _single_active_sort_key(item: Dict[str, Any]) -> Tuple[int, datetime]:
    """单任务活跃排序键：按状态和最新更新时间排序。"""
    status = str(item.get("status", ""))
    status_score = {
        "running": 3,
        "pending": 2,
        "cancelling": 1,
    }.get(status, 0)
    ts = _parse_iso_datetime(item.get("updated_at"))
    if ts is None:
        ts = _parse_iso_datetime(item.get("created_at"))
    if ts is None:
        ts = datetime.min
    return status_score, ts


async def run_batch_loop(
    batch_id: str,
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int,
    window_start: str,
    window_end: str,
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
):
    """循环注册：在指定时间窗口内持续发起注册任务。"""
    task_uuids = list(batch_tasks.get(batch_id, {}).get("task_uuids", []))
    _init_batch_state(
        batch_id,
        task_uuids,
        registration_mode="loop",
        window_start=window_start,
        window_end=window_end,
    )

    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    running_tasks_set: set = set()
    launch_index = batch_tasks[batch_id].get("total", 0)
    waiting_logged = False

    update_batch_status(status="running", finished=False, registration_mode="loop")
    add_batch_log(
        f"[系统] 循环注册模式启动，并发数: {concurrency}，窗口: {window_start}-{window_end}"
    )

    async def _run_and_release(idx: int, task_uuid: str, pfx: str):
        try:
            await run_registration_task(
                task_uuid,
                email_service_type,
                proxy,
                email_service_config,
                email_service_id,
                log_prefix=pfx,
                batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa,
                cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api,
                sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm,
                tm_service_ids=tm_service_ids or [],
            )

            with get_db() as db:
                task = crud.get_registration_task(db, task_uuid)

            if task:
                async with counter_lock:
                    new_completed = batch_tasks[batch_id]["completed"] + 1
                    new_success = batch_tasks[batch_id]["success"]
                    new_failed = batch_tasks[batch_id]["failed"]
                    if task.status == "completed":
                        new_success += 1
                        add_batch_log(f"{pfx} [成功] 注册成功")
                    elif task.status == "failed":
                        new_failed += 1
                        add_batch_log(f"{pfx} [失败] 注册失败: {task.error_message}")

                    current_running = max(0, batch_tasks[batch_id].get("running", 1) - 1)
                    update_batch_status(
                        completed=new_completed,
                        success=new_success,
                        failed=new_failed,
                        running=current_running,
                    )
        finally:
            semaphore.release()

    try:
        while True:
            cancelled = task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False)
            if cancelled:
                update_batch_status(cancelled=True, status="cancelling")
                add_batch_log("[取消] 已收到循环注册取消请求，等待运行中的任务结束...")
                break

            in_window, wait_seconds = _get_loop_window_state(window_start, window_end)
            update_batch_status(in_window=in_window, next_window_seconds=wait_seconds)

            if not in_window:
                update_batch_status(status="waiting_window", next_run_at=None)
                if not waiting_logged:
                    add_batch_log(
                        f"[等待] 当前不在注册时段 {window_start}-{window_end}，"
                        f"将在 {_format_wait_seconds(wait_seconds)} 后重试"
                    )
                    waiting_logged = True

                await asyncio.sleep(min(max(wait_seconds, 1), 60))
                continue

            if waiting_logged:
                add_batch_log("[系统] 已进入注册时段，恢复循环注册")
                waiting_logged = False

            await semaphore.acquire()

            cancelled_after_wait = task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False)
            if cancelled_after_wait:
                semaphore.release()
                update_batch_status(cancelled=True, status="cancelling")
                add_batch_log("[取消] 循环注册已停止发起新任务")
                break

            launch_index += 1
            task_uuid = _create_registration_task_for_loop(proxy)
            batch_tasks[batch_id]["task_uuids"].append(task_uuid)

            async with counter_lock:
                new_total = batch_tasks[batch_id]["total"] + 1
                new_running = batch_tasks[batch_id].get("running", 0) + 1
                update_batch_status(total=new_total, current_index=launch_index, running=new_running, status="running")

            prefix = f"[循环任务{launch_index}]"
            add_batch_log(f"{prefix} 开始注册...")

            running_task = asyncio.create_task(_run_and_release(launch_index, task_uuid, prefix))
            running_tasks_set.add(running_task)
            running_task.add_done_callback(lambda done_task: running_tasks_set.discard(done_task))

            wait_time = random.randint(interval_min, interval_max)
            next_run_at = datetime.now() + timedelta(seconds=wait_time)
            update_batch_status(next_run_at=next_run_at.isoformat())

            # 间隔期内也要快速响应取消请求。
            remaining = wait_time
            while remaining > 0:
                if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False):
                    break
                sleep_chunk = min(1, remaining)
                await asyncio.sleep(sleep_chunk)
                remaining -= sleep_chunk

        if running_tasks_set:
            await asyncio.gather(*list(running_tasks_set), return_exceptions=True)

        if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False):
            update_batch_status(finished=True, status="cancelled", running=0, next_run_at=None)
            add_batch_log(
                f"[完成] 循环注册已停止，累计成功: {batch_tasks[batch_id]['success']}，"
                f"失败: {batch_tasks[batch_id]['failed']}"
            )
        else:
            update_batch_status(finished=True, status="completed", running=0, next_run_at=None)
            add_batch_log("[完成] 循环注册已结束")
    except Exception as e:
        logger.error(f"循环注册任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 循环注册异常: {str(e)}")
        update_batch_status(finished=True, status="failed", running=0, next_run_at=None)
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_target_success(
    batch_id: str,
    target_success_count: int,
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int,
    mode: str,
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
):
    """批量模式：以“成功数量”作为完成条件，按需动态创建任务。"""
    _init_batch_state(batch_id, [], registration_mode="batch")
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)

    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    running_tasks_set: Set[asyncio.Task[Any]] = set()
    launch_index = int(batch_tasks[batch_id].get("current_index", 0))

    update_batch_status(
        status="running",
        finished=False,
        total=target_success_count,
        target_success=target_success_count,
        completed=0,
        success=0,
        failed=0,
        attempts=0,
        running=0,
        registration_mode="batch",
    )
    add_batch_log(
        f"[系统] 批量目标成功模式启动，目标成功数: {target_success_count}，"
        f"并发数: {concurrency}，模式: {mode}"
    )

    async def _run_and_release(attempt_no: int, task_uuid: str, prefix: str):
        try:
            await run_registration_task(
                task_uuid,
                email_service_type,
                proxy,
                email_service_config,
                email_service_id,
                log_prefix=prefix,
                batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa,
                cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api,
                sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm,
                tm_service_ids=tm_service_ids or [],
            )

            with get_db() as db:
                task = crud.get_registration_task(db, task_uuid)

            async with counter_lock:
                new_success = batch_tasks[batch_id]["success"]
                new_failed = batch_tasks[batch_id]["failed"]
                new_attempts = batch_tasks[batch_id].get("attempts", 0) + 1

                if task and task.status == "completed":
                    new_success += 1
                    add_batch_log(
                        f"{prefix} [成功] 注册成功 (目标进度: {new_success}/{target_success_count})"
                    )
                elif task:
                    new_failed += 1
                    add_batch_log(f"{prefix} [失败] 注册失败: {task.error_message}")
                else:
                    new_failed += 1
                    add_batch_log(f"{prefix} [失败] 未找到任务结果记录")

                current_running = max(0, batch_tasks[batch_id].get("running", 1) - 1)
                new_completed = min(new_success, target_success_count)

                update_kwargs: Dict[str, Any] = {
                    "success": new_success,
                    "failed": new_failed,
                    "attempts": new_attempts,
                    "completed": new_completed,
                    "running": current_running,
                }

                if new_success >= target_success_count and not batch_tasks[batch_id].get("cancelled", False):
                    update_kwargs["status"] = "target_reached"

                update_batch_status(**update_kwargs)
        finally:
            semaphore.release()

    try:
        while True:
            cancelled = task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False)
            if cancelled:
                update_batch_status(cancelled=True, status="cancelling")
                add_batch_log("[取消] 已收到批量任务取消请求，停止发起新任务")
                break

            if batch_tasks[batch_id].get("success", 0) >= target_success_count:
                add_batch_log(
                    f"[系统] 已达到目标成功数 {target_success_count}，等待运行中任务收尾"
                )
                break

            await semaphore.acquire()

            cancelled_after_wait = task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False)
            if cancelled_after_wait or batch_tasks[batch_id].get("success", 0) >= target_success_count:
                semaphore.release()
                break

            launch_index += 1
            task_uuid = _create_registration_task_for_loop(proxy)
            batch_tasks[batch_id]["task_uuids"].append(task_uuid)

            async with counter_lock:
                new_running = batch_tasks[batch_id].get("running", 0) + 1
                update_batch_status(current_index=launch_index, running=new_running, status="running")

            prefix = f"[任务{launch_index}]"
            add_batch_log(f"{prefix} 开始注册...")

            running_task = asyncio.create_task(_run_and_release(launch_index, task_uuid, prefix))
            running_tasks_set.add(running_task)
            running_task.add_done_callback(lambda done_task: running_tasks_set.discard(done_task))

            if mode == "pipeline":
                wait_time = random.randint(interval_min, interval_max)
                next_run_at = datetime.now() + timedelta(seconds=wait_time)
                update_batch_status(next_run_at=next_run_at.isoformat())

                remaining = wait_time
                while remaining > 0:
                    if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False):
                        break
                    if batch_tasks[batch_id].get("success", 0) >= target_success_count:
                        break
                    sleep_chunk = min(1, remaining)
                    await asyncio.sleep(sleep_chunk)
                    remaining -= sleep_chunk
            else:
                update_batch_status(next_run_at=None)
                await asyncio.sleep(0)

        if running_tasks_set:
            await asyncio.gather(*list(running_tasks_set), return_exceptions=True)

        success_count = batch_tasks[batch_id].get("success", 0)
        failed_count = batch_tasks[batch_id].get("failed", 0)
        attempts = batch_tasks[batch_id].get("attempts", 0)

        if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id].get("cancelled", False):
            update_batch_status(finished=True, status="cancelled", running=0, next_run_at=None)
            add_batch_log(
                f"[完成] 批量任务已取消，目标成功数: {target_success_count}，"
                f"累计成功: {success_count}，失败: {failed_count}，尝试: {attempts}"
            )
        elif success_count >= target_success_count:
            update_batch_status(finished=True, status="completed", running=0, next_run_at=None)
            add_batch_log(
                f"[完成] 已达到目标成功数 {target_success_count}，"
                f"累计失败: {failed_count}，尝试: {attempts}"
            )
        else:
            update_batch_status(finished=True, status="failed", running=0, next_run_at=None)
            add_batch_log(
                f"[错误] 批量任务异常结束，目标成功数: {target_success_count}，"
                f"累计成功: {success_count}，失败: {failed_count}"
            )
    except Exception as e:
        logger.error(f"批量目标成功任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量目标成功任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed", running=0, next_run_at=None)
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_registration(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int = 1,
    mode: str = "pipeline",
    auto_upload_cpa: bool = False,
    cpa_service_ids: Optional[List[int]] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: Optional[List[int]] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: Optional[List[int]] = None,
    registration_mode: str = "fixed",
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
    target_success_count: Optional[int] = None,
):
    """根据 mode 分发到并行或流水线执行"""
    if registration_mode == "loop":
        if not window_start or not window_end:
            raise ValueError("循环注册模式缺少时间窗口参数")
        await run_batch_loop(
            batch_id=batch_id,
            email_service_type=email_service_type,
            proxy=proxy,
            email_service_config=email_service_config,
            email_service_id=email_service_id,
            interval_min=interval_min,
            interval_max=interval_max,
            concurrency=concurrency,
            window_start=window_start,
            window_end=window_end,
            auto_upload_cpa=auto_upload_cpa,
            cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api,
            sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm,
            tm_service_ids=tm_service_ids,
        )
        return

    if registration_mode == "batch_target_success":
        if not target_success_count or target_success_count < 1:
            raise ValueError("批量目标成功模式缺少有效 target_success_count")
        await run_batch_target_success(
            batch_id=batch_id,
            target_success_count=target_success_count,
            email_service_type=email_service_type,
            proxy=proxy,
            email_service_config=email_service_config,
            email_service_id=email_service_id,
            interval_min=interval_min,
            interval_max=interval_max,
            concurrency=concurrency,
            mode=mode,
            auto_upload_cpa=auto_upload_cpa,
            cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api,
            sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm,
            tm_service_ids=tm_service_ids,
        )
        return

    if mode == "parallel":
        await run_batch_parallel(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )
    else:
        await run_batch_pipeline(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id,
            interval_min, interval_max, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )


# ============== API Endpoints ==============

@router.post("/start", response_model=RegistrationTaskResponse)
async def start_registration(
    request: RegistrationTaskCreate,
    background_tasks: BackgroundTasks
):
    """
    启动注册任务

    - email_service_type: 邮箱服务类型（仅支持 tempmail）
    - proxy: 代理地址
    - email_service_config: 邮箱服务配置
    """
    # 验证邮箱服务类型
    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    if request.email_service_type != EmailServiceType.TEMPMAIL.value:
        raise HTTPException(status_code=400, detail="POP 注册方式已下线，请改用临时邮箱规则")

    # 创建任务
    task_uuid = str(uuid.uuid4())
    settings_snapshot = _build_single_settings_snapshot(request)

    with get_db() as db:
        task = crud.create_registration_task(
            db,
            task_uuid=task_uuid,
            proxy=request.proxy
        )

    task_manager.update_status(
        task_uuid,
        "pending",
        registration_mode="single",
        settings=settings_snapshot,
    )

    # 在后台运行注册任务（与请求连接解耦，页面关闭不影响执行）
    _spawn_detached_coroutine(
        run_registration_task(
            task_uuid,
            request.email_service_type,
            request.proxy,
            request.email_service_config,
            request.email_service_id,
            "",
            "",
            request.auto_upload_cpa,
            request.cpa_service_ids,
            request.auto_upload_sub2api,
            request.sub2api_service_ids,
            request.auto_upload_tm,
            request.tm_service_ids,
        )
    )

    return task_to_response(task)


@router.post("/batch", response_model=BatchRegistrationResponse)
async def start_batch_registration(
    request: BatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动批量注册任务

    - count: 目标成功数量 (1-999999)
    - registration_mode: batch(固定批量) 或 loop(循环注册)
    - email_service_type: 邮箱服务类型
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    # 验证参数
    if request.registration_mode not in ("batch", "loop"):
        raise HTTPException(status_code=400, detail="registration_mode 必须为 batch 或 loop")

    if request.registration_mode == "batch" and (request.count < 1 or request.count > 999999):
        raise HTTPException(status_code=400, detail="注册数量必须在 1-999999 之间")

    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    if request.email_service_type != EmailServiceType.TEMPMAIL.value:
        raise HTTPException(status_code=400, detail="POP 注册方式已下线，请改用临时邮箱规则")

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    if not 1 <= request.concurrency <= 50:
        raise HTTPException(status_code=400, detail="并发数必须在 1-50 之间")

    if request.mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="模式必须为 parallel 或 pipeline")

    normalized_window_start = request.window_start
    normalized_window_end = request.window_end
    settings_snapshot = _build_batch_settings_snapshot(request)
    if request.registration_mode == "loop":
        if not request.window_start or not request.window_end:
            raise HTTPException(status_code=400, detail="循环注册模式必须设置注册时间段")
        try:
            normalized_window_start, normalized_window_end = _normalize_time_window(
                request.window_start,
                request.window_end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        settings_snapshot = _build_batch_settings_snapshot(
            request,
            normalized_window_start=normalized_window_start,
            normalized_window_end=normalized_window_end,
        )

        batch_id = str(uuid.uuid4())
        _init_batch_state(
            batch_id,
            [],
            registration_mode="loop",
            window_start=normalized_window_start,
            window_end=normalized_window_end,
        )
        batch_tasks[batch_id]["config_snapshot"] = settings_snapshot
        task_manager.update_batch_status(batch_id, config_snapshot=settings_snapshot)

        _spawn_detached_coroutine(
            run_batch_registration(
                batch_id,
                [],
                request.email_service_type,
                request.proxy,
                request.email_service_config,
                request.email_service_id,
                request.interval_min,
                request.interval_max,
                request.concurrency,
                request.mode,
                request.auto_upload_cpa,
                request.cpa_service_ids,
                request.auto_upload_sub2api,
                request.sub2api_service_ids,
                request.auto_upload_tm,
                request.tm_service_ids,
                "loop",
                normalized_window_start,
                normalized_window_end,
                None,
            )
        )

        return BatchRegistrationResponse(
            batch_id=batch_id,
            count=0,
            tasks=[],
            registration_mode="loop",
            window_start=normalized_window_start,
            window_end=normalized_window_end,
        )

    # 创建批量任务（目标成功模式：不预创建全部任务，按需调度，避免大批量卡顿）
    batch_id = str(uuid.uuid4())
    _init_batch_state(batch_id, [], registration_mode="batch")
    batch_tasks[batch_id].update({
        "total": request.count,
        "target_success": request.count,
        "completed": 0,
        "success": 0,
        "failed": 0,
        "attempts": 0,
        "status": "pending",
        "finished": False,
        "config_snapshot": settings_snapshot,
    })
    task_manager.update_batch_status(
        batch_id,
        total=request.count,
        target_success=request.count,
        completed=0,
        success=0,
        failed=0,
        attempts=0,
        status="pending",
        finished=False,
        config_snapshot=settings_snapshot,
    )

    # 在后台运行批量注册（与请求连接解耦）
    _spawn_detached_coroutine(
        run_batch_registration(
            batch_id,
            [],
            request.email_service_type,
            request.proxy,
            request.email_service_config,
            request.email_service_id,
            request.interval_min,
            request.interval_max,
            request.concurrency,
            request.mode,
            request.auto_upload_cpa,
            request.cpa_service_ids,
            request.auto_upload_sub2api,
            request.sub2api_service_ids,
            request.auto_upload_tm,
            request.tm_service_ids,
            "batch_target_success",
            None,
            None,
            request.count,
        )
    )

    return BatchRegistrationResponse(
        batch_id=batch_id,
        count=request.count,
        tasks=[],
        registration_mode="batch",
    )


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """获取批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "target_success": batch.get("target_success", batch["total"]),
        "attempts": batch.get("attempts", 0),
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "status": batch.get("status", "running"),
        "registration_mode": batch.get("registration_mode", "batch"),
        "window_start": batch.get("window_start"),
        "window_end": batch.get("window_end"),
        "in_window": batch.get("in_window", True),
        "next_window_seconds": batch.get("next_window_seconds", 0),
        "running": batch.get("running", 0),
        "next_run_at": batch.get("next_run_at"),
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "config_snapshot": batch.get("config_snapshot"),
        "progress": f"{batch['completed']}/{batch['total']}"
    }


@router.get("/active")
async def get_active_registration_tasks():
    """获取当前仍在执行的任务（用于浏览器重开后的状态恢复）。"""
    single_items = []
    for task_uuid, status_data in task_manager.get_all_task_statuses().items():
        status = str(status_data.get("status", ""))
        if not status or _is_terminal_status(status):
            continue
        single_items.append({
            "mode": "single",
            "task_uuid": task_uuid,
            "status": status,
            "batch_id": status_data.get("batch_id"),
            "settings": status_data.get("settings") or {},
            "created_at": status_data.get("created_at"),
            "updated_at": status_data.get("updated_at"),
        })

    batch_items = []
    for batch_id, batch in batch_tasks.items():
        status = str(batch.get("status", "running"))
        finished = bool(batch.get("finished", False))
        if finished or _is_terminal_status(status):
            continue

        batch_items.append({
            "mode": batch.get("registration_mode", "batch"),
            "batch_id": batch_id,
            "status": status,
            "total": batch.get("total", 0),
            "target_success": batch.get("target_success", batch.get("total", 0)),
            "attempts": batch.get("attempts", 0),
            "success": batch.get("success", 0),
            "failed": batch.get("failed", 0),
            "completed": batch.get("completed", 0),
            "running": batch.get("running", 0),
            "window_start": batch.get("window_start"),
            "window_end": batch.get("window_end"),
            "in_window": batch.get("in_window", True),
            "next_window_seconds": batch.get("next_window_seconds", 0),
            "next_run_at": batch.get("next_run_at"),
            "config_snapshot": batch.get("config_snapshot") or {},
            "created_at": batch.get("created_at"),
            "updated_at": batch.get("updated_at"),
        })

    if batch_items:
        # 优先恢复批量/循环任务，且优先选择当前最活跃的任务，避免误绑到历史残留任务。
        batch_items.sort(key=_batch_active_sort_key, reverse=True)

    active_batch_ids = {item["batch_id"] for item in batch_items}
    filtered_single_items = [
        item
        for item in single_items
        if not (item.get("batch_id") and item.get("batch_id") in active_batch_ids)
    ]

    if filtered_single_items:
        filtered_single_items.sort(key=_single_active_sort_key, reverse=True)

    active_candidates = len(batch_items) + len(filtered_single_items)
    active = batch_items[0] if batch_items else (filtered_single_items[0] if filtered_single_items else None)

    return {
        "active": active,
        "active_count": active_candidates,
        "active_ambiguous": active_candidates > 1,
        "single_tasks": filtered_single_items,
        "batch_tasks": batch_items,
    }


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    batch["cancelled"] = True
    task_manager.cancel_batch(batch_id)
    return {"success": True, "message": "批量任务取消请求已提交"}


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """获取任务列表"""
    with get_db() as db:
        query = db.query(RegistrationTask)

        if status:
            query = query.filter(RegistrationTask.status == status)

        total = query.count()
        offset = (page - 1) * page_size
        tasks = query.order_by(RegistrationTask.created_at.desc()).offset(offset).limit(page_size).all()

        return TaskListResponse(
            total=total,
            tasks=[task_to_response(t) for t in tasks]
        )


@router.get("/tasks/{task_uuid}", response_model=RegistrationTaskResponse)
async def get_task(task_uuid: str):
    """获取任务详情"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        status_data = task_manager.get_status(task_uuid) or {}
        settings_snapshot = status_data.get("settings")
        return task_to_response(task, settings=settings_snapshot)


@router.get("/tasks/{task_uuid}/logs")
async def get_task_logs(task_uuid: str):
    """获取任务日志"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        logs = task.logs or ""
        return {
            "task_uuid": task_uuid,
            "status": task.status,
            "logs": logs.split("\n") if logs else []
        }


@router.post("/tasks/{task_uuid}/cancel")
async def cancel_task(task_uuid: str):
    """取消任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status not in ["pending", "running"]:
            raise HTTPException(status_code=400, detail="任务已完成或已取消")

        task = crud.update_registration_task(db, task_uuid, status="cancelled")

        return {"success": True, "message": "任务已取消"}


@router.delete("/tasks/{task_uuid}")
async def delete_task(task_uuid: str):
    """删除任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status == "running":
            raise HTTPException(status_code=400, detail="无法删除运行中的任务")

        crud.delete_registration_task(db, task_uuid)

        return {"success": True, "message": "任务已删除"}


@router.get("/stats")
async def get_registration_stats():
    """获取注册统计信息"""
    with get_db() as db:
        from sqlalchemy import func

        # 按状态统计
        status_stats = db.query(
            RegistrationTask.status,
            func.count(RegistrationTask.id)
        ).group_by(RegistrationTask.status).all()

        # 今日注册数
        today = datetime.utcnow().date()
        today_count = db.query(func.count(RegistrationTask.id)).filter(
            func.date(RegistrationTask.created_at) == today
        ).scalar()

        return {
            "by_status": {status: count for status, count in status_stats},
            "today_count": today_count
        }


@router.get("/available-services")
async def get_available_email_services():
    """
    获取可用于注册的邮箱服务列表（仅邮箱规则池）。

    说明：注册页仅支持临时邮箱规则，POP 注册方式已下线。
    """
    from ...database.models import EmailService as EmailServiceModel
    settings = get_settings()
    services = []
    runtime_state = {
        "selection_mode": "single",
        "single_service_id": None,
    }

    with get_db() as db:
        ensure_builtin_tempmail_services(db, settings)
        runtime_state = get_tempmail_runtime_state(db, settings)
        db_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "tempmail",
            EmailServiceModel.enabled == True,
        ).order_by(
            EmailServiceModel.priority.asc(),
            EmailServiceModel.id.asc(),
        ).all()

        for service in db_services:
            test_status = str(service.last_test_status or "").strip().lower()
            test_message = str(service.last_test_message or "").strip().lower()
            if test_status != "success" or "[otp_received]" not in test_message:
                continue

            config = service.config or {}
            provider = str(service.provider or config.get("provider") or "tempmail_lol")
            if _is_pop3_alias_provider(provider):
                continue
            provider_meta = get_tempmail_provider_meta(provider)
            description = (
                f"{provider_meta.get('label') or provider} / "
                f"前缀: {config.get('address_prefix') or '-'}"
            )
            services.append({
                "id": service.id,
                "name": service.name,
                "type": "tempmail",
                "provider": provider,
                "provider_label": provider_meta.get("label") or provider,
                "is_builtin": bool(service.is_builtin),
                "is_immutable": bool(service.is_immutable),
                "priority": service.priority,
                "description": description,
            })

    return {
        "tempmail": {
            "available": bool(services),
            "count": len(services),
            "services": services,
        },
        "selection": {
            "mode": runtime_state["selection_mode"],
            "single_service_id": runtime_state["single_service_id"],
        },
    }
