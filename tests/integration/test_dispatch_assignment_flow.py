"""End-to-end evidence for a real child assignment."""
from __future__ import annotations

from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.store import load_task_index
from onemancompany.core.task_tree import TaskTree, get_tree, register_tree
from onemancompany.core.vessel import EmployeeManager, ScheduleEntry


@pytest.mark.asyncio
async def test_dispatch_persists_tree_index_schedules_and_taskboard(tmp_path):
    from onemancompany.agents.tree_tools import dispatch_child
    from onemancompany.api.routes import get_employee_taskboard
    from onemancompany.core.vessel import _current_task_id, _current_vessel

    employees_dir = tmp_path / "employees"
    employees_dir.mkdir()
    tree_path = tmp_path / "project" / "iterations" / "iter_001" / "task_tree.yaml"
    tree_path.parent.mkdir(parents=True)

    tree = TaskTree(project_id="proj1")
    root = tree.create_root(employee_id="00002", description="Root")
    tree.save(tree_path)
    register_tree(tree_path, tree)

    manager = EmployeeManager()
    launcher = MagicMock(spec=__import__("onemancompany.core.vessel", fromlist=["Launcher"]).Launcher)
    launcher.is_ready.return_value = False
    manager.register("00100", launcher)
    manager._schedule_next = MagicMock()
    manager._schedule["00002"] = [ScheduleEntry(node_id=root.id, tree_path=str(tree_path))]
    vessel = MagicMock()
    vessel.employee_id = "00002"
    vessel_task = _current_task_id.set(root.id)
    vessel_ctx = _current_vessel.set(vessel)

    try:
        with (
            patch("onemancompany.core.vessel.employee_manager", manager),
            patch("onemancompany.core.store.EMPLOYEES_DIR", employees_dir),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Dev"}),
        ):
            result = dispatch_child.invoke({
                "target_employee_id": "00100",
                "description": "Build the assigned feature",
                "title": "Assigned feature",
                "acceptance_criteria": ["Tests pass"],
            })

            assert result["status"] == "dispatched"
            node_id = result["node_id"]
            verification = result["verification"]
            assert verification == {
                "dispatch_child_called": True,
                "task_tree_node_created": True,
                "task_tree_persisted": True,
                "task_index_written": True,
                "schedule_node_called": True,
                "schedule_registered": True,
                "schedule_expected": True,
                "verified": True,
            }

            persisted = TaskTree.load(tree_path)
            child = persisted.get_node(node_id)
            assert child is not None
            assert child.employee_id == "00100"
            assert child.dispatch_verification["verified"] is True
            assert child.dispatch_verification["schedule_node_called"] is True
            assert child.dispatch_verification["schedule_registered"] is True
            assert child.dispatch_verification["receipt_id"]

            execution_log = tree_path.parent / "nodes" / root.id / "execution.log"
            assert execution_log.exists()
            log_text = execution_log.read_text(encoding="utf-8")
            assert '"type": "dispatch_verified"' in log_text
            assert node_id in log_text
            assert child.dispatch_verification["receipt_id"] in log_text

            index = load_task_index("00100")
            assert {entry["node_id"] for entry in index} == {node_id}

            board = await get_employee_taskboard("00100")
            assert {task["id"] for task in board["tasks"]} == {node_id}
            assert board["counts"] == {
                "active": 1,
                "completed": 0,
                "failed": 0,
                "total": 1,
            }
    finally:
        _current_vessel.reset(vessel_ctx)
        _current_task_id.reset(vessel_task)


@pytest.mark.asyncio
async def test_started_receipt_is_written_at_executor_boundary(tmp_path):
    """Scheduling alone is not `started`; the launcher boundary is."""
    from unittest.mock import AsyncMock

    from onemancompany.core.task_tree import TaskTree, register_tree
    from onemancompany.core.vessel import EmployeeManager, LaunchResult, Launcher, ScheduleEntry

    tree_path = tmp_path / "project" / "iterations" / "iter_001" / "task_tree.yaml"
    tree_path.parent.mkdir(parents=True)
    tree = TaskTree(project_id="proj1/iter_001")
    root = tree.create_root(employee_id="00002", description="Parent")
    child = tree.add_child(
        parent_id=root.id,
        employee_id="00100",
        description="Execute assigned work",
        acceptance_criteria=["Done"],
    )
    child.project_dir = str(tree_path.parent)
    child.project_id = "proj1/iter_001"
    child.dispatch_verification = {
        "dispatch_child_called": True,
        "task_tree_node_created": True,
        "task_tree_persisted": True,
        "task_index_written": True,
        "schedule_node_called": True,
        "schedule_registered": True,
        "schedule_expected": True,
        "verified": True,
        "receipt_id": "receipt-123",
    }
    tree.save(tree_path)
    register_tree(tree_path, tree)

    async def executor_probe(*_args, **_kwargs):
        persisted = TaskTree.load(tree_path)
        receipt = persisted.get_node(child.id).dispatch_verification
        assert receipt["started"] is True
        assert receipt["started_at"]
        assert receipt["started_by"] == "executor"
        return LaunchResult(output="done")

    manager = EmployeeManager()
    launcher = MagicMock(spec=Launcher)
    launcher.execute = AsyncMock(side_effect=executor_probe)
    launcher.is_ready.return_value = True
    manager.register("00100", launcher)
    manager._push_to_conversation = MagicMock()
    manager._set_employee_status = MagicMock()
    entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

    with (
        patch("onemancompany.core.vessel.company_state") as state,
        patch("onemancompany.core.vessel.event_bus") as bus,
        patch("onemancompany.core.vessel._store") as store,
        patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path / "employees"),
        patch("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees"),
        patch("onemancompany.core.vessel._load_progress", return_value=""),
        patch("onemancompany.core.vessel._append_progress"),
        patch("onemancompany.core.skill_hooks.run_hooks", new_callable=AsyncMock, return_value=[]),
        patch.object(manager, "_on_child_complete", new_callable=AsyncMock),
    ):
        state.employees = {"00100": MagicMock(status="idle")}
        state.active_tasks = []
        bus.publish = AsyncMock()
        store.save_employee_runtime = AsyncMock()
        store.load_employee.return_value = {"id": "00100"}
        await manager._execute_task("00100", entry)

    receipt = TaskTree.load(tree_path).get_node(child.id).dispatch_verification
    assert receipt["started"] is True
    assert receipt["receipt_id"] == "receipt-123"
    execution_log = tree_path.parent / "nodes" / child.id / "execution.log"
    assert '"type": "dispatch_started"' in execution_log.read_text(encoding="utf-8")
