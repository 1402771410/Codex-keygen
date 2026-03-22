import asyncio
from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.web.routes import registration as registration_routes


@pytest.fixture(autouse=True)
def clear_batch_tasks_state():
    registration_routes.batch_tasks.clear()
    yield
    registration_routes.batch_tasks.clear()


def test_get_loop_window_state_inside_window():
    in_window, wait_seconds = registration_routes._get_loop_window_state(
        "09:00",
        "18:00",
        datetime(2026, 3, 22, 10, 0, 0),
    )

    assert in_window is True
    assert wait_seconds == 0


def test_get_loop_window_state_cross_midnight_waits_next_start():
    in_window, wait_seconds = registration_routes._get_loop_window_state(
        "22:00",
        "02:00",
        datetime(2026, 3, 22, 3, 0, 0),
    )

    assert in_window is False
    assert wait_seconds == 19 * 3600


def test_start_batch_registration_loop_requires_window():
    request = registration_routes.BatchRegistrationRequest(
        registration_mode="loop",
        email_service_type="tempmail",
        interval_min=0,
        interval_max=1,
        concurrency=1,
        mode="pipeline",
    )

    background_tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(registration_routes.start_batch_registration(request, background_tasks))

    assert exc.value.status_code == 400
    assert "时间段" in str(exc.value.detail)


def test_start_batch_registration_loop_initializes_state_and_response():
    request = registration_routes.BatchRegistrationRequest(
        registration_mode="loop",
        email_service_type="tempmail",
        interval_min=0,
        interval_max=1,
        concurrency=1,
        mode="pipeline",
        window_start="8:05",
        window_end="23:00",
    )

    background_tasks = BackgroundTasks()
    response = asyncio.run(registration_routes.start_batch_registration(request, background_tasks))

    assert response.registration_mode == "loop"
    assert response.count == 0
    assert response.tasks == []
    assert response.window_start == "08:05"
    assert response.window_end == "23:00"
    assert len(background_tasks.tasks) == 1

    state = registration_routes.batch_tasks[response.batch_id]
    assert state["registration_mode"] == "loop"
    assert state["window_start"] == "08:05"
    assert state["window_end"] == "23:00"
    assert state["total"] == 0
    assert state["status"] == "running"

    status_response = asyncio.run(registration_routes.get_batch_status(response.batch_id))
    assert status_response["registration_mode"] == "loop"
    assert status_response["window_start"] == "08:05"
    assert status_response["window_end"] == "23:00"
