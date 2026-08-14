from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.runtime_storage import RuntimeStorage, set_runtime_storage
from onemancompany.core.store import append_task_index_entry
from onemancompany.core.task_tree import TaskTree, register_tree
from onemancompany.core.vessel import EmployeeManager, Launcher, ScheduleEntry, _current_task_id, _current_vessel


async def _formal_dispatch_fixture(tmp_path):
    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    tree_path = tmp_path / "project" / "iterations" / "iter_010" / "task_tree.yaml"
    tree_path.parent.mkdir(parents=True)
    tree = TaskTree(project_id="project/iter_010", mode="standard", workflow_contract_version=2)
    parent = tree.create_root(employee_id="00003", description="COO parent")
    parent.implementation_path = "/Users/hanzhen/Documents/云测试的项目"
    parent.task_key = "phase1-parent"
    tree.save(tree_path)
    register_tree(tree_path, tree)

    manager = EmployeeManager()
    manager.register("00006", MagicMock(spec=Launcher))
    manager._schedule["00003"] = [ScheduleEntry(node_id=parent.id, tree_path=str(tree_path))]

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    return employees_dir, tree_path, tree, parent, manager, storage


@pytest.mark.asyncio
async def test_standard_v2_requires_task_key_before_creating_child(tmp_path):
    from onemancompany.agents.tree_tools import dispatch_child

    employees_dir, tree_path, tree, parent, manager, storage = await _formal_dispatch_fixture(tmp_path)
    vessel = MagicMock(employee_id="00003")
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(parent.id)
    try:
        with (
            patch("onemancompany.core.vessel.employee_manager", manager),
            patch("onemancompany.core.store.EMPLOYEES_DIR", employees_dir),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Backend"}),
        ):
            result = await asyncio.to_thread(dispatch_child.invoke, {
                "target_employee_id": "00006",
                "description": "Run smoke remediation",
                "acceptance_criteria": ["smoke passes"],
            })

        assert result["status"] == "error"
        assert result["error_type"] == "DispatchPersistenceError"
        assert tree.get_children(parent.id) == []
    finally:
        _current_vessel.reset(tok_v)
        _current_task_id.reset(tok_t)
        set_runtime_storage(None)
        await storage.close()


@pytest.mark.asyncio
async def test_standard_v2_replay_returns_original_child_and_changed_request_conflicts(tmp_path):
    from onemancompany.agents.tree_tools import dispatch_child

    employees_dir, tree_path, tree, parent, manager, storage = await _formal_dispatch_fixture(tmp_path)
    vessel = MagicMock(employee_id="00003")
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(parent.id)
    request = {
        "target_employee_id": "00006",
        "task_key": "phase1-smoke-backend",
        "description": "Run smoke remediation",
        "acceptance_criteria": ["smoke passes"],
        "timeout_seconds": 900,
    }
    try:
        with (
            patch("onemancompany.core.vessel.employee_manager", manager),
            patch("onemancompany.core.store.EMPLOYEES_DIR", employees_dir),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Backend"}),
        ):
            first = await asyncio.to_thread(dispatch_child.invoke, request)
            replay = await asyncio.to_thread(dispatch_child.invoke, request)
            changed = await asyncio.to_thread(dispatch_child.invoke, {**request, "description": "Different work"})

        assert first["status"] == "dispatched"
        assert replay["status"] == "already_dispatched"
        assert replay["node_id"] == first["node_id"]
        assert len(tree.get_children(parent.id)) == 1
        assert changed["status"] == "error"
        assert changed["error_type"] == "IdempotencyConflict"

        child = tree.get_node(first["node_id"])
        assert child.task_key == "phase1-smoke-backend"
        assert child.dispatch_request_fingerprint.startswith("sha256:")
        assert tree.dispatch_manifest["phase1-smoke-backend"] == {
            "employee_id": "00006",
            "task_key": "phase1-smoke-backend",
            "node_id": child.id,
            "request_fingerprint": child.dispatch_request_fingerprint,
        }
        intent = await storage.get_dispatch_intent(parent.id, "00006", "phase1-smoke-backend")
        assert intent["node_id"] == child.id
        assert intent["state"] == "scheduled"
    finally:
        _current_vessel.reset(tok_v)
        _current_task_id.reset(tok_t)
        set_runtime_storage(None)
        await storage.close()
