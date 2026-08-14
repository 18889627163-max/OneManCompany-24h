from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree, register_tree, _cache
from onemancompany.core.vessel import EmployeeManager, ScheduleEntry


def _manager() -> EmployeeManager:
    manager = EmployeeManager.__new__(EmployeeManager)
    manager._schedule = {}
    manager.executors = {"00003": MagicMock()}
    manager._running_tasks = {}
    manager._hooks = {}
    manager._deferred_schedule = set()
    manager._event_loop = None
    manager._employees = {}
    manager._completion_queue = None
    manager._completion_consumer = None
    manager._pending_ceo_reports = {}
    manager._restart_pending = False
    manager._current_entries = {}
    manager.schedule_node = MagicMock()
    manager._schedule_next = MagicMock()
    manager._publish_node_update = MagicMock()
    return manager


def _tree(tmp_path: Path, omission_count: int = 0):
    tree_path = tmp_path / "project" / "iterations" / "iter_010" / "task_tree.yaml"
    tree_path.parent.mkdir(parents=True)
    tree = TaskTree(project_id="project/iter_010", mode="standard", workflow_contract_version=2)
    parent = tree.create_root(employee_id="00003", description="COO parent")
    parent.task_key = "phase1-parent"
    parent.set_status(TaskPhase.PROCESSING)
    parent.set_status(TaskPhase.HOLDING)
    parent.project_dir = str(tree_path.parent)

    child = tree.add_child(parent.id, "00006", "backend remediation", ["tests pass"])
    child.task_key = "phase1-smoke-backend"
    child.set_status(TaskPhase.PROCESSING)
    child.set_status(TaskPhase.COMPLETED)
    child.review_omission_count = omission_count
    child.project_dir = str(tree_path.parent)

    review = tree.add_child(parent.id, "00003", "review child", [])
    review.node_type = NodeType.REVIEW
    review.task_key = f"review-phase1-smoke-backend-{omission_count + 1}"
    review.set_status(TaskPhase.PROCESSING)
    review.set_status(TaskPhase.COMPLETED)
    review.set_status(TaskPhase.ACCEPTED)
    review.set_status(TaskPhase.FINISHED)
    review.project_dir = str(tree_path.parent)

    tree.save(tree_path)
    register_tree(tree_path, tree)
    return tree_path, tree, parent, child, review


@pytest.mark.asyncio
async def test_first_valid_review_omission_keeps_child_completed_and_creates_corrective_review(tmp_path):
    tree_path, tree, parent, child, review = _tree(tmp_path)
    manager = _manager()
    entry = ScheduleEntry(node_id=review.id, tree_path=str(tree_path))

    with (
        patch("onemancompany.core.vessel._store") as store,
        patch("onemancompany.core.task_tree.save_tree_async", side_effect=lambda path: tree.save(Path(path))),
    ):
        store.save_project_status = AsyncMock()
        store.save_employee_runtime = AsyncMock()
        await manager._on_child_complete_inner("00003", entry, project_id=tree.project_id)

    assert child.status == TaskPhase.COMPLETED.value
    assert child.acceptance_audit is None
    assert child.review_omission_count == 1
    corrective = [
        node for node in tree.get_children(parent.id)
        if node.node_type == NodeType.REVIEW and node.id != review.id
    ]
    assert len(corrective) == 1
    assert corrective[0].event_key == f"review-omission:{parent.id}:1"
    assert "failed to call accept_child() or reject_child()" in corrective[0].description
    assert manager.schedule_node.call_count == 1
    _cache.clear()


@pytest.mark.asyncio
async def test_second_valid_review_omission_creates_one_manual_escalation_and_holds_parent(tmp_path):
    tree_path, tree, parent, child, review = _tree(tmp_path, omission_count=1)
    manager = _manager()
    entry = ScheduleEntry(node_id=review.id, tree_path=str(tree_path))

    with (
        patch("onemancompany.core.vessel._store") as store,
        patch("onemancompany.core.task_tree.save_tree_async", side_effect=lambda path: tree.save(Path(path))),
    ):
        store.save_project_status = AsyncMock()
        store.save_employee_runtime = AsyncMock()
        await manager._on_child_complete_inner("00003", entry, project_id=tree.project_id)
        await manager._on_child_complete_inner("00003", entry, project_id=tree.project_id)

    assert child.status == TaskPhase.COMPLETED.value
    assert child.review_omission_count == 2
    assert parent.status == TaskPhase.HOLDING.value
    assert parent.hold_reason == "awaiting_manual_review"
    escalations = [
        node for node in tree.get_children(parent.id)
        if node.event_key == f"manual-review-escalation:{parent.id}"
    ]
    assert len(escalations) == 1
    assert escalations[0].node_type == NodeType.CEO_REQUEST
    corrective = [
        node for node in tree.get_children(parent.id)
        if node.event_key.startswith("review-omission:")
    ]
    assert corrective == []
    assert manager.schedule_node.call_count == 1
    _cache.clear()


@pytest.mark.asyncio
async def test_standard_v2_parent_is_completed_not_auto_accepted_when_children_explicitly_accepted(tmp_path):
    tree_path, tree, parent, child, review = _tree(tmp_path)
    child.set_status(TaskPhase.ACCEPTED)
    child.acceptance_audit = {
        "decision": "accepted",
        "decided_by": "00003",
        "decided_via": "accept_child",
        "review_node_id": review.id,
        "decided_at": "2026-08-12T16:00:00+08:00",
        "criteria_results": [],
        "evidence_refs": [],
        "notes": "ok",
    }
    tree.save(tree_path)
    manager = _manager()
    entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

    with (
        patch("onemancompany.core.vessel._store") as store,
        patch("onemancompany.core.task_tree.save_tree_async", side_effect=lambda path: tree.save(Path(path))),
    ):
        store.save_project_status = AsyncMock()
        store.save_employee_runtime = AsyncMock()
        await manager._on_child_complete_inner("00006", entry, project_id=tree.project_id)

    assert parent.status == TaskPhase.COMPLETED.value
    assert parent.acceptance_result is None
    assert parent.acceptance_audit is None
    _cache.clear()
