"""TaskTree-first reconciliation for durable LangGraph checkpoints.

The reconciler never executes a graph and never treats checkpoint content as a
business fact. It only compares formal-v2 TaskNodes with checkpoint thread
existence, persists fail-closed holds/conflicts, and records orphan threads for
audit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.core.runtime_reconciliation import is_system_adhoc_checkpoint_thread
from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree, get_tree_lock, register_tree


@dataclass
class ReconciliationFinding:
    status: str
    reason: str
    checkpoint_thread_id: str
    node_id: str = ""
    tree_path: str = ""


@dataclass
class ReconciliationReport:
    scanned_trees: int = 0
    scanned_nodes: int = 0
    checkpoint_threads: int = 0
    resumable: int = 0
    missing: int = 0
    conflicts: int = 0
    orphans: int = 0
    findings: list[ReconciliationFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [asdict(item) for item in self.findings]
        return data


def _is_formal_v2(tree: TaskTree, node) -> bool:
    return (
        tree.mode == "standard"
        and int(getattr(tree, "workflow_contract_version", 1)) >= 2
        and bool(getattr(node, "task_key", ""))
    )


def _expected_thread_id(tree: TaskTree, node) -> str:
    if node.checkpoint_thread_id:
        return str(node.checkpoint_thread_id)
    raw_project = str(tree.project_id or node.project_id or "")
    if "/" in raw_project:
        project_id, iteration_id = raw_project.split("/", 1)
    else:
        project_id = raw_project or "unknown-project"
        iteration_id = "unknown-iteration"
    generation = max(1, int(getattr(node, "execution_generation", 1) or 1))
    return f"omc:{project_id}:{iteration_id}:{node.id}:g{generation}"


async def _checkpoint_thread_ids(storage: RuntimeStorage) -> set[str]:
    if storage.checkpointer is None:
        return set()
    threads: set[str] = set()
    async for checkpoint in storage.checkpointer.alist(None):
        config = checkpoint.config or {}
        configurable = config.get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        if thread_id:
            threads.add(thread_id)
    return threads


def _persist_tree(tree_path: Path, tree: TaskTree) -> None:
    register_tree(tree_path, tree)
    with get_tree_lock(tree_path):
        tree.save(tree_path)


async def reconcile_checkpoints(
    storage: RuntimeStorage,
    projects_dir: str | Path,
) -> ReconciliationReport:
    """Reconcile formal TaskNodes against SQLite checkpoints without executing.

    TaskTree remains authoritative:
    - processing + checkpoint: resumable (unchanged);
    - processing + no checkpoint: HOLDING, controlled recovery required;
    - finished + checkpoint marked active: conflict, thread must not run;
    - checkpoint with no formal TaskNode: orphan audit only.
    """
    projects_dir = Path(projects_dir).expanduser().resolve()
    report = ReconciliationReport()
    checkpoint_threads = await _checkpoint_thread_ids(storage)
    report.checkpoint_threads = len(checkpoint_threads)

    indexed_threads: dict[str, tuple[Path, TaskTree, Any]] = {}
    if projects_dir.exists():
        for tree_path in sorted(projects_dir.rglob("task_tree.yaml")):
            try:
                tree = TaskTree.load(tree_path)
            except Exception as exc:
                logger.warning("Checkpoint reconciler skipped corrupt tree {}: {}", tree_path, exc)
                continue
            report.scanned_trees += 1
            modified = False
            for node in tree._nodes.values():
                if not _is_formal_v2(tree, node):
                    continue
                report.scanned_nodes += 1
                thread_id = _expected_thread_id(tree, node)
                indexed_threads[thread_id] = (tree_path, tree, node)
                has_checkpoint = thread_id in checkpoint_threads

                if node.status == TaskPhase.PROCESSING.value:
                    if has_checkpoint:
                        report.resumable += 1
                    else:
                        node.checkpoint_thread_id = thread_id
                        node.set_status(TaskPhase.HOLDING)
                        node.hold_reason = "checkpoint_missing_controlled_recovery"
                        node.hold_started_at = datetime.now().astimezone().isoformat()
                        node.checkpoint_status = "missing"
                        node.execution_checkpoint = {
                            **dict(node.execution_checkpoint or {}),
                            "phase": "checkpoint_missing_controlled_recovery",
                            "reason": node.hold_reason,
                        }
                        modified = True
                        report.missing += 1
                        report.findings.append(ReconciliationFinding(
                            status="blocked",
                            reason=node.hold_reason,
                            checkpoint_thread_id=thread_id,
                            node_id=node.id,
                            tree_path=str(tree_path),
                        ))
                        await storage.record_recovery(
                            node_id=node.id,
                            tree_path=str(tree_path),
                            mode=tree.mode,
                            expected_status=TaskPhase.PROCESSING.value,
                            reason=node.hold_reason,
                            execution_generation=max(1, int(node.execution_generation or 1)),
                            checkpoint_thread_id=thread_id,
                            status="blocked",
                        )
                elif (
                    node.status == TaskPhase.FINISHED.value
                    and has_checkpoint
                    and str(node.checkpoint_status or "").lower()
                    in {"active", "waiting", "waiting_for_lease", "holding"}
                ):
                    node.checkpoint_status = "conflict"
                    node.execution_checkpoint = {
                        **dict(node.execution_checkpoint or {}),
                        "phase": "tasktree_terminal_checkpoint_conflict",
                        "reason": "tasktree_finished_checkpoint_active",
                    }
                    modified = True
                    report.conflicts += 1
                    report.findings.append(ReconciliationFinding(
                        status="conflict",
                        reason="tasktree_finished_checkpoint_active",
                        checkpoint_thread_id=thread_id,
                        node_id=node.id,
                        tree_path=str(tree_path),
                    ))
                    await storage.record_recovery(
                        node_id=node.id,
                        tree_path=str(tree_path),
                        mode=tree.mode,
                        expected_status=TaskPhase.FINISHED.value,
                        reason="tasktree_finished_checkpoint_active",
                        execution_generation=max(1, int(node.execution_generation or 1)),
                        checkpoint_thread_id=thread_id,
                        status="conflict",
                    )
            if modified:
                _persist_tree(tree_path, tree)

    # Ad-hoc system threads are intentionally not formal TaskNodes.
    formal_checkpoint_threads = {
        thread_id
        for thread_id in checkpoint_threads
        if thread_id.startswith("omc:") and not is_system_adhoc_checkpoint_thread(thread_id)
    }
    for thread_id in sorted(formal_checkpoint_threads - set(indexed_threads)):
        parts = thread_id.split(":")
        node_id = parts[-2] if len(parts) >= 2 else "unknown"
        report.orphans += 1
        report.findings.append(ReconciliationFinding(
            status="orphan",
            reason="checkpoint_without_tasktree_node",
            checkpoint_thread_id=thread_id,
            node_id=node_id,
        ))
        await storage.record_recovery(
            node_id=node_id,
            tree_path="",
            mode="standard",
            expected_status="task_node_exists",
            reason="checkpoint_without_tasktree_node",
            execution_generation=1,
            checkpoint_thread_id=thread_id,
            status="orphan",
        )

    return report
