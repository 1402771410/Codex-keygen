import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import BackgroundTasks
import pytest

from src.web.routes import registration as registration_routes
from src.web import task_manager as task_manager_module


@pytest.fixture(autouse=True)
def clear_runtime_state():
    registration_routes.batch_tasks.clear()
    task_manager_module._task_status.clear()
    task_manager_module._batch_status.clear()
    task_manager_module._task_cancelled.clear()
    task_manager_module._log_queues.clear()
    task_manager_module._batch_logs.clear()
    yield
    registration_routes.batch_tasks.clear()
    task_manager_module._task_status.clear()
    task_manager_module._batch_status.clear()
    task_manager_module._task_cancelled.clear()
    task_manager_module._log_queues.clear()
    task_manager_module._batch_logs.clear()


@dataclass
class DummyTask:
    id: int
    task_uuid: str
    status: str = "pending"
    email_service_id: Optional[int] = None
    proxy: Optional[str] = None
    logs: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


def test_start_registration_spawns_detached_job(monkeypatch):
    captured = {}

    def fake_spawn(coro):
        captured["called"] = True
        # 避免 RuntimeWarning: coroutine was never awaited
        coro.close()
        return None

    @contextmanager
    def fake_get_db():
        yield object()

    monkeypatch.setattr(registration_routes, "_spawn_detached_coroutine", fake_spawn)
    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)
    monkeypatch.setattr(
        registration_routes.crud,
        "create_registration_task",
        lambda db, task_uuid, proxy: DummyTask(id=1, task_uuid=task_uuid, proxy=proxy),
    )

    request = registration_routes.RegistrationTaskCreate(email_service_type="tempmail")
    response = asyncio.run(registration_routes.start_registration(request, BackgroundTasks()))

    assert captured.get("called") is True
    assert response.status == "pending"
    assert isinstance(response.task_uuid, str)


def test_start_batch_registration_uses_target_success_mode(monkeypatch):
    captured = {}

    def fake_spawn(coro):
        captured["called"] = True
        coro.close()
        return None

    monkeypatch.setattr(registration_routes, "_spawn_detached_coroutine", fake_spawn)

    request = registration_routes.BatchRegistrationRequest(
        count=12,
        registration_mode="batch",
        email_service_type="tempmail",
        interval_min=0,
        interval_max=1,
        concurrency=2,
        mode="pipeline",
    )

    response = asyncio.run(registration_routes.start_batch_registration(request, BackgroundTasks()))

    assert captured.get("called") is True
    assert response.registration_mode == "batch"
    assert response.count == 12
    assert response.tasks == []

    state = registration_routes.batch_tasks[response.batch_id]
    assert state["total"] == 12
    assert state["target_success"] == 12
    assert state["attempts"] == 0
    assert state["success"] == 0
    assert state["failed"] == 0


def test_run_registration_task_records_batch_id_for_worker_status(monkeypatch):
    task_uuid = "worker-status-1"
    batch_id = "batch-parent-1"

    monkeypatch.setattr(registration_routes, "_run_sync_registration_task", lambda *args, **kwargs: None)

    asyncio.run(
        registration_routes.run_registration_task(
            task_uuid=task_uuid,
            email_service_type="tempmail",
            proxy=None,
            email_service_config=None,
            batch_id=batch_id,
        )
    )

    status = registration_routes.task_manager.get_status(task_uuid)
    assert status is not None
    assert status["status"] == "pending"
    assert status["batch_id"] == batch_id


def test_get_batch_status_returns_target_success_and_attempts():
    batch_id = "batch-test-1"
    registration_routes.batch_tasks[batch_id] = {
        "total": 10,
        "target_success": 10,
        "attempts": 14,
        "completed": 6,
        "success": 6,
        "failed": 8,
        "current_index": 14,
        "cancelled": False,
        "finished": False,
        "status": "running",
        "registration_mode": "batch",
        "window_start": None,
        "window_end": None,
        "in_window": True,
        "next_window_seconds": 0,
        "running": 2,
        "next_run_at": None,
        "logs": [],
    }

    result = asyncio.run(registration_routes.get_batch_status(batch_id))

    assert result["target_success"] == 10
    assert result["attempts"] == 14
    assert result["success"] == 6
    assert result["failed"] == 8
    assert result["progress"] == "6/10"


def test_get_active_registration_tasks_skips_auto_select_when_multiple_running():
    task_uuid = "task-running-1"
    batch_id = "batch-running-1"

    registration_routes.task_manager.update_status(
        task_uuid,
        "running",
        registration_mode="single",
        settings={"registration_mode": "single", "email_service_type": "tempmail"},
    )

    registration_routes.batch_tasks[batch_id] = {
        "total": 5,
        "target_success": 5,
        "attempts": 7,
        "completed": 2,
        "success": 2,
        "failed": 5,
        "current_index": 7,
        "cancelled": False,
        "finished": False,
        "status": "running",
        "registration_mode": "batch",
        "window_start": None,
        "window_end": None,
        "in_window": True,
        "next_window_seconds": 0,
        "running": 2,
        "next_run_at": None,
        "config_snapshot": {"registration_mode": "batch", "count": 5},
        "logs": [],
    }

    result = asyncio.run(registration_routes.get_active_registration_tasks())

    assert result["active"] is not None
    assert result["active"]["batch_id"] == batch_id
    assert result["active"]["mode"] == "batch"
    assert result["active_count"] == 2
    assert result["active_ambiguous"] is True
    assert result["batch_tasks"][0]["batch_id"] == batch_id


def test_run_batch_registration_dispatches_target_success_mode(monkeypatch):
    captured = {}

    async def fake_run_batch_target_success(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(registration_routes, "run_batch_target_success", fake_run_batch_target_success)

    asyncio.run(
        registration_routes.run_batch_registration(
            batch_id="batch-dispatch-1",
            task_uuids=[],
            email_service_type="tempmail",
            proxy=None,
            email_service_config=None,
            email_service_id=None,
            interval_min=0,
            interval_max=1,
            concurrency=3,
            mode="pipeline",
            registration_mode="batch_target_success",
            target_success_count=25,
        )
    )

    assert captured["batch_id"] == "batch-dispatch-1"
    assert captured["target_success_count"] == 25
    assert captured["concurrency"] == 3


def test_get_active_registration_tasks_prefers_live_batch_by_activity():
    registration_routes.batch_tasks["batch-stale"] = {
        "total": 10,
        "target_success": 10,
        "attempts": 30,
        "completed": 6,
        "success": 6,
        "failed": 24,
        "current_index": 30,
        "cancelled": False,
        "finished": False,
        "status": "waiting_window",
        "registration_mode": "loop",
        "window_start": "08:00",
        "window_end": "23:00",
        "in_window": False,
        "next_window_seconds": 120,
        "running": 0,
        "next_run_at": "2026-03-22T10:10:00",
        "created_at": "2026-03-22T09:00:00",
        "updated_at": "2026-03-22T10:00:00",
        "config_snapshot": {"registration_mode": "loop"},
        "logs": [],
    }

    registration_routes.batch_tasks["batch-live"] = {
        "total": 20,
        "target_success": 20,
        "attempts": 8,
        "completed": 3,
        "success": 3,
        "failed": 5,
        "current_index": 8,
        "cancelled": False,
        "finished": False,
        "status": "running",
        "registration_mode": "batch",
        "window_start": None,
        "window_end": None,
        "in_window": True,
        "next_window_seconds": 0,
        "running": 2,
        "next_run_at": "2026-03-22T10:02:00",
        "created_at": "2026-03-22T09:58:00",
        "updated_at": "2026-03-22T10:01:00",
        "config_snapshot": {"registration_mode": "batch", "count": 20},
        "logs": [],
    }

    result = asyncio.run(registration_routes.get_active_registration_tasks())

    assert result["active"] is not None
    assert result["active"]["batch_id"] == "batch-live"
    assert result["active_count"] == 2
    assert result["active_ambiguous"] is True
    assert result["batch_tasks"][0]["batch_id"] == "batch-live"


def test_get_active_registration_tasks_returns_active_when_single_candidate():
    batch_id = "batch-only-1"

    registration_routes.batch_tasks[batch_id] = {
        "total": 3,
        "target_success": 3,
        "attempts": 2,
        "completed": 1,
        "success": 1,
        "failed": 1,
        "current_index": 2,
        "cancelled": False,
        "finished": False,
        "status": "running",
        "registration_mode": "batch",
        "window_start": None,
        "window_end": None,
        "in_window": True,
        "next_window_seconds": 0,
        "running": 1,
        "next_run_at": None,
        "config_snapshot": {"registration_mode": "batch", "count": 3},
        "created_at": "2026-03-22T12:00:00",
        "updated_at": "2026-03-22T12:01:00",
        "logs": [],
    }

    result = asyncio.run(registration_routes.get_active_registration_tasks())

    assert result["active_count"] == 1
    assert result["active_ambiguous"] is False
    assert result["active"]["batch_id"] == batch_id
    assert result["active"]["mode"] == "batch"


def test_get_active_registration_tasks_ignores_worker_subtasks_from_same_batch():
    batch_id = "batch-restore-1"

    registration_routes.batch_tasks[batch_id] = {
        "total": 12,
        "target_success": 12,
        "attempts": 6,
        "completed": 2,
        "success": 2,
        "failed": 4,
        "current_index": 6,
        "cancelled": False,
        "finished": False,
        "status": "running",
        "registration_mode": "batch",
        "window_start": None,
        "window_end": None,
        "in_window": True,
        "next_window_seconds": 0,
        "running": 2,
        "next_run_at": None,
        "config_snapshot": {"registration_mode": "batch", "count": 12},
        "created_at": "2026-03-23T10:00:00",
        "updated_at": "2026-03-23T10:01:00",
        "logs": [],
    }

    registration_routes.task_manager.update_status(
        "worker-1",
        "running",
        batch_id=batch_id,
        settings={"registration_mode": "single"},
    )
    registration_routes.task_manager.update_status(
        "worker-2",
        "running",
        batch_id=batch_id,
        settings={"registration_mode": "single"},
    )

    result = asyncio.run(registration_routes.get_active_registration_tasks())

    assert result["active_count"] == 1
    assert result["active_ambiguous"] is False
    assert result["active"]["batch_id"] == batch_id
    assert result["active"]["mode"] == "batch"


def test_get_task_includes_runtime_settings_snapshot(monkeypatch):
    task_uuid = "task-settings-1"
    expected_settings = {
        "registration_mode": "single",
        "email_service_type": "tempmail",
        "auto_upload_cpa": True,
    }

    dummy_task = DummyTask(
        id=99,
        task_uuid=task_uuid,
        status="running",
    )

    @contextmanager
    def fake_get_db():
        yield object()

    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)
    monkeypatch.setattr(
        registration_routes.crud,
        "get_registration_task",
        lambda db, uuid: dummy_task if uuid == task_uuid else None,
    )

    registration_routes.task_manager.update_status(
        task_uuid,
        "running",
        settings=expected_settings,
    )

    result = asyncio.run(registration_routes.get_task(task_uuid))

    assert result.task_uuid == task_uuid
    assert result.settings == expected_settings
