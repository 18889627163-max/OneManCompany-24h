from __future__ import annotations

from unittest.mock import MagicMock, patch

from onemancompany.core.task_tree import TaskTree, register_tree
from onemancompany.core.vessel import _current_task_id, _current_vessel


def _formal_review_tree(tmp_path):
    tree_path = tmp_path / "project" / "iterations" / "iter_010" / "task_tree.yaml"
    tree_path.parent.mkdir(parents=True)
    tree = TaskTree(project_id="project/iter_010", mode="standard", workflow_contract_version=2)
    parent = tree.create_root(employee_id="00003", description="COO parent")
    parent.task_key = "phase1-parent"
    child = tree.add_child(parent.id, "00006", "backend remediation", ["tests pass"])
    child.task_key = "phase1-smoke-backend"
    child.status = "completed"
    review = tree.add_child(parent.id, "00003", "review child", [])
    review.node_type = "review"
    review.task_key = "review-phase1-smoke-backend-1"
    tree.save(tree_path)
    register_tree(tree_path, tree)
    return tree_path, tree, parent, child, review


def test_standard_v2_accept_child_persists_explicit_audit_atomically(tmp_path):
    from onemancompany.agents.tree_tools import accept_child

    tree_path, tree, parent, child, review = _formal_review_tree(tmp_path)
    vessel = MagicMock(employee_id="00003")
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(review.id)
    try:
        with (
            patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=(str(tree_path.parent), str(tree_path))),
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.core.vessel._trigger_dep_resolution"),
        ):
            result = accept_child.invoke({
                "node_id": child.id,
                "notes": "verified smoke evidence",
                "criteria_results": [{"criterion": "tests pass", "passed": True}],
                "evidence_refs": ["nodes/evidence/smoke.log"],
            })

        persisted = TaskTree.load(tree_path)
        accepted = persisted.get_node(child.id)
        assert result["status"] == "accepted"
        assert accepted.status == "accepted"
        assert accepted.acceptance_audit["decision"] == "accepted"
        assert accepted.acceptance_audit["decided_by"] == "00003"
        assert accepted.acceptance_audit["decided_via"] == "accept_child"
        assert accepted.acceptance_audit["review_node_id"] == review.id
        assert accepted.acceptance_audit["criteria_results"] == [{"criterion": "tests pass", "passed": True}]
        assert accepted.acceptance_audit["evidence_refs"] == ["nodes/evidence/smoke.log"]
        assert accepted.acceptance_audit["decided_at"].endswith("+00:00") or "+" in accepted.acceptance_audit["decided_at"]
        assert "Auto-accepted" not in accepted.acceptance_audit["notes"]
    finally:
        _current_vessel.reset(tok_v)
        _current_task_id.reset(tok_t)


def test_standard_v2_reject_child_persists_decision_before_retry(tmp_path):
    from onemancompany.agents.tree_tools import reject_child

    tree_path, tree, parent, child, review = _formal_review_tree(tmp_path)
    vessel = MagicMock(employee_id="00003")
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(review.id)
    manager = MagicMock()
    manager.executors = {"00006": MagicMock()}
    try:
        with (
            patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=(str(tree_path.parent), str(tree_path))),
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.core.vessel.employee_manager", manager),
        ):
            result = reject_child.invoke({
                "node_id": child.id,
                "reason": "missing exit code",
                "retry": True,
                "criteria_results": [{"criterion": "tests pass", "passed": False}],
                "evidence_refs": ["nodes/evidence/review.txt"],
            })

        persisted = TaskTree.load(tree_path)
        rejected = persisted.get_node(child.id)
        assert result["status"] == "rejected_retry"
        assert rejected.status == "pending"
        assert rejected.acceptance_audit["decision"] == "rejected"
        assert rejected.acceptance_audit["decided_via"] == "reject_child"
        assert rejected.acceptance_audit["review_node_id"] == review.id
        assert rejected.acceptance_audit["notes"] == "missing exit code"
    finally:
        _current_vessel.reset(tok_v)
        _current_task_id.reset(tok_t)
