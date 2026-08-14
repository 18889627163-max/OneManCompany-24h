#!/usr/bin/env python3
"""Verify the independent Parent, Dispatch, and Closure gates for an iteration.

The checker is intentionally iteration-scoped and fail-closed.  A v2 result is
successful only when all three gates pass.  Historical prose, legacy automatic
acceptance, and an in-memory dispatch return value are not formal evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from onemancompany.core.task_lifecycle import (
    CLOSURE_COMPLETE,
    PARENT_VALID,
    parse_task_phase,
)

PROJECT_ID = "18b1e9d4a1fc"
EMPLOYEES = ("00006", "00007", "00008", "00009")
REJECTED_IDS = {PROJECT_ID, "0515ed131b56", "4ff2c0e8b16b", "c100484fde61"}
NODE_ID_RE = re.compile(r"^[0-9a-f]{12}$")
BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
PROJECT_DIR = ROOT / ".onemancompany/company/business/projects" / PROJECT_ID
EMPLOYEES_DIR = ROOT / ".onemancompany/company/human_resource/employees"
RECEIPT_KEYS = (
    "dispatch_child_called",
    "task_tree_node_created",
    "task_tree_persisted",
    "task_index_written",
    "schedule_node_called",
    "schedule_registered",
    "verified",
    "started",
)


def get_json(path: str, *, base_url: str = BASE_URL) -> dict:
    with urllib.request.urlopen(base_url + path, timeout=3) as response:
        value = json.load(response)
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return [row for row in values if isinstance(row, dict)]


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _node_id(row: dict) -> str:
    return str(row.get("id") or row.get("node_id") or "")


def _gate(errors: list[str], **extra: Any) -> dict:
    return {"ok": not errors, "errors": errors, **extra}


def _aware_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _resolved_path(value: Any) -> str:
    return str(Path(str(value)).expanduser().resolve()) if value else ""


def verify_formal_dispatch(
    *,
    project_dir: Path,
    target_iteration: str,
    expected_implementation_path: str,
    expected_employees: Iterable[str] | None = None,
    employees_dir: Path = EMPLOYEES_DIR,
    rejected_ids: set[str] | None = None,
    api_getter: Callable[[str], dict] = get_json,
    project_id: str = PROJECT_ID,
) -> dict:
    """Return an independent three-gate verdict for one target iteration."""
    common_errors: list[str] = []
    rejected = REJECTED_IDS | set(rejected_ids or ())
    requested_employees = tuple(str(value) for value in (expected_employees or ()))
    project_dir = Path(project_dir).resolve()
    employees_dir = Path(employees_dir).resolve()
    expected_path = _resolved_path(expected_implementation_path)
    expected_project_id = f"{project_id}/{target_iteration}"

    project_file = project_dir / "project.yaml"
    if not project_file.exists():
        common_errors.append(f"project file missing: {project_file}")
        return _empty_report(project_id, target_iteration, common_errors)
    project_doc = _load_yaml(project_file) or {}
    if target_iteration not in [str(v) for v in (project_doc.get("iterations") or [])]:
        common_errors.append(f"target iteration is not registered: {target_iteration}")

    tree_path = project_dir / "iterations" / target_iteration / "task_tree.yaml"
    if not tree_path.exists():
        common_errors.append(f"target tree missing: {tree_path}")
        return _empty_report(project_id, target_iteration, common_errors)

    tree_doc = _load_yaml(tree_path) or {}
    contract_version = int(tree_doc.get("workflow_contract_version", 1))
    nodes = _rows(tree_doc.get("nodes"))
    nodes_by_id = {_node_id(node): node for node in nodes if _node_id(node)}
    manifest = tree_doc.get("dispatch_manifest") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    # Legacy checker compatibility is deliberately read-only.  Strict formal
    # completion requires v2, but this fallback lets diagnostics explain old data.
    if contract_version >= 2:
        entries = [(str(task_key), value) for task_key, value in manifest.items() if isinstance(value, dict)]
    else:
        employees = requested_employees or EMPLOYEES
        entries = []
        for employee_id in employees:
            matching = [node for node in nodes if str(node.get("employee_id", "")) == employee_id]
            node = matching[0] if len(matching) == 1 else {}
            entries.append((str(node.get("task_key") or f"legacy:{employee_id}"), {
                "employee_id": employee_id,
                "node_id": _node_id(node),
                "request_fingerprint": node.get("dispatch_request_fingerprint", ""),
                "_legacy_count": len(matching),
            }))

    # Parent discovery does not inspect receipts.  It therefore runs even when
    # dispatch evidence is absent or malformed.
    candidate_parent_ids = {
        str(nodes_by_id.get(str(entry.get("node_id", "")), {}).get("parent_id", ""))
        for _, entry in entries
        if str(entry.get("node_id", "")) in nodes_by_id
    }
    candidate_parent_ids.discard("")
    parent_id = next(iter(candidate_parent_ids)) if len(candidate_parent_ids) == 1 else ""
    parent = nodes_by_id.get(parent_id)

    parent_errors = list(common_errors)
    if contract_version < 2:
        parent_errors.append("workflow_contract_version must be >= 2 for a formal v2 verdict")
    if not entries:
        parent_errors.append("dispatch_manifest is empty; formal parent cannot be determined")
    if len(candidate_parent_ids) != 1:
        parent_errors.append(f"formal children do not identify one direct parent: {sorted(candidate_parent_ids)}")
    if not parent_id:
        parent_errors.append("formal parent is missing")
    elif parent_id in rejected or parent_id.startswith("task_") or not NODE_ID_RE.fullmatch(parent_id):
        parent_errors.append(f"formal parent is rejected or invalid: {parent_id}")
    elif parent is None:
        parent_errors.append(f"formal parent does not exist in target tree: {parent_id}")
    else:
        try:
            parent_phase = parse_task_phase(parent.get("status"))
            if parent_phase not in PARENT_VALID:
                parent_errors.append(f"formal parent status is not allowed: {parent_phase.value!r}")
        except ValueError as exc:
            parent_errors.append(f"formal parent status is invalid: {exc}")
        if str(parent.get("project_id", "")) != expected_project_id:
            parent_errors.append(f"formal parent belongs to wrong iteration: {parent_id}")
        if _resolved_path(parent.get("implementation_path")) != expected_path:
            parent_errors.append("formal parent implementation_path does not match the required path")
    parent_gate = _gate(parent_errors, parent_node_id=parent_id or None)

    dispatch_errors = list(common_errors)
    receipts: list[dict] = []
    manifest_employees = tuple(str(entry.get("employee_id", "")) for _, entry in entries)
    if requested_employees and set(manifest_employees) != set(requested_employees):
        dispatch_errors.append(
            f"dispatch_manifest employees {sorted(set(manifest_employees))} do not match expected {sorted(set(requested_employees))}"
        )
    if contract_version >= 2 and not entries:
        dispatch_errors.append("dispatch_manifest is empty")

    try:
        api_tree = api_getter(f"/api/projects/{project_id}/{target_iteration}/tree") or {}
        api_node_ids = {_node_id(node) for node in _rows(api_tree.get("nodes") or api_tree.get("tasks")) if _node_id(node)}
    except Exception as exc:
        api_node_ids = set()
        dispatch_errors.append(f"target tree API failed: {exc}")

    seen_nodes: set[str] = set()
    for task_key, entry in entries:
        employee_id = str(entry.get("employee_id", ""))
        node_id = str(entry.get("node_id", ""))
        node = nodes_by_id.get(node_id)
        prefix = f"{task_key or '<missing-task-key>'}/{employee_id or '<missing-employee>'}/{node_id or '<missing-node>'}"
        node_errors: list[str] = []
        if not task_key or (contract_version >= 2 and task_key.startswith("legacy:")):
            node_errors.append("task_key is missing")
        if not employee_id:
            node_errors.append("employee_id is missing")
        if node_id in seen_nodes:
            node_errors.append("node_id is duplicated in dispatch_manifest")
        seen_nodes.add(node_id)
        if node_id in rejected or node_id.startswith("task_") or not NODE_ID_RE.fullmatch(node_id):
            node_errors.append("node_id is rejected or is not a generated 12-char hex id")
        if node is None:
            if entry.get("_legacy_count") != 1:
                node_errors.append(f"expected exactly one node for employee, found {entry.get('_legacy_count', 0)}")
            else:
                node_errors.append("TaskTree node does not exist")
        else:
            if str(node.get("parent_id", "")) != parent_id:
                node_errors.append("node is not a direct child of formal parent")
            if str(node.get("employee_id", "")) != employee_id:
                node_errors.append("employee_id does not match TaskTree node")
            if str(node.get("project_id", "")) != expected_project_id:
                node_errors.append(f"project_id must be {expected_project_id!r}")
            try:
                parse_task_phase(node.get("status"))
            except ValueError as exc:
                node_errors.append(f"status is invalid: {exc}")
            if _resolved_path(node.get("implementation_path")) != expected_path:
                node_errors.append("implementation_path does not equal the required path")
            fingerprint = str(entry.get("request_fingerprint", ""))
            if contract_version >= 2 and not fingerprint:
                node_errors.append("request_fingerprint is missing")
            if str(node.get("task_key", "")) != task_key:
                node_errors.append("TaskTree task_key does not match dispatch_manifest")
            if str(node.get("dispatch_request_fingerprint", "")) != fingerprint:
                node_errors.append("TaskTree request fingerprint does not match dispatch_manifest")
            verification = node.get("dispatch_verification") or {}
            for key in RECEIPT_KEYS:
                if verification.get(key) is not True:
                    node_errors.append(f"dispatch_verification.{key} is not true")
            if verification.get("started_by") != "executor":
                node_errors.append("dispatch_verification.started_by must be 'executor'")
            if not _aware_iso8601(verification.get("started_at")):
                node_errors.append("dispatch_verification.started_at must be timezone-aware ISO 8601")
            if node_id not in api_node_ids:
                node_errors.append("target tree API does not return node")

            index_path = employees_dir / employee_id / "task_index.yaml"
            if not index_path.exists():
                node_errors.append(f"employee task index missing: {index_path}")
            else:
                index_rows = _rows(_load_yaml(index_path) or [])
                exact = [
                    row for row in index_rows
                    if str(row.get("node_id", "")) == node_id
                    and _resolved_path(row.get("tree_path")) == str(tree_path.resolve())
                ]
                if len(exact) != 1:
                    node_errors.append("employee task_index.yaml lacks one exact node/tree entry")
            try:
                board = api_getter(f"/api/employee/{employee_id}/taskboard") or {}
                board_ids = {_node_id(task) for task in _rows(board.get("tasks"))}
                if node_id not in board_ids:
                    node_errors.append("employee taskboard API does not return node")
            except Exception as exc:
                node_errors.append(f"employee taskboard API failed: {exc}")

        dispatch_errors.extend(f"{prefix}: {reason}" for reason in node_errors)
        if not node_errors and node is not None:
            receipts.append({
                "task_key": task_key,
                "employee_id": employee_id,
                "node_id": node_id,
                "parent_id": parent_id,
                "status": str(node.get("status")),
                "verification": node.get("dispatch_verification") or {},
            })
    dispatch_gate = _gate(dispatch_errors, receipts=receipts)

    closure_errors = list(common_errors)
    if not entries:
        closure_errors.append("no required business children are declared")
    for task_key, entry in entries:
        node_id = str(entry.get("node_id", ""))
        node = nodes_by_id.get(node_id)
        if node is None:
            closure_errors.append(f"{task_key}: business child is missing")
            continue
        try:
            phase = parse_task_phase(node.get("status"))
            if phase not in CLOSURE_COMPLETE:
                closure_errors.append(f"{task_key}/{node_id}: child is not closure-complete ({phase.value})")
        except ValueError as exc:
            closure_errors.append(f"{task_key}/{node_id}: invalid status: {exc}")
        audit = node.get("acceptance_audit")
        if not isinstance(audit, dict):
            closure_errors.append(f"{task_key}/{node_id}: explicit acceptance_audit is missing")
        else:
            if audit.get("decision") != "accepted":
                closure_errors.append(f"{task_key}/{node_id}: acceptance decision is not accepted")
            if audit.get("decided_via") != "accept_child":
                closure_errors.append(f"{task_key}/{node_id}: acceptance was not decided via accept_child")
            if str(audit.get("notes", "")).lower().startswith("auto-accepted"):
                closure_errors.append(f"{task_key}/{node_id}: Auto-accepted evidence is forbidden")
            if not _aware_iso8601(audit.get("decided_at")):
                closure_errors.append(f"{task_key}/{node_id}: acceptance decided_at is invalid")
    if parent is not None:
        try:
            if parse_task_phase(parent.get("status")) not in CLOSURE_COMPLETE:
                closure_errors.append("formal parent is not closure-complete")
        except ValueError as exc:
            closure_errors.append(f"formal parent status is invalid: {exc}")
        if parent.get("hold_reason") == "awaiting_manual_review":
            closure_errors.append("manual review escalation is still pending")
    closure_gate = _gate(closure_errors)

    all_errors = [*parent_gate["errors"], *dispatch_gate["errors"], *closure_gate["errors"]]
    report = {
        "ok": parent_gate["ok"] and dispatch_gate["ok"] and closure_gate["ok"],
        "project_id": project_id,
        "iteration_id": target_iteration,
        "workflow_contract_version": contract_version,
        "parent_gate": parent_gate,
        "dispatch_gate": dispatch_gate,
        "closure_gate": closure_gate,
        "errors": list(dict.fromkeys(all_errors)),
    }
    if parent_id:
        report["formal_parent_node_id"] = parent_id
    if receipts:
        report["receipts"] = receipts
    return report


def _empty_report(project_id: str, iteration_id: str, errors: list[str]) -> dict:
    return {
        "ok": False,
        "project_id": project_id,
        "iteration_id": iteration_id,
        "parent_gate": _gate(list(errors)),
        "dispatch_gate": _gate(list(errors), receipts=[]),
        "closure_gate": _gate(list(errors)),
        "errors": list(errors),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", required=True)
    parser.add_argument("--implementation-path", required=True)
    parser.add_argument("--project-dir", type=Path, default=PROJECT_DIR)
    parser.add_argument("--employees-dir", type=Path, default=EMPLOYEES_DIR)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--employee", action="append", dest="employees")
    parser.add_argument("--reject-node", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = verify_formal_dispatch(
        project_dir=args.project_dir,
        target_iteration=args.iteration,
        expected_employees=tuple(args.employees or EMPLOYEES),
        expected_implementation_path=args.implementation_path,
        employees_dir=args.employees_dir,
        rejected_ids=set(args.reject_node),
        api_getter=lambda path: get_json(path, base_url=args.base_url),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
