"""Tree tools — dispatch_child, accept_child, reject_child.

These tools allow parent tasks to dispatch subtasks to employees,
then accept or reject results. They operate on a TaskTree persisted
as task_tree.yaml in the project directory.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core.config import CEO_ID, PROJECT_YAML_FILENAME, SYSTEM_AGENT, TASK_TREE_FILENAME, read_text_utf, write_text_utf
from onemancompany.core.models import EventType
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _authoritative_tree_path(project_dir: str, tree_path: str = "") -> Path:
    """Resolve a formal task's authoritative tree path without inventing one."""
    return Path(tree_path) if tree_path else Path(project_dir) / TASK_TREE_FILENAME


def _load_tree(project_dir: str, tree_path: str = "") -> TaskTree:
    """Load the TaskTree, preferring the scheduler entry's authoritative path."""
    from onemancompany.core.task_tree import get_tree
    path = _authoritative_tree_path(project_dir, tree_path)
    if not path.exists():
        if tree_path:
            raise FileNotFoundError(f"Authoritative TaskTree not found: {path}")
        logger.warning("task_tree.yaml not found at {}", path)
        return TaskTree(project_id="")
    return get_tree(path)


def _find_entry_for_task(task_id: str) -> tuple[str, str]:
    """Find (project_dir, tree_path) for a task_id in schedule or running entries.

    Running tasks are popped from _schedule, so we also check _current_entries.
    Returns ("", "") if not found.
    """
    from onemancompany.core.vessel import employee_manager

    # Check schedule first
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                return str(Path(e.tree_path).parent), e.tree_path

    # Check currently running tasks (popped from schedule)
    for e in employee_manager._current_entries.values():
        if e.node_id == task_id:
            return str(Path(e.tree_path).parent), e.tree_path

    return "", ""


def _save_tree(project_dir: str, tree: TaskTree, tree_path: str = "") -> None:
    """Schedule a save to the authoritative TaskTree path."""
    from onemancompany.core.task_tree import save_tree_async
    path = _authoritative_tree_path(project_dir, tree_path)
    save_tree_async(path)


def _persist_tree_for_dispatch(project_dir: str, tree: TaskTree, tree_path: str = "") -> bool:
    """Synchronously persist a tree before dispatch_child returns.

    ``dispatch_child`` is a synchronous tool.  A fire-and-forget save can
    return ``dispatched`` while task_tree.yaml still contains the old tree,
    making the task invisible to recovery and the taskboard.
    """
    from onemancompany.core.task_tree import get_tree_lock, register_tree

    path = _authoritative_tree_path(project_dir, tree_path)
    register_tree(path, tree)
    with get_tree_lock(path):
        tree.save(path)
    return path.exists()


def _is_standard_v2(tree: TaskTree) -> bool:
    return tree.mode == "standard" and int(getattr(tree, "workflow_contract_version", 1)) >= 2


def _normalize_dispatch_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().replace("\r\n", "\n").split("\n"))


def _dispatch_fingerprint(
    *,
    employee_id: str,
    description: str,
    acceptance_criteria: list[str],
    implementation_path: str,
    timeout_seconds: int,
    depends_on: list[str],
) -> str:
    payload = {
        "employee_id": str(employee_id),
        "description": _normalize_dispatch_text(description),
        "acceptance_criteria": [_normalize_dispatch_text(item) for item in acceptance_criteria],
        "implementation_path": str(Path(implementation_path).expanduser()) if implementation_path else "",
        "timeout_seconds": int(timeout_seconds),
        "depends_on": sorted(str(item) for item in depends_on),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _runtime_storage_call(coroutine):
    from onemancompany.core.runtime_storage import get_runtime_storage

    storage = get_runtime_storage()
    if storage is None:
        raise RuntimeError("RuntimeStorage is unavailable")
    return storage.run_sync(coroutine)


def _advance_dispatch_intent_sync(**kwargs):
    from onemancompany.core.runtime_storage import get_runtime_storage

    storage = get_runtime_storage()
    if storage is None:
        raise RuntimeError("RuntimeStorage is unavailable")
    return storage.run_sync(storage.advance_dispatch_intent(**kwargs))


def _mark_active_tasks_dirty() -> None:
    """Signal that the durable active-task projection must be refreshed."""
    from onemancompany.core.config import DirtyCategory
    from onemancompany.core.store import mark_dirty

    mark_dirty(DirtyCategory.ACTIVE_TASKS)


def _verify_dispatch_persistence(
    *,
    tree_path: str,
    node_id: str,
    employee_id: str,
    schedule_node_called: bool,
    schedule_registered: bool = False,
    schedule_expected: bool = True,
) -> dict:
    """Verify the durable evidence behind a dispatch result.

    The check rereads disk instead of trusting the model's prose or only the
    in-memory tree. ``schedule_node_called`` is set only after the real method
    returns successfully.
    """
    from onemancompany.core.store import load_task_index
    from onemancompany.core.task_tree import TaskTree

    path = Path(tree_path)
    node_created = False
    if path.exists():
        try:
            persisted_tree = TaskTree.load(path)
            persisted_node = persisted_tree.get_node(node_id)
            node_created = bool(
                persisted_node
                and persisted_node.employee_id == employee_id
            )
        except Exception as exc:
            logger.error("[DISPATCH] Cannot verify task tree {}: {}", path, exc)

    index_written = False
    try:
        index_written = any(
            entry.get("node_id") == node_id
            and str(entry.get("tree_path", "")) == str(path)
            for entry in load_task_index(employee_id)
        )
    except Exception as exc:
        logger.error("[DISPATCH] Cannot verify task index for {}: {}", employee_id, exc)

    verified = node_created and index_written
    if schedule_expected:
        verified = verified and schedule_node_called and schedule_registered

    return {
        "dispatch_child_called": True,
        "task_tree_node_created": node_created,
        "task_tree_persisted": node_created,
        "task_index_written": index_written,
        "schedule_node_called": schedule_node_called,
        "schedule_registered": schedule_registered,
        "schedule_expected": schedule_expected,
        "verified": verified,
    }


def _record_dispatch_receipt(
    *,
    project_dir: str,
    parent_node_id: str,
    child,
    verification: dict,
) -> dict:
    """Attach a durable dispatch receipt to a child and its parent log.

    The return value is stored on the child node and survives restart.  The
    parent execution log is an additional audit trail, so a child that was
    merely inserted into YAML cannot be mistaken for a real tool dispatch.
    """
    receipt = {
        **verification,
        "receipt_id": uuid.uuid4().hex[:16],
    }
    child.dispatch_verification = receipt

    try:
        from onemancompany.core.vessel import _append_node_execution_log

        _append_node_execution_log(
            project_dir,
            parent_node_id,
            "dispatch_verified" if verification.get("verified") is True else "dispatch_verification_failed",
            json.dumps(
                {
                    "child_node_id": child.id,
                    "employee_id": child.employee_id,
                    "receipt_id": receipt["receipt_id"],
                    "verification": verification,
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:  # pragma: no cover - audit logging must not hide dispatch result
        logger.warning("[DISPATCH] Could not write dispatch receipt log for {}: {}", child.id, exc)

    return receipt


def _resolve_project_root(project_dir: str) -> Path | None:
    """Resolve project root dir containing project.yaml from an iteration dir.

    project_dir is typically projects/{slug}/iterations/iter_NNN/.
    project.yaml lives at projects/{slug}/project.yaml.
    """
    d = Path(project_dir)
    # Check project_dir itself first
    if (d / PROJECT_YAML_FILENAME).exists():
        return d
    # Walk up (max 3 levels) looking for project.yaml
    for _ in range(3):
        d = d.parent
        if (d / PROJECT_YAML_FILENAME).exists():
            return d
    return None


def _add_to_project_team(project_dir: str, employee_id: str) -> None:
    """Add employee to project.yaml team list (idempotent)."""
    import yaml
    root = _resolve_project_root(project_dir)
    if root is None:
        logger.debug("No project.yaml found from {}", project_dir)
        return
    project_yaml = root / PROJECT_YAML_FILENAME
    try:
        data = yaml.safe_load(read_text_utf(project_yaml)) or {}
        team = data.get("team", [])
        if any(m.get("employee_id") == employee_id for m in team):
            return  # already in team
        from datetime import datetime
        team.append({
            "employee_id": employee_id,
            "role": "",
            "joined_at": datetime.now().isoformat(),
        })
        data["team"] = team
        write_text_utf(project_yaml, yaml.dump(data, allow_unicode=True, sort_keys=False))
    except Exception:  # pragma: no cover
        logger.warning("Failed to add {} to project team in {}", employee_id, project_dir)  # pragma: no cover


def _get_current_node(tree: TaskTree, task_id: str):
    """Look up the TaskNode for the given task/node ID."""
    return tree.get_node(task_id)


def set_current_node_product_id(product_id: str) -> bool:
    """Stamp the currently-executing task node with a linked product_id and persist.

    Called after an agent creates/links a product so descendant nodes inherit it
    via dispatch_child (regression guard for #395). Returns False when there is no
    tree context (e.g. system/adhoc tasks) so callers can no-op gracefully.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id
    from onemancompany.core.task_tree import get_tree_lock

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return False
    project_dir, tree_path_str = _find_entry_for_task(task_id)
    if not project_dir or not tree_path_str:
        return False
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir, tree_path_str)
        node = _get_current_node(tree, task_id)
        if not node:
            return False
        if node.product_id == product_id:
            return True  # already linked, nothing to persist
        node.product_id = product_id
        _save_tree(project_dir, tree, tree_path_str)
    return True


def _create_standalone_ceo_request(
    description: str,
    requester_task_id: str,
    vessel,
) -> dict:
    """Create a CEO request without requiring a task tree context.

    Used when agents running system/adhoc tasks (no tree) need to escalate to CEO.
    Creates a CEO_REQUEST node and schedules it via CeoExecutor so CEO sees it
    in the conversation UI.
    """
    import asyncio
    from onemancompany.core.events import CompanyEvent, event_bus
    from onemancompany.core.models import EventType
    from onemancompany.core.config import SYSTEM_AGENT
    from onemancompany.core.vessel import employee_manager

    project_id = "default"
    source = vessel.employee_id if vessel else "unknown"

    # Create a temporary tree so the node has a tree_path and can be scheduled.
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.config import PROJECTS_DIR
    from onemancompany.core.vessel import _save_project_tree

    adhoc_dir = PROJECTS_DIR / "_adhoc_ceo"
    adhoc_dir.mkdir(parents=True, exist_ok=True)
    tree_path_file = adhoc_dir / TASK_TREE_FILENAME

    # Load existing adhoc tree or create one
    if tree_path_file.exists():
        from onemancompany.core.task_tree import get_tree
        tree = get_tree(tree_path_file, project_id=project_id)
    else:
        tree = TaskTree(project_id=project_id)
        # Create a synthetic CEO root
        root = tree.create_root(employee_id=CEO_ID, description="Ad-hoc CEO requests")
        root.node_type = NodeType.CEO_PROMPT
        from onemancompany.core.task_lifecycle import TaskPhase
        root.set_status(TaskPhase.PROCESSING)

    # Add CEO_REQUEST node under root
    ceo_node = tree.add_child(
        parent_id=tree.root_id,
        employee_id=CEO_ID,
        description=description,
        acceptance_criteria=[],
    )
    ceo_node.node_type = NodeType.CEO_REQUEST

    _save_project_tree(str(adhoc_dir), tree)
    tree_path_str = str(tree_path_file)

    # Schedule the node so CeoExecutor handles it
    employee_manager.schedule_node(CEO_ID, ceo_node.id, tree_path_str)
    employee_manager._schedule_next(CEO_ID)

    # CeoExecutor will create the project conversation and push the message
    # when it executes this node. Broadcast a hint to frontend now.
    main_loop = getattr(employee_manager, "_event_loop", None)
    if main_loop and main_loop.is_running():
        coro = event_bus.publish(CompanyEvent(
            type=EventType.CEO_SESSION_MESSAGE,
            payload={
                "project_id": project_id,
                "node_id": ceo_node.id,
                "message": description,
                "source_employee": source,
                "interaction_type": "ceo_request",
            },
            agent=SYSTEM_AGENT,
        ))
        asyncio.run_coroutine_threadsafe(coro, main_loop)

    return {
        "status": "dispatched",
        "node_id": ceo_node.id,
        "employee_id": CEO_ID,
        "description": description,
        "node_type": NodeType.CEO_REQUEST,
        "ceo_request": True,
        "message": "Task dispatched to CEO. CEO will respond when available.",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def dispatch_child(
    target_employee_id: str,
    description: str,
    acceptance_criteria: list[str],
    title: str = "",
    timeout_seconds: int = 3600,
    depends_on: list[str] | None = None,
    directive: str = "",
    task_key: str = "",
) -> dict:
    """Dispatch a child task to an employee with acceptance criteria.

    Creates a child node in the task tree and schedules it for execution.
    The child must complete and be accepted before this task can finish.

    IMPORTANT: The description should preserve the original task from the CEO.
    Do NOT rewrite or summarize the original problem — pass it through as-is.
    If you need to add your own analysis, instructions, or context for the
    executor, use the directive parameter. The executor will see the original
    description + all directives from the chain (CEO → EA → COO → Employee).

    If depends_on is provided, the child will only be scheduled when all dependency
    nodes reach a terminal status. Until then the child is created in the tree
    but not scheduled.

    Args:
        target_employee_id: Target employee ID (who to assign the task to)
        description: The task description — preserve the original wording from upstream
        title: Short task name (e.g. "Build login page") — shown in task tree view. Always provide a brief, descriptive title.
        acceptance_criteria: List of measurable criteria the result must meet
        timeout_seconds: Max seconds allowed for the child task (default 3600)
        depends_on: List of TaskNode IDs that must complete before this child starts
        directive: Your analysis, instructions, or context for the executor (appended to directive chain)
        task_key: Stable business key. Required for standard workflow contract v2.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Load tree, find current node
    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir or not tree_path_str:
        # --- Standalone CEO request (no tree context, e.g. system/adhoc tasks) ---
        if target_employee_id == CEO_ID:
            return _create_standalone_ceo_request(
                description=description,
                requester_task_id=task_id,
                vessel=vessel,
            )
        return {"status": "error", "message": "No project directory in current task context."}

    # Validate target_employee_id format and existence
    from onemancompany.agents.common_tools import _validate_employee_id
    id_err = _validate_employee_id(target_employee_id)
    if id_err:  # pragma: no cover
        return id_err  # pragma: no cover
    from onemancompany.core.store import load_employee
    if not load_employee(target_employee_id):
        return {"status": "error", "message": f"Employee {target_employee_id} not found. Use list_colleagues() to find valid IDs."}

    from onemancompany.core.task_tree import get_tree_lock
    tree_lock = get_tree_lock(tree_path_str)

    with tree_lock:
        tree = _load_tree(project_dir, tree_path_str)
        current_node = _get_current_node(tree, task_id)
        if not current_node:  # pragma: no cover
            return {"status": "error", "message": "Current task not found in task tree."}  # pragma: no cover

        # EA dispatch restrictions
        from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
        if current_node.employee_id == EA_ID:
            # Self-dispatch guard — always blocked
            if target_employee_id == EA_ID:
                return {
                    "status": "error",
                    "message": "EA cannot dispatch tasks to itself. Please dispatch to an appropriate team member.",
                }
            # O-level restriction — only in standard mode
            if tree.mode != "simple":
                allowed_targets = {HR_ID, COO_ID, CSO_ID}
                if target_employee_id not in allowed_targets:
                    suggestion = f"COO({COO_ID})"
                    return {
                        "status": "error",
                        "message": (
                            f"EA cannot directly dispatch tasks to {target_employee_id}. "
                            f"Please dispatch_child to the corresponding O-level executive instead: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID}). "
                            f"Hint: for development/design/operations tasks, dispatch to {suggestion} to organize team execution. "
                            f"Please immediately re-call dispatch_child with the correct target_employee_id."
                        ),
                    }

        # --- Circuit breaker: children count limit ---
        from onemancompany.core.config import MAX_CHILDREN_PER_NODE, MAX_TREE_DEPTH
        active_children = tree.get_active_children(task_id)
        if len(active_children) >= MAX_CHILDREN_PER_NODE:
            return {
                "status": "error",
                "message": f"Child task limit reached ({MAX_CHILDREN_PER_NODE}). Please consolidate existing tasks or escalate.",
            }

        # --- Circuit breaker: tree depth limit ---
        depth = 0
        walker = current_node
        while walker.parent_id:
            depth += 1
            walker = tree.get_node(walker.parent_id)
            if not walker:  # pragma: no cover
                break  # pragma: no cover
        if depth + 1 >= MAX_TREE_DEPTH:
            return {
                "status": "error",
                "message": f"Task tree has reached maximum depth ({MAX_TREE_DEPTH}). Cannot dispatch further. Please complete directly or escalate.",
            }

        # Normalize depends_on
        depends_on = depends_on or []

        # Validate depends_on IDs exist in tree
        for dep_id in depends_on:
            if not tree.get_node(dep_id):
                return {
                    "status": "error",
                    "message": f"Dependency node {dep_id} not found in task tree.",
                }

        # Formal v2 dispatches are backed by a durable intent before the TaskTree
        # is mutated. Persisted standard trees fail closed when the key/storage
        # contract is unavailable; synthetic legacy unit trees remain compatible.
        formal_v2 = _is_standard_v2(tree) and bool(current_node.task_key) and Path(tree_path_str).exists()
        request_fingerprint = ""
        dispatch_intent = None
        replaying_intent = False
        if formal_v2:
            if not str(task_key or "").strip():
                return {
                    "status": "error",
                    "error_type": "DispatchPersistenceError",
                    "message": "standard v2 dispatch_child requires a non-empty task_key.",
                }
            task_key = str(task_key).strip()
            request_fingerprint = _dispatch_fingerprint(
                employee_id=target_employee_id,
                description=description,
                acceptance_criteria=acceptance_criteria,
                implementation_path=current_node.implementation_path,
                timeout_seconds=timeout_seconds,
                depends_on=depends_on,
            )
            try:
                from onemancompany.core.runtime_storage import (
                    DispatchIntentConflict,
                    get_runtime_storage,
                )

                storage = get_runtime_storage()
                if storage is None:
                    raise RuntimeError("RuntimeStorage is unavailable")
                dispatch_intent = storage.run_sync(storage.prepare_dispatch_intent(
                    parent_id=task_id,
                    employee_id=target_employee_id,
                    task_key=task_key,
                    request_fingerprint=request_fingerprint,
                ))
            except DispatchIntentConflict as exc:
                return {
                    "status": "error",
                    "error_type": "IdempotencyConflict",
                    "message": str(exc),
                }
            except Exception as exc:
                logger.error("[DISPATCH] cannot prepare durable intent {}: {}", task_key, exc)
                return {
                    "status": "error",
                    "error_type": "DispatchPersistenceError",
                    "message": f"Cannot prepare durable dispatch intent: {exc}",
                }
            replaying_intent = bool(dispatch_intent.get("node_id"))
            if not replaying_intent:
                matching_children = [
                    candidate for candidate in tree.get_children(task_id)
                    if candidate.employee_id == target_employee_id
                    and candidate.task_key == task_key
                ]
                conflicting_children = [
                    candidate for candidate in matching_children
                    if candidate.dispatch_request_fingerprint != request_fingerprint
                ]
                if conflicting_children:
                    return {
                        "status": "error",
                        "error_type": "IdempotencyConflict",
                        "node_id": conflicting_children[0].id,
                        "message": f"TaskTree already contains a different request for {task_key}",
                    }
                if len(matching_children) > 1:
                    return {
                        "status": "error",
                        "error_type": "DispatchReconciliationRequired",
                        "message": f"Multiple TaskTree children exist for durable dispatch key {task_key}",
                    }
                if matching_children:
                    child_from_tree = matching_children[0]
                    try:
                        dispatch_intent = _advance_dispatch_intent_sync(
                            parent_id=task_id,
                            employee_id=target_employee_id,
                            task_key=task_key,
                            request_fingerprint=request_fingerprint,
                            state="tree_written",
                            node_id=child_from_tree.id,
                        )
                    except Exception as exc:
                        return {
                            "status": "error",
                            "error_type": "DispatchReconciliationRequired",
                            "node_id": child_from_tree.id,
                            "message": f"Existing TaskTree child could not be rebound to its dispatch intent: {exc}",
                        }
                    replaying_intent = True
            if (
                replaying_intent
                and dispatch_intent.get("state") in {"scheduled", "started"}
                and isinstance(dispatch_intent.get("receipt"), dict)
            ):
                persisted_child = tree.get_node(str(dispatch_intent["node_id"]))
                if persisted_child is None:
                    return {
                        "status": "error",
                        "error_type": "DispatchReconciliationRequired",
                        "node_id": dispatch_intent["node_id"],
                        "message": "Dispatch intent is scheduled but its TaskTree child is missing.",
                    }
                return {
                    "status": "already_dispatched",
                    "node_id": persisted_child.id,
                    "employee_id": target_employee_id,
                    "description": persisted_child.description,
                    "dependency_status": "resolved",
                    "verification": dict(dispatch_intent["receipt"]),
                }

        # --- CEO request interception (idempotency check BEFORE creating child) ---
        if target_employee_id == CEO_ID and not replaying_intent:
            from onemancompany.core.task_lifecycle import TaskPhase as _TP
            existing = [
                c for c in tree.get_children(task_id)
                if c.node_type == NodeType.CEO_REQUEST
                and c.status not in (_TP.FINISHED.value, _TP.CANCELLED.value, _TP.ACCEPTED.value)
            ]
            if existing:
                dup = existing[0]
                return {
                    "status": "already_dispatched",
                    "node_id": dup.id,
                    "employee_id": target_employee_id,
                    "description": dup.description,
                    "node_type": NodeType.CEO_REQUEST,
                    "ceo_request": True,
                    "message": (
                        f"A CEO request ({dup.id}) is already pending. Do NOT create another. "
                        "Your task will automatically pause (HOLDING) until the CEO responds. "
                        "You should finish your current output now — the system handles the rest."
                    ),
                }

        # Create once, or recover the child bound to the durable intent after a
        # response/process failure. The replay path never calls add_child().
        if replaying_intent:
            child = tree.get_node(str(dispatch_intent["node_id"]))
            if child is None:
                return {
                    "status": "error",
                    "error_type": "DispatchReconciliationRequired",
                    "node_id": dispatch_intent["node_id"],
                    "message": "Dispatch intent exists but its TaskTree child is missing.",
                }
        else:
            child = tree.add_child(
                parent_id=task_id,
                employee_id=target_employee_id,
                description=description,
                acceptance_criteria=acceptance_criteria,
                timeout_seconds=timeout_seconds,
                depends_on=depends_on,
                title=title,
            )
            child.project_id = current_node.project_id
            child.product_id = current_node.product_id  # inherit linked product (#395)
            child.project_dir = project_dir

            # Propagate directive chain: inherit parent's directives + add new directive
            current_node.load_content(project_dir)
            child.directives = list(current_node.directives)  # copy parent chain
            if directive:  # pragma: no cover
                from datetime import datetime  # pragma: no cover
                child.directives.append({  # pragma: no cover
                    "from": vessel.employee_id,
                    "directive": directive,
                    "at": datetime.now().isoformat(),
                })

            # Auto-register dispatched employee in project team for project history
            _add_to_project_team(project_dir, target_employee_id)

        if formal_v2:
            child.task_key = task_key
            child.dispatch_request_fingerprint = request_fingerprint
            manifest_entry = {
                "employee_id": target_employee_id,
                "task_key": task_key,
                "node_id": child.id,
                "request_fingerprint": request_fingerprint,
            }
            existing_manifest = tree.dispatch_manifest.get(task_key)
            if existing_manifest and existing_manifest != manifest_entry:
                return {
                    "status": "error",
                    "error_type": "IdempotencyConflict",
                    "message": f"dispatch_manifest already contains a different entry for {task_key}",
                }
            tree.dispatch_manifest[task_key] = manifest_entry
            try:
                _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
                dispatch_intent = _advance_dispatch_intent_sync(
                    parent_id=task_id,
                    employee_id=target_employee_id,
                    task_key=task_key,
                    request_fingerprint=request_fingerprint,
                    state="tree_written",
                    node_id=child.id,
                )
            except Exception as exc:
                logger.error("[DISPATCH] failed to persist tree intent {}: {}", task_key, exc)
                return {
                    "status": "error",
                    "error_type": "DispatchPersistenceError",
                    "node_id": child.id,
                    "message": f"TaskTree was created but durable intent reconciliation failed: {exc}",
                }

        if target_employee_id == CEO_ID:
            child.node_type = NodeType.CEO_REQUEST
            # Signal vessel to auto-HOLD parent after execution
            current_node.hold_reason = f"ceo_request={child.id},no_watchdog=1"
            # Fall through to normal scheduling path below — CeoExecutor handles the rest

        # --- Normal employee dispatch (existing logic) ---
        # When dispatching to a DIFFERENT employee, the parent should HOLD
        # until child tasks complete — otherwise it gets marked COMPLETED
        # immediately and never has a chance to review/accept children.
        if target_employee_id != current_node.employee_id and not current_node.hold_reason:
            current_node.hold_reason = f"awaiting_children,no_watchdog=1"

        # Check if dependencies are already satisfied
        deps_resolved = tree.all_deps_resolved(child.id)

        if not deps_resolved:
            _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
            _mark_active_tasks_dirty()
            _save_tree(project_dir, tree, tree_path_str)  # compatibility hook; sync save already completed
            # Persist task index entry for taskboard even though not yet scheduled
            from onemancompany.core.store import append_task_index_entry
            append_task_index_entry(target_employee_id, child.id, tree_path_str)
            verification = _verify_dispatch_persistence(
                tree_path=tree_path_str,
                node_id=child.id,
                employee_id=target_employee_id,
                schedule_node_called=False,
                schedule_expected=False,
            )
            receipt = _record_dispatch_receipt(
                project_dir=project_dir,
                parent_node_id=task_id,
                child=child,
                verification=verification,
            )
            if formal_v2:
                try:
                    dispatch_intent = _advance_dispatch_intent_sync(
                        parent_id=task_id,
                        employee_id=target_employee_id,
                        task_key=task_key,
                        request_fingerprint=request_fingerprint,
                        state="index_written",
                        node_id=child.id,
                        receipt=receipt,
                    )
                except Exception as exc:
                    return {
                        "status": "error",
                        "error_type": "DispatchReconciliationRequired",
                        "node_id": child.id,
                        "message": f"Task index exists but dispatch intent was not advanced: {exc}",
                    }
            # The receipt is part of the durable dispatch evidence. Persist it
            # after rereading the tree/index so a YAML-only child cannot later
            # be mistaken for a tool-created child.
            _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
            if getattr(tree, "_source_dir", None) and not verification["verified"]:
                return {
                    "status": "error",
                    "node_id": child.id,
                    "employee_id": target_employee_id,
                    "description": description,
                    "dependency_status": "waiting",
                    "verification": verification,
                    "message": "Task was created but durable dispatch evidence is incomplete.",
                }
            return {
                "status": "dispatched_waiting",
                "node_id": child.id,
                "employee_id": target_employee_id,
                "description": description,
                "dependency_status": "waiting",
                "verification": verification,
            }

        # Save tree and schedule via employee_manager.  The call flag is set
        # only after schedule_node returns, so a failed call cannot look like
        # a successful assignment.
        from onemancompany.core.vessel import employee_manager
        _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
        _mark_active_tasks_dirty()
        _save_tree(project_dir, tree, tree_path_str)  # compatibility hook; sync save already completed
        schedule_node_called = False
        schedule_registered = False
        try:
            schedule_result = employee_manager.schedule_node(
                target_employee_id, child.id, tree_path_str
            )
            schedule_node_called = True
            # EmployeeManager returns False when the task index was written but
            # no executor/runtime queue accepted the node.  Keep compatibility
            # with test doubles and older managers that returned None by only
            # treating an explicit False as a failed registration.
            schedule_registered = schedule_result is not False
        except Exception as exc:
            logger.error("[DISPATCH] schedule_node failed for {}: {}", child.id, exc)

        if schedule_registered:
            employee_manager._schedule_next(target_employee_id)

        verification = _verify_dispatch_persistence(
            tree_path=tree_path_str,
            node_id=child.id,
            employee_id=target_employee_id,
            schedule_node_called=schedule_node_called,
            schedule_registered=schedule_registered,
        )
        receipt = _record_dispatch_receipt(
            project_dir=project_dir,
            parent_node_id=task_id,
            child=child,
            verification=verification,
        )
        if formal_v2:
            try:
                dispatch_intent = _advance_dispatch_intent_sync(
                    parent_id=task_id,
                    employee_id=target_employee_id,
                    task_key=task_key,
                    request_fingerprint=request_fingerprint,
                    state="scheduled" if schedule_registered else "index_written",
                    node_id=child.id,
                    receipt=receipt,
                )
            except Exception as exc:
                return {
                    "status": "error",
                    "error_type": "DispatchReconciliationRequired",
                    "node_id": child.id,
                    "verification": verification,
                    "message": f"Dispatch side effects exist but intent reconciliation failed: {exc}",
                }
        # Persist the receipt as part of the same durable dispatch contract.
        _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
        # Existing mocked unit tests use a synthetic, non-existent tree path.
        # Real dispatches always have task_tree.yaml because _load_tree found
        # the current task there; enforce the durable contract for that path.
        if getattr(tree, "_source_dir", None) and not verification["verified"]:
            return {
                "status": "error",
                "node_id": child.id,
                "employee_id": target_employee_id,
                "description": description,
                "dependency_status": "resolved",
                "verification": verification,
                "message": "Task was created but durable dispatch evidence is incomplete.",
            }

        return {
            "status": "dispatched",
            "node_id": child.id,
            "employee_id": target_employee_id,
            "description": description,
            "dependency_status": "resolved",
            "verification": verification,
        }


@tool
def accept_child(
    node_id: str,
    notes: str = "",
    criteria_results: list[dict] | None = None,
    evidence_refs: list[str] | None = None,
) -> dict:
    """Accept a child task's result after reviewing it.

    IMPORTANT: node_id is a TaskNode ID (e.g. "a1b2c3d4e5f6"), NOT an employee ID.
    You must first dispatch_child to create a child task, then accept it after it completes.
    Use the node_id returned by dispatch_child.

    Args:
        node_id: The TaskNode ID of the child to accept (12-char hex, NOT employee ID)
        notes: Optional acceptance notes
        criteria_results: Per-criterion review outcomes for the acceptance audit
        evidence_refs: Durable evidence references reviewed before deciding
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Find project_dir from current task context
    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:  # pragma: no cover
        return {"status": "error", "message": "No project context."}  # pragma: no cover

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir, tree_path_str)
        node = tree.get_node(node_id)
        if not node:
            # Help agent: list actual children so they know what node_ids exist
            current_node = tree.get_node(task_id)
            children = tree.get_children(task_id) if current_node else []
            if children:
                child_list = ", ".join(f"{c.id} ({c.status})" for c in children)
                hint = f" Your child nodes: {child_list}"
            else:  # pragma: no cover
                hint = " You have no child tasks yet. Use dispatch_child first to create one."  # pragma: no cover
            return {"status": "error", "message": f"Node {node_id} not found.{hint}"}

        # Normalize status to string for comparison (TaskNode.status is str)
        current = node.status.value if hasattr(node.status, "value") else node.status

        # Idempotent: already accepted/finished/cancelled → return success without re-transitioning
        if current == TaskPhase.ACCEPTED.value:
            return {
                "status": TaskPhase.ACCEPTED.value,
                "node_id": node_id,
                "notes": notes,
                "already_accepted": True,
                "acceptance_audit": node.acceptance_audit,
            }
        if current == TaskPhase.FINISHED.value:  # pragma: no cover — race: task finished between check and accept
            return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes, "already_finished": True}  # pragma: no cover
        if current == TaskPhase.CANCELLED.value:  # pragma: no cover
            return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes, "already_cancelled": True}  # pragma: no cover

        # Only completed tasks can be accepted
        if current != TaskPhase.COMPLETED.value:
            return {
                "status": "error",
                "message": f"Cannot accept node {node_id}: current status is '{current}', must be 'completed' first.",
            }

        node.set_status(TaskPhase.ACCEPTED)
        node.acceptance_result = {"passed": True, "notes": notes}
        if _is_standard_v2(tree) and bool(node.task_key):
            from datetime import datetime

            review_node = tree.get_node(task_id)
            node.acceptance_audit = {
                "decision": "accepted",
                "decided_by": str(vessel.employee_id),
                "decided_via": "accept_child",
                "review_node_id": review_node.id if review_node and review_node.node_type == NodeType.REVIEW else "",
                "decided_at": datetime.now().astimezone().isoformat(),
                "criteria_results": list(criteria_results or []),
                "evidence_refs": [str(ref) for ref in (evidence_refs or [])],
                "notes": notes,
            }
            _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
        else:
            _save_tree(project_dir, tree, tree_path_str)

        # Trigger dependency resolution for dependents
        from onemancompany.core.vessel import _trigger_dep_resolution
        _trigger_dep_resolution(project_dir, tree, node)

        return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes}


@tool
def reject_child(
    node_id: str,
    reason: str,
    retry: bool = True,
    criteria_results: list[dict] | None = None,
    evidence_refs: list[str] | None = None,
) -> dict:
    """Reject a child task's result.

    Args:
        node_id: The TaskNode ID of the child to reject
        reason: Why the result was rejected
        retry: If True, schedule a correction task. If False, mark as failed.
        criteria_results: Per-criterion review outcomes for the acceptance audit
        evidence_refs: Durable evidence references reviewed before deciding
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:  # pragma: no cover — requires missing runtime context
        return {"status": "error", "message": "No agent context."}  # pragma: no cover

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:  # pragma: no cover
        return {"status": "error", "message": "No project context."}  # pragma: no cover

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir, tree_path_str)
        node = tree.get_node(node_id)
        if not node:  # pragma: no cover
            return {"status": "error", "message": f"Node {node_id} not found. Check dispatch_child() return value for correct node_id."}  # pragma: no cover

        current = node.status

        # Only completed tasks can be rejected
        if current != TaskPhase.COMPLETED.value:  # pragma: no cover
            return {  # pragma: no cover
                "status": "error",
                "message": f"Cannot reject node {node_id}: current status is '{current}', must be 'completed' first. Wait for the employee to finish before rejecting.",
            }

        # --- Max retry guard: prevent infinite reject→retry loops ---
        MAX_REJECT_RETRIES = 3
        if retry and node.retry_count >= MAX_REJECT_RETRIES:
            logger.warning(
                "Node {} has been retried {} times (max {}), forcing abandon",
                node_id, node.retry_count, MAX_REJECT_RETRIES,
            )
            retry = False

        if retry:
            from onemancompany.core.vessel import employee_manager as em
            if node.employee_id not in em.executors:
                return {"status": "error", "message": f"No handle for employee {node.employee_id}, cannot push correction task."}

        node.acceptance_result = {"passed": False, "notes": reason}
        if _is_standard_v2(tree) and bool(node.task_key):
            from datetime import datetime

            review_node = tree.get_node(task_id)
            node.acceptance_audit = {
                "decision": "rejected",
                "decided_by": str(vessel.employee_id),
                "decided_via": "reject_child",
                "review_node_id": review_node.id if review_node and review_node.node_type == NodeType.REVIEW else "",
                "decided_at": datetime.now().astimezone().isoformat(),
                "criteria_results": list(criteria_results or []),
                "evidence_refs": [str(ref) for ref in (evidence_refs or [])],
                "notes": reason,
            }

        if retry:
            # Reset to pending and re-schedule — keep original description, add rejection as directive
            node.load_content(project_dir)
            node.set_status(TaskPhase.PENDING)
            node.retry_count += 1
            node.result = ""
            from datetime import datetime
            new_directives = list(node.directives)
            new_directives.append({
                "from": vessel.employee_id,
                "directive": (
                    f"[CORRECTION] Your previous result was rejected.\n"
                    f"Reason: {reason}\n"
                    f"Acceptance criteria:\n" + "\n".join(f"- {c}" for c in node.acceptance_criteria)
                ),
                "at": datetime.now().isoformat(),
            })
            node.directives = new_directives  # reassign to trigger _content_dirty
            if _is_standard_v2(tree) and bool(node.task_key):
                _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
            else:
                _save_tree(project_dir, tree, tree_path_str)

            em.schedule_node(node.employee_id, node.id, tree_path_str)
            em._schedule_next(node.employee_id)

            return {"status": "rejected_retry", "node_id": node_id, "reason": reason}
        else:
            node.set_status(TaskPhase.FAILED)
            if _is_standard_v2(tree) and bool(node.task_key):
                _persist_tree_for_dispatch(project_dir, tree, tree_path_str)
            else:
                _save_tree(project_dir, tree, tree_path_str)

            from onemancompany.core.vessel import _trigger_dep_resolution
            _trigger_dep_resolution(project_dir, tree, node)

            return {"status": "rejected_failed", "node_id": node_id, "reason": reason}


@tool
def unblock_child(node_id: str, new_description: str = "") -> dict:
    """Unblock a BLOCKED task, optionally with updated instructions.

    Removes failed/cancelled dependencies from depends_on and re-evaluates.
    If remaining deps are met, schedules the task for execution.

    Args:
        node_id: The blocked task node ID.
        new_description: Updated task description (optional).
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir, tree_path_str)
        node = tree.get_node(node_id)
        if not node:  # pragma: no cover
            return {"status": "error", "message": f"Node {node_id} not found. Check dispatch_child() return value for correct node_id."}  # pragma: no cover
        if node.status != TaskPhase.BLOCKED.value:
            return {"status": "error", "message": f"Node {node_id} is {node.status}, not blocked."}

        # Remove failed/cancelled deps
        _terminal_bad = {TaskPhase.FAILED.value, TaskPhase.CANCELLED.value}
        node.depends_on = [
            d for d in node.depends_on
            if tree.get_node(d) and tree.get_node(d).status not in _terminal_bad
        ]
        if new_description:
            node.description = new_description
        node.set_status(TaskPhase.PENDING)
        _save_tree(project_dir, tree, tree_path_str)

        # Check if remaining deps are met
        from onemancompany.core.vessel import employee_manager
        if tree.all_deps_resolved(node.id):
            employee_manager.schedule_node(node.employee_id, node.id, tree_path_str)
            employee_manager._schedule_next(node.employee_id)
            return {"status": "unblocked_and_dispatched", "node_id": node_id}

        return {"status": "unblocked_waiting", "node_id": node_id,  # pragma: no cover
                "waiting_on": node.depends_on}  # pragma: no cover


@tool
def cancel_child(node_id: str, reason: str = "") -> dict:
    """Cancel a pending or running child task.

    Use this when a subtask is no longer needed (e.g. requirements changed,
    duplicate work, or parent task is being abandoned). Cancellation triggers
    dependency resolution — any tasks depending on this node will be blocked
    or resolved based on their fail_strategy.

    Cannot cancel tasks that are already finished, accepted, or cancelled.

    Args:
        node_id: The task node ID to cancel. Use read_node_detail() to inspect
            node state before cancelling.
        reason: Why the task is being cancelled (shown to the assigned employee).
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir, tree_path_str)
        node = tree.get_node(node_id)
        if not node:
            return {"status": "error", "message": f"Node {node_id} not found. Check dispatch_child() return value for correct node_id."}
        if node.is_resolved:
            return {"status": "error", "message": f"Node {node_id} already resolved ({node.status})."}

        node.set_status(TaskPhase.CANCELLED)
        node.result = reason or "Cancelled by parent"
        _save_tree(project_dir, tree, tree_path_str)

        from onemancompany.core.vessel import _trigger_dep_resolution
        _trigger_dep_resolution(project_dir, tree, node)

        return {"status": TaskPhase.CANCELLED.value, "node_id": node_id}


@tool
def set_project_name(name: str) -> dict:
    """Set the display name for the current project.

    Call this when you first receive a new CEO task to give it a descriptive name.

    Args:
        name: Short project name (2-6 words)
    """
    from onemancompany.core.vessel import _current_task_id

    task_id = _current_task_id.get()
    if not task_id:
        return {"status": "error", "message": "No agent context."}

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    # Resolve project root (project_dir is typically an iteration subdir)
    import yaml
    root = _resolve_project_root(project_dir)
    if root is None:
        logger.warning("set_project_name: no project.yaml found from {}", project_dir)
        return {"status": "error", "message": "Project file not found."}

    project_yaml = root / PROJECT_YAML_FILENAME
    data = yaml.safe_load(read_text_utf(project_yaml)) or {}
    data["name"] = name.strip()
    write_text_utf(project_yaml, yaml.dump(data, allow_unicode=True, sort_keys=False))
    return {"status": "ok", "name": name.strip()}


@tool
def create_project(task: str, mode: str = "standard") -> dict:
    """Create a new project from a CEO task description.

    Use this when the CEO gives a task that requires team execution
    (development, research, hiring, marketing, etc.). Do NOT use this
    for simple questions or conversations — just answer those directly.

    This creates a project with a task tree and schedules EA to analyze
    and dispatch the work to appropriate team members.

    Args:
        task: The full task description from the CEO.
        mode: "standard" (with retrospective) or "simple" (no retrospective).
    """
    import asyncio
    from pathlib import Path
    from onemancompany.core.config import CEO_ID, EA_ID, TASK_TREE_FILENAME
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.vessel import employee_manager

    if not task:
        return {"status": "error", "message": "Task description is required"}
    if mode not in ("simple", "standard"):
        mode = "standard"

    try:
        from onemancompany.core.project_archive import (
            async_create_project_from_task,
            get_project_dir,
        )

        # Create project (sync wrapper for async function)
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # No event loop — will use asyncio.run() below

        if loop and loop.is_running():  # pragma: no cover
            # We're in an async context — use a thread to run the async function
            import concurrent.futures  # pragma: no cover
            with concurrent.futures.ThreadPoolExecutor() as pool:  # pragma: no cover
                pid, iter_id = pool.submit(  # pragma: no cover
                    lambda: asyncio.run(async_create_project_from_task(task, "pending"))
                ).result(timeout=30)
        else:
            pid, iter_id = asyncio.run(async_create_project_from_task(task, "pending"))

        pdir = get_project_dir(pid)
        ctx_id = f"{pid}/{iter_id}" if iter_id else pid

        # Build task tree
        tree_path = Path(pdir) / TASK_TREE_FILENAME
        tree = TaskTree(project_id=ctx_id, mode=mode)
        ceo_root = tree.create_root(employee_id=CEO_ID, description=task)
        ceo_root.node_type = NodeType.CEO_PROMPT
        ceo_root.set_status(TaskPhase.PROCESSING)

        ea_task = (
            f"CEO has assigned a new task. Please analyze and dispatch to the appropriate owner:\n\n"
            f"Task: {task}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )
        ea_node = tree.add_child(
            parent_id=ceo_root.id,
            employee_id=EA_ID,
            description=ea_task,
            acceptance_criteria=[],
        )

        from onemancompany.core.vessel import _save_project_tree
        _save_project_tree(pdir, tree)
        _add_to_project_team(pdir, CEO_ID)
        _add_to_project_team(pdir, EA_ID)

        # Create project conversation via ConversationService
        from onemancompany.core.conversation import get_conversation_service
        _conv_svc = get_conversation_service()
        _create_conv = _conv_svc.get_or_create_project_conversation(ctx_id, [CEO_ID, EA_ID])
        try:
            _running = asyncio.get_running_loop()
        except RuntimeError:
            _running = None
        if _running and _running.is_running():  # pragma: no cover
            import concurrent.futures  # pragma: no cover
            with concurrent.futures.ThreadPoolExecutor() as _pool:  # pragma: no cover
                _pool.submit(lambda: asyncio.run(_create_conv)).result(timeout=10)  # pragma: no cover
        else:
            asyncio.run(_create_conv)

        # Schedule EA
        employee_manager.schedule_node(EA_ID, ea_node.id, str(tree_path))
        employee_manager._schedule_next(EA_ID)

        logger.info("[create_project] Created project {} from EA chat", ctx_id)
        return {
            "status": "ok",
            "project_id": ctx_id,
            "message": f"Project created and assigned to EA for analysis. Project ID: {ctx_id}",
        }
    except Exception as e:
        logger.error("[create_project] Failed: {}", e)
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from onemancompany.core.tool_registry import tool_registry, ToolMeta

tool_registry.register(dispatch_child, ToolMeta(name="dispatch_child", category="base", side_effecting=True))
tool_registry.register(accept_child, ToolMeta(name="accept_child", category="base", side_effecting=True))
tool_registry.register(reject_child, ToolMeta(name="reject_child", category="base", side_effecting=True))
tool_registry.register(unblock_child, ToolMeta(name="unblock_child", category="base", side_effecting=True))
tool_registry.register(cancel_child, ToolMeta(name="cancel_child", category="base", side_effecting=True))
tool_registry.register(set_project_name, ToolMeta(name="set_project_name", category="base", side_effecting=True))
tool_registry.register(create_project, ToolMeta(name="create_project", category="base", side_effecting=True))
