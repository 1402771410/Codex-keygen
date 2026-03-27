"""日志相关 API 路由。"""

from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from ...config.settings import get_settings
from ...database.models import EmailService, RegistrationTask
from ...database.session import get_db

router = APIRouter()

_LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d+)?)\s+\[(?P<level>[A-Z]+)\]\s+(?P<source>[^:]+):\s*(?P<message>.*)$"
)

_TASK_LINE_PATTERN = re.compile(r"^\[(?P<hms>\d{2}:\d{2}:\d{2})\]\s*(?P<message>.*)$")


def _resolve_runtime_log_path() -> Path:
    """解析运行日志路径。"""
    settings = get_settings()
    configured = Path(str(settings.log_file or "logs/app.log"))

    candidates: List[Path] = []
    env_logs_dir = os.environ.get("APP_LOGS_DIR")
    if env_logs_dir:
        candidates.append(Path(env_logs_dir) / configured.name)

    if configured.is_absolute():
        candidates.append(configured)
    else:
        candidates.append(Path.cwd() / configured)
        candidates.append(Path.cwd() / "logs" / configured.name)
        candidates.append(Path("logs") / configured.name)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0] if candidates else configured


def _tail_lines(path: Path, limit: int) -> List[str]:
    """读取文件尾部若干行。"""
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8", errors="ignore") as file:
        return [line.rstrip("\n") for line in deque(file, maxlen=limit)]


def _parse_runtime_line(raw_line: str) -> Dict[str, Any]:
    """解析运行日志行为结构化对象。"""
    match = _LOG_PATTERN.match(raw_line)
    if not match:
        return {
            "timestamp": None,
            "level": "UNKNOWN",
            "source": "unknown",
            "message": raw_line,
            "raw": raw_line,
        }

    timestamp_text = match.group("timestamp")
    parsed_timestamp: Optional[str] = None
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed_timestamp = datetime.strptime(timestamp_text, fmt).isoformat()
            break
        except ValueError:
            continue

    return {
        "timestamp": parsed_timestamp,
        "level": match.group("level"),
        "source": match.group("source").strip(),
        "message": match.group("message").strip(),
        "raw": raw_line,
    }


def _normalize_keyword(keyword: Optional[str]) -> Optional[str]:
    if keyword is None:
        return None
    normalized = keyword.strip().lower()
    return normalized or None


def _parse_task_log_time(task: RegistrationTask, raw_line: str) -> tuple[Optional[datetime], str]:
    """尽可能从任务日志行推断时间。"""
    fallback = task.completed_at or task.started_at or task.created_at
    match = _TASK_LINE_PATTERN.match(raw_line)
    if not match:
        return fallback, raw_line

    if not fallback:
        return None, match.group("message").strip()

    try:
        parsed_time = datetime.strptime(match.group("hms"), "%H:%M:%S").time()
        composed = datetime.combine(fallback.date(), parsed_time)
        return composed, match.group("message").strip()
    except ValueError:
        return fallback, match.group("message").strip()


def _append_event(
    bucket: List[Dict[str, Any]],
    *,
    timestamp: Optional[datetime],
    category: str,
    action: str,
    level: str,
    title: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    bucket.append(
        {
            "timestamp": timestamp.isoformat() if timestamp else None,
            "category": category,
            "action": action,
            "level": level,
            "title": title,
            "message": message,
            "metadata": metadata or {},
        }
    )


@router.get("/run")
async def get_runtime_logs(
    lines: int = Query(200, ge=20, le=2000, description="返回日志行数"),
    level: Optional[str] = Query(None, description="日志级别过滤，例如 INFO/ERROR"),
    keyword: Optional[str] = Query(None, description="关键词过滤"),
):
    """读取运行日志（应用日志）。"""
    path = _resolve_runtime_log_path()
    raw_lines = _tail_lines(path, max(lines * 4, lines))
    normalized_level = (level or "").strip().upper()
    normalized_keyword = _normalize_keyword(keyword)

    entries: List[Dict[str, Any]] = []
    for raw_line in raw_lines:
        entry = _parse_runtime_line(raw_line)
        if normalized_level and entry["level"] != normalized_level:
            continue
        if normalized_keyword:
            haystack = f"{entry['source']} {entry['message']} {entry['raw']}".lower()
            if normalized_keyword not in haystack:
                continue
        entries.append(entry)

    entries = entries[-lines:]
    return {
        "file": str(path),
        "total": len(entries),
        "entries": entries,
    }


@router.get("/operations")
async def get_operation_logs(
    limit: int = Query(200, ge=20, le=1000, description="返回条数"),
    keyword: Optional[str] = Query(None, description="关键词过滤"),
    category: Optional[str] = Query(None, description="类别过滤：registration/tempmail"),
):
    """读取操作日志（任务行为 + 临时邮箱测试行为）。"""
    normalized_keyword = _normalize_keyword(keyword)
    normalized_category = (category or "").strip().lower() or None

    events: List[Dict[str, Any]] = []
    with get_db() as db:
        recent_tasks = (
            db.query(RegistrationTask)
            .order_by(RegistrationTask.created_at.desc())
            .limit(120)
            .all()
        )
        recent_tempmail_tests = (
            db.query(EmailService)
            .filter(
                EmailService.service_type == "tempmail",
                EmailService.last_tested_at.isnot(None),
            )
            .order_by(EmailService.last_tested_at.desc())
            .limit(120)
            .all()
        )

    for task in recent_tasks:
        base_meta = {"task_uuid": task.task_uuid, "task_id": task.id}
        _append_event(
            events,
            timestamp=task.created_at,
            category="registration",
            action="task_created",
            level="INFO",
            title="创建注册任务",
            message=f"任务 {task.task_uuid} 已创建",
            metadata=base_meta,
        )

        if task.started_at:
            _append_event(
                events,
                timestamp=task.started_at,
                category="registration",
                action="task_started",
                level="INFO",
                title="启动注册任务",
                message=f"任务 {task.task_uuid} 开始执行",
                metadata=base_meta,
            )

        if task.completed_at:
            completion_level = "INFO" if task.status == "completed" else "WARNING"
            _append_event(
                events,
                timestamp=task.completed_at,
                category="registration",
                action=f"task_{task.status}",
                level=completion_level,
                title="任务结束",
                message=f"任务 {task.task_uuid} 结束状态：{task.status}",
                metadata=base_meta,
            )

        if task.error_message:
            _append_event(
                events,
                timestamp=task.completed_at or task.started_at or task.created_at,
                category="registration",
                action="task_error",
                level="ERROR",
                title="任务异常",
                message=task.error_message,
                metadata=base_meta,
            )

        if task.logs:
            lines = [line.strip() for line in str(task.logs).splitlines() if line.strip()]
            for raw_line in lines[-4:]:
                log_time, clean_message = _parse_task_log_time(task, raw_line)
                message_lower = clean_message.lower()
                level = "ERROR" if "失败" in clean_message or "error" in message_lower else "INFO"
                _append_event(
                    events,
                    timestamp=log_time,
                    category="registration",
                    action="task_log",
                    level=level,
                    title="任务日志",
                    message=clean_message,
                    metadata=base_meta,
                )

    for service in recent_tempmail_tests:
        status = str(service.last_test_status or "unknown").lower()
        level = "INFO" if status == "success" else "WARNING"
        _append_event(
            events,
            timestamp=service.last_tested_at,
            category="tempmail",
            action="service_test",
            level=level,
            title="临时邮箱连通性测试",
            message=f"{service.name}: {service.last_test_message or '无测试详情'}",
            metadata={"service_id": service.id, "service_name": service.name, "status": status},
        )

    if normalized_category:
        events = [item for item in events if item["category"] == normalized_category]

    if normalized_keyword:
        filtered: List[Dict[str, Any]] = []
        for item in events:
            haystack = f"{item['title']} {item['message']} {item['action']} {item['category']}"
            if normalized_keyword in haystack.lower():
                filtered.append(item)
        events = filtered

    events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    trimmed = events[:limit]
    return {
        "total": len(trimmed),
        "entries": trimmed,
    }
