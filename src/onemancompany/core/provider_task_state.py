"""Durable TaskTree state bridge for ProviderGateway holding and recovery."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from onemancompany.core.config import DirtyCategory
from onemancompany.core.store import mark_dirty
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import get_tree, get_tree_lock, register_tree


PROVIDER_CAPACITY_HOLD_REASON = "provider_capacity"


class ProviderTaskStateBridge:
    """Project provider retry state into the authoritative TaskTree.

    ProviderGateway owns request attempts and retry timing. This bridge mirrors
    only the task-facing state needed for restart recovery and UI visibility;
    it never marks dispatch, tool side effects, acceptance, or completion.
    """

    def __init__(
        self,
        *,
        tree_path: str | Path,
        node_id: str,
        checkpoint_thread_id: str,
        storage: Any | None = None,
    ) -> None:
        self.tree_path = Path(tree_path)
        self.node_id = str(node_id)
        self.checkpoint_thread_id = str(checkpoint_thread_id)
        self.storage = storage

    def _load(self):
        tree = get_tree(self.tree_path)
        node = tree.get_node(self.node_id)
        if node is None:
            raise RuntimeError(f"provider task node {self.node_id!r} no longer exists")
        return tree, node

    def _save(self, tree) -> None:
        register_tree(str(self.tree_path), tree)
        with get_tree_lock(str(self.tree_path)):
            tree.save(self.tree_path)
        mark_dirty(DirtyCategory.ACTIVE_TASKS)

    async def on_holding(self, attempt: int, next_retry_at: str, _error: str) -> None:
        tree, node = self._load()
        now = datetime.now().astimezone().isoformat()
        if node.status != TaskPhase.HOLDING.value:
            node.set_status(TaskPhase.HOLDING)
        node.hold_reason = PROVIDER_CAPACITY_HOLD_REASON
        node.hold_started_at = node.hold_started_at or now
        node.next_retry_at = str(next_retry_at or "")
        node.checkpoint_status = "waiting_provider"
        node.last_checkpoint_at = now
        execution_checkpoint = dict(node.execution_checkpoint or {})
        execution_checkpoint.update({
            "phase": "waiting_provider_capacity",
            "provider_attempt": int(attempt),
            "next_retry_at": node.next_retry_at,
            "checkpoint_thread_id": self.checkpoint_thread_id,
        })
        node.execution_checkpoint = execution_checkpoint
        node.result = "Provider capacity unavailable; execution is holding for durable retry"
        self._save(tree)
        if self.storage is not None:
            await self.storage.append_audit("provider_capacity_holding", {
                "node_id": self.node_id,
                "checkpoint_thread_id": self.checkpoint_thread_id,
                "attempt": int(attempt),
                "next_retry_at": node.next_retry_at,
            })

    async def on_recovered(self, attempt: int) -> None:
        tree, node = self._load()
        if (
            node.status != TaskPhase.HOLDING.value
            or node.hold_reason != PROVIDER_CAPACITY_HOLD_REASON
        ):
            return
        node.set_status(TaskPhase.PROCESSING)
        node.hold_reason = ""
        node.hold_started_at = ""
        node.next_retry_at = ""
        node.checkpoint_status = "active"
        node.last_checkpoint_at = datetime.now().astimezone().isoformat()
        execution_checkpoint = dict(node.execution_checkpoint or {})
        execution_checkpoint.update({
            "phase": "provider_capacity_recovered",
            "provider_attempt": int(attempt),
            "next_retry_at": None,
            "checkpoint_thread_id": self.checkpoint_thread_id,
        })
        node.execution_checkpoint = execution_checkpoint
        node.result = "Provider capacity recovered; continuing the same checkpoint thread"
        self._save(tree)
        if self.storage is not None:
            await self.storage.append_audit("provider_capacity_recovered", {
                "node_id": self.node_id,
                "checkpoint_thread_id": self.checkpoint_thread_id,
                "attempt": int(attempt),
            })
