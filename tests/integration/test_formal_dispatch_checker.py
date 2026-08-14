from __future__ import annotations

from pathlib import Path

import yaml

from scripts.check_formal_dispatch_18b1e9d4a1fc import verify_formal_dispatch

PROJECT_ID = "18b1e9d4a1fc"
IMPLEMENTATION_PATH = "/Users/hanzhen/Documents/云测试的项目"
RECEIPT = {
    "dispatch_child_called": True,
    "task_tree_node_created": True,
    "task_tree_persisted": True,
    "task_index_written": True,
    "schedule_node_called": True,
    "schedule_registered": True,
    "verified": True,
    "started": True,
    "started_at": "2026-08-12T12:00:00+08:00",
    "started_by": "executor",
}


def _write_yaml(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _build_project(tmp_path: Path, employee_ids=("00006", "00007", "00008", "00009")):
    iteration = "iter_v2"
    project_dir = tmp_path / "projects" / PROJECT_ID
    _write_yaml(project_dir / "project.yaml", {"project_id": PROJECT_ID, "iterations": [iteration]})
    parent_id = "abcdef123456"
    node_ids = {employee: f"{index + 6}0000000000{index + 6}"[-12:] for index, employee in enumerate(employee_ids)}
    # Use deterministic valid ids independent of employee count.
    node_ids = {employee: f"{int(employee):012x}"[-12:] for employee in employee_ids}
    manifest = {}
    children = []
    for employee in employee_ids:
        key = f"task-{employee}"
        fingerprint = f"sha256:{employee}"
        node_id = node_ids[employee]
        manifest[key] = {"employee_id": employee, "task_key": key, "node_id": node_id, "request_fingerprint": fingerprint}
        children.append({
            "id": node_id, "parent_id": parent_id, "children_ids": [], "employee_id": employee,
            "status": "accepted", "project_id": f"{PROJECT_ID}/{iteration}",
            "implementation_path": IMPLEMENTATION_PATH, "task_key": key,
            "dispatch_request_fingerprint": fingerprint, "dispatch_verification": dict(RECEIPT),
            "acceptance_audit": {"decision": "accepted", "decided_by": "00004", "decided_via": "accept_child",
                                 "decided_at": "2026-08-12T13:00:00+08:00", "notes": "verified"},
        })
    parent = {
        "id": parent_id, "parent_id": "111111111111", "children_ids": list(node_ids.values()),
        "employee_id": "00004", "status": "finished", "project_id": f"{PROJECT_ID}/{iteration}",
        "implementation_path": IMPLEMENTATION_PATH,
    }
    tree = {"project_id": f"{PROJECT_ID}/{iteration}", "root_id": "111111111111", "mode": "standard",
            "workflow_contract_version": 2, "dispatch_manifest": manifest, "nodes": [parent, *children]}
    tree_path = project_dir / "iterations" / iteration / "task_tree.yaml"
    _write_yaml(tree_path, tree)
    employees_dir = tmp_path / "employees"
    for employee, node_id in node_ids.items():
        _write_yaml(employees_dir / employee / "task_index.yaml", [{"node_id": node_id, "tree_path": str(tree_path.resolve())}])
    api = {f"/api/projects/{PROJECT_ID}/{iteration}/tree": {"nodes": tree["nodes"]}}
    for employee, node_id in node_ids.items():
        api[f"/api/employee/{employee}/taskboard"] = {"tasks": [{"id": node_id}]}
    return project_dir, employees_dir, tree_path, tree, api


def _verify(project_dir, employees_dir, api, employees=None):
    return verify_formal_dispatch(
        project_dir=project_dir, target_iteration="iter_v2", expected_employees=employees,
        expected_implementation_path=IMPLEMENTATION_PATH, employees_dir=employees_dir,
        api_getter=api.__getitem__, project_id=PROJECT_ID,
    )


def test_checker_accepts_dynamic_v2_manifest_and_three_gates(tmp_path):
    employees = ("00006", "00007", "00008", "00009", "00010")
    project_dir, employees_dir, _, _, api = _build_project(tmp_path, employees)
    report = _verify(project_dir, employees_dir, api, employees)
    assert report["ok"] is True
    assert report["parent_gate"]["ok"] is True
    assert report["dispatch_gate"]["ok"] is True
    assert report["closure_gate"]["ok"] is True
    assert len(report["receipts"]) == 5


def test_parent_gate_runs_when_manifest_is_empty(tmp_path):
    project_dir, employees_dir, tree_path, tree, api = _build_project(tmp_path, ())
    tree["dispatch_manifest"] = {}
    _write_yaml(tree_path, tree)
    api[f"/api/projects/{PROJECT_ID}/iter_v2/tree"] = {"nodes": tree["nodes"]}
    report = _verify(project_dir, employees_dir, api)
    assert report["ok"] is False
    assert report["parent_gate"]["ok"] is False
    assert any("formal parent" in error for error in report["parent_gate"]["errors"])
    assert "dispatch_gate" in report and "closure_gate" in report


def test_parent_gate_rejects_unknown_and_pending_status(tmp_path):
    for invalid in ("", "running", "pending"):
        project_dir, employees_dir, tree_path, tree, api = _build_project(tmp_path / (invalid or "empty"))
        tree["nodes"][0]["status"] = invalid
        _write_yaml(tree_path, tree)
        api[f"/api/projects/{PROJECT_ID}/iter_v2/tree"] = {"nodes": tree["nodes"]}
        report = _verify(project_dir, employees_dir, api, ("00006", "00007", "00008", "00009"))
        assert report["parent_gate"]["ok"] is False


def test_dispatch_gate_requires_executor_and_timezone_aware_started_at(tmp_path):
    project_dir, employees_dir, tree_path, tree, api = _build_project(tmp_path)
    child = tree["nodes"][1]
    child["dispatch_verification"]["started_by"] = "model"
    child["dispatch_verification"]["started_at"] = "2026-08-12T12:00:00"
    _write_yaml(tree_path, tree)
    api[f"/api/projects/{PROJECT_ID}/iter_v2/tree"] = {"nodes": tree["nodes"]}
    report = _verify(project_dir, employees_dir, api, ("00006", "00007", "00008", "00009"))
    assert report["dispatch_gate"]["ok"] is False
    assert any("started_by" in error for error in report["dispatch_gate"]["errors"])
    assert any("timezone-aware" in error for error in report["dispatch_gate"]["errors"])


def test_closure_gate_requires_explicit_non_auto_acceptance(tmp_path):
    project_dir, employees_dir, tree_path, tree, api = _build_project(tmp_path)
    child = tree["nodes"][1]
    child["status"] = "completed"
    child["acceptance_audit"] = {"decision": "accepted", "decided_via": "system", "decided_at": "2026-08-12T13:00:00+08:00", "notes": "Auto-accepted"}
    _write_yaml(tree_path, tree)
    api[f"/api/projects/{PROJECT_ID}/iter_v2/tree"] = {"nodes": tree["nodes"]}
    report = _verify(project_dir, employees_dir, api, ("00006", "00007", "00008", "00009"))
    assert report["dispatch_gate"]["ok"] is True
    assert report["closure_gate"]["ok"] is False
    assert any("Auto-accepted" in error for error in report["closure_gate"]["errors"])
