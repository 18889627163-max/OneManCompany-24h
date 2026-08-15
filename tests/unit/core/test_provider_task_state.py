from __future__ import annotations

import pytest

from onemancompany.core.provider_task_state import (
    PROVIDER_CAPACITY_HOLD_REASON,
    ProviderTaskStateBridge,
)
from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree


@pytest.mark.asyncio
async def test_provider_holding_and_recovery_are_persisted_without_changing_thread(tmp_path):
    tree_path = tmp_path / "task_tree.yaml"
    tree = TaskTree("provider-gate/iter_001")
    node = tree.create_root("00006", "Exercise provider recovery")
    node.task_key = "provider-gate"
    node.set_status(TaskPhase.PROCESSING)
    node.execution_generation = 1
    node.checkpoint_thread_id = f"omc:provider-gate:iter_001:{node.id}:g1"
    node.checkpoint_status = "active"
    node.execution_checkpoint = {
        "phase": "tool_completed",
        "side_effects_confirmed": ["effect-1"],
    }
    tree.save(tree_path)

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    bridge = ProviderTaskStateBridge(
        tree_path=tree_path,
        node_id=node.id,
        checkpoint_thread_id=node.checkpoint_thread_id,
        storage=storage,
    )
    try:
        await bridge.on_holding(
            2,
            "2026-08-14T12:00:30+08:00",
            "HTTP 429 body must not be copied into TaskTree",
        )
        held = TaskTree.load(tree_path).get_node(node.id)
        assert held.status == "holding"
        assert held.hold_reason == PROVIDER_CAPACITY_HOLD_REASON
        assert held.next_retry_at == "2026-08-14T12:00:30+08:00"
        assert held.checkpoint_status == "waiting_provider"
        assert held.checkpoint_thread_id == node.checkpoint_thread_id
        assert held.execution_checkpoint["provider_attempt"] == 2
        assert held.execution_checkpoint["side_effects_confirmed"] == ["effect-1"]
        assert "HTTP 429" not in held.result

        await bridge.on_recovered(2)
        recovered = TaskTree.load(tree_path).get_node(node.id)
        assert recovered.status == "processing"
        assert recovered.hold_reason == ""
        assert recovered.next_retry_at == ""
        assert recovered.checkpoint_status == "active"
        assert recovered.checkpoint_thread_id == node.checkpoint_thread_id
        assert recovered.execution_checkpoint["phase"] == "provider_capacity_recovered"
        assert recovered.execution_checkpoint["side_effects_confirmed"] == ["effect-1"]

        audit_rows = await storage.fetchall(
            "SELECT event_type FROM audit_events ORDER BY sequence"
        )
        assert [row[0] for row in audit_rows] == [
            "provider_capacity_holding",
            "provider_capacity_recovered",
        ]
    finally:
        await storage.close()
