from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from onemancompany.core.runtime_context import get_task_runtime_context
from onemancompany.core.runtime_storage import RuntimeStorage, set_runtime_storage
from onemancompany.core.task_tree import TaskTree, evict_tree
from onemancompany.core.vessel import EmployeeManager, ScheduleEntry


def _formal_entry(tmp_path, *, project_id: str = "project-a/iter_001"):
    tree = TaskTree(project_id=project_id, mode="standard", workflow_contract_version=2)
    node = tree.create_root(employee_id="00006", description="formal work")
    node.task_key = "formal-work"
    path = tmp_path / "task_tree.yaml"
    tree.save(path)
    evict_tree(path)
    return ScheduleEntry(node_id=node.id, tree_path=str(path)), path, node.id


@pytest.mark.asyncio
async def test_standard_v2_refuses_to_start_without_runtime_storage(tmp_path):
    entry, path, node_id = _formal_entry(tmp_path)
    manager = EmployeeManager()
    manager._execute_task_body = AsyncMock()
    set_runtime_storage(None)

    await manager._execute_task("00006", entry)

    loaded = TaskTree.load(path, skeleton_only=False)
    node = loaded.get_node(node_id)
    assert node is not None
    assert node.status == "holding"
    assert node.hold_reason == "runtime_storage_unavailable"
    assert node.checkpoint_status == "unavailable"
    assert node.completed_at == ""
    assert node.next_retry_at
    assert node.execution_checkpoint["phase"] == "awaiting_runtime_storage"
    manager._execute_task_body.assert_not_awaited()


@pytest.mark.asyncio
async def test_standard_v2_acquires_lease_and_sets_checkpoint_context(tmp_path):
    entry, path, node_id = _formal_entry(tmp_path)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    manager = EmployeeManager()
    observed = {}

    async def body(employee_id, body_entry, **runtime):
        observed.update(get_task_runtime_context())
        observed["lease"] = runtime["execution_lease"]
        observed["fence_valid"] = await runtime["runtime_storage"].validate_fencing_token(
            node_id, 1, runtime["execution_lease"].fencing_token
        )

    manager._execute_task_body = body
    try:
        await manager._execute_task("00006", entry)
        loaded = TaskTree.load(path, skeleton_only=False)
        node = loaded.get_node(node_id)
        assert node is not None
        assert node.checkpoint_thread_id == f"omc:project-a:iter_001:{node_id}:g1"
        assert node.checkpoint_status == "active"
        assert observed["node_id"] == node_id
        assert observed["employee_id"] == "00006"
        assert observed["project_id"] == "project-a"
        assert observed["iteration_id"] == "iter_001"
        assert observed["fence_valid"] is True
        assert get_task_runtime_context() == {}
        assert await storage.fetchone(
            "SELECT 1 FROM execution_leases WHERE node_id=? AND execution_generation=1",
            (node_id,),
        ) is None
    finally:
        set_runtime_storage(None)
        await storage.close()


@pytest.mark.asyncio
async def test_standard_v2_does_not_execute_without_lease(tmp_path):
    entry, path, node_id = _formal_entry(tmp_path)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    other = await storage.acquire_lease(node_id, 1, "other-worker", ttl_seconds=60)
    assert other is not None
    manager = EmployeeManager()
    manager._execute_task_body = AsyncMock()
    try:
        await manager._execute_task("00006", entry)
        loaded = TaskTree.load(path, skeleton_only=False)
        node = loaded.get_node(node_id)
        assert node is not None
        assert node.status == "holding"
        assert node.hold_reason == "execution_lease_unavailable"
        manager._execute_task_body.assert_not_awaited()
    finally:
        await storage.release_lease(other)
        set_runtime_storage(None)
        await storage.close()

@pytest.mark.asyncio
async def test_standard_v2_provider_transient_error_holds_without_whole_agent_retry(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    from onemancompany.core.vessel import LaunchResult, Launcher

    entry, path, node_id = _formal_entry(tmp_path)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    manager = EmployeeManager()
    launcher = MagicMock(spec=Launcher)
    launcher.execute = AsyncMock(side_effect=RuntimeError("provider concurrency limit"))
    manager.register("00006", launcher)
    try:
        with patch("onemancompany.core.vessel._store") as store, \
             patch("onemancompany.core.vessel.event_bus") as bus, \
             patch("onemancompany.core.vessel._load_progress", return_value=""), \
             patch("onemancompany.core.vessel._append_progress"):
            store.save_employee_runtime = AsyncMock()
            store.load_employee.return_value = {"id": "00006", "role": "Backend"}
            bus.publish = AsyncMock()
            await manager._execute_task("00006", entry)

        loaded = TaskTree.load(path, skeleton_only=False)
        node = loaded.get_node(node_id)
        assert node is not None
        assert node.status == "holding"
        assert node.hold_reason == "provider_transient_error"
        assert launcher.execute.await_count == 1
    finally:
        set_runtime_storage(None)
        await storage.close()


@pytest.mark.asyncio
async def test_standard_v2_provider_configuration_error_blocks_without_retry(tmp_path):
    from unittest.mock import MagicMock, patch
    from onemancompany.core.vessel import Launcher

    entry, path, node_id = _formal_entry(tmp_path)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    manager = EmployeeManager()
    launcher = MagicMock(spec=Launcher)
    launcher.execute = AsyncMock(side_effect=RuntimeError("invalid API key"))
    manager.register("00006", launcher)
    try:
        with patch("onemancompany.core.vessel._store") as store, \
             patch("onemancompany.core.vessel.event_bus") as bus, \
             patch("onemancompany.core.vessel._load_progress", return_value=""), \
             patch("onemancompany.core.vessel._append_progress"):
            store.save_employee_runtime = AsyncMock()
            store.load_employee.return_value = {"id": "00006", "role": "Backend"}
            bus.publish = AsyncMock()
            await manager._execute_task("00006", entry)

        loaded = TaskTree.load(path, skeleton_only=False)
        node = loaded.get_node(node_id)
        assert node is not None
        assert node.status == "blocked"
        assert node.hold_reason == "provider_configuration_blocked"
        assert launcher.execute.await_count == 1
    finally:
        set_runtime_storage(None)
        await storage.close()


@pytest.mark.asyncio
async def test_standard_v2_verified_dispatch_does_not_reach_executor_when_started_intent_is_missing(tmp_path):
    """A YAML receipt cannot claim started when its SQLite dispatch intent is absent."""
    from unittest.mock import MagicMock, patch
    from onemancompany.core.vessel import Launcher

    entry, path, node_id = _formal_entry(tmp_path)
    tree = TaskTree.load(path, skeleton_only=False)
    node = tree.get_node(node_id)
    node.dispatch_verification = {
        "verified": True,
        "receipt_id": "receipt-missing-intent",
        "schedule_node_called": True,
        "schedule_registered": True,
    }
    tree.save(path)
    evict_tree(path)

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    manager = EmployeeManager()
    launcher = MagicMock(spec=Launcher)
    launcher.execute = AsyncMock()
    manager.register("00006", launcher)
    try:
        with patch("onemancompany.core.vessel._store") as store, \
             patch("onemancompany.core.vessel.event_bus") as bus, \
             patch("onemancompany.core.vessel._load_progress", return_value=""), \
             patch("onemancompany.core.vessel._append_progress"):
            store.save_employee_runtime = AsyncMock()
            store.load_employee.return_value = {"id": "00006", "role": "Backend"}
            bus.publish = AsyncMock()
            await manager._execute_task("00006", entry)

        loaded = TaskTree.load(path, skeleton_only=False)
        persisted = loaded.get_node(node_id)
        assert persisted.status == "holding"
        assert persisted.hold_reason == "dispatch_reconciliation_required"
        assert persisted.dispatch_verification.get("started") is not True
        launcher.execute.assert_not_awaited()
    finally:
        set_runtime_storage(None)
        await storage.close()

@pytest.mark.asyncio
async def test_runtime_storage_hold_becomes_runnable_after_retry_time(tmp_path):
    from datetime import datetime, timedelta

    entry, path, node_id = _formal_entry(tmp_path)
    tree = TaskTree.load(path, skeleton_only=False)
    node = tree.get_node(node_id)
    node.set_status(__import__("onemancompany.core.task_lifecycle", fromlist=["TaskPhase"]).TaskPhase.HOLDING)
    node.hold_reason = "runtime_storage_unavailable"
    node.next_retry_at = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
    tree.save(path)
    evict_tree(path)

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    manager = EmployeeManager()
    manager._schedule["00006"] = [entry]
    try:
        assert manager.get_next_scheduled("00006") == entry
    finally:
        set_runtime_storage(None)
        await storage.close()


def test_runtime_storage_hold_never_times_out_as_business_failure(tmp_path):
    from datetime import datetime, timedelta
    from onemancompany.core.task_lifecycle import TaskPhase

    entry, path, node_id = _formal_entry(tmp_path)
    tree = TaskTree.load(path, skeleton_only=False)
    node = tree.get_node(node_id)
    node.set_status(TaskPhase.HOLDING)
    node.hold_reason = "runtime_storage_unavailable"
    node.hold_started_at = (datetime.now() - timedelta(days=2)).isoformat()
    tree.save(path)
    evict_tree(path)

    manager = EmployeeManager()
    assert manager._check_holding_timeout(str(path), node_id) is False
    persisted = TaskTree.load(path, skeleton_only=False).get_node(node_id)
    assert persisted.status == "holding"


def test_restart_recovery_holds_processing_standard_v2_for_checkpoint_reconciliation(tmp_path):
    from onemancompany.core.task_lifecycle import TaskPhase
    from onemancompany.core.task_persistence import recover_schedule_from_trees

    projects = tmp_path / "projects"
    iteration = projects / "project-a" / "iterations" / "iter_001"
    iteration.mkdir(parents=True)
    path = iteration / "task_tree.yaml"
    tree = TaskTree(project_id="project-a/iter_001", mode="standard", workflow_contract_version=2)
    node = tree.create_root(employee_id="00006", description="formal work")
    node.task_key = "formal-work"
    node.set_status(TaskPhase.PROCESSING)
    tree.save(path)
    evict_tree(path)

    manager = EmployeeManager()
    manager.executors["00006"] = AsyncMock()
    recover_schedule_from_trees(manager, projects, tmp_path / "employees")

    persisted = TaskTree.load(path, skeleton_only=False).get_node(node.id)
    assert persisted.status == "holding"
    assert persisted.hold_reason == "checkpoint_reconciliation_required"
    assert persisted.execution_checkpoint["phase"] == "awaiting_checkpoint_reconciliation"
