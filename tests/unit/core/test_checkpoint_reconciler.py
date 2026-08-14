from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.core.checkpoint_reconciler import reconcile_checkpoints
from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree, evict_tree


def _formal_tree(path, project_iteration: str, node_id: str, status: str, checkpoint_status: str):
    tree = TaskTree(
        project_id=project_iteration,
        mode="standard",
        workflow_contract_version=2,
    )
    node = tree.create_root(employee_id="00006", description=f"work {node_id}")
    original_id = node.id
    if original_id != node_id:
        tree._nodes.pop(original_id)
        node.id = node_id
        tree._nodes[node_id] = node
        tree.root_id = node_id
    node.task_key = f"task-{node_id}"
    node.execution_generation = 1
    project_id, iteration_id = project_iteration.split("/", 1)
    node.checkpoint_thread_id = f"omc:{project_id}:{iteration_id}:{node_id}:g1"
    node.status = status
    node.checkpoint_status = checkpoint_status
    tree.save(path)
    evict_tree(path)
    return node.checkpoint_thread_id


async def _write_checkpoint(storage: RuntimeStorage, thread_id: str):
    graph = StateGraph(MessagesState)

    async def finish(_state: MessagesState):
        return {"messages": [AIMessage(content="checkpointed")]}

    graph.add_node("finish", finish)
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    compiled = graph.compile(checkpointer=storage.checkpointer)
    await compiled.ainvoke(
        {"messages": [HumanMessage(content="formal task")]},
        config={"configurable": {"thread_id": thread_id}},
    )


@pytest.mark.asyncio
async def test_checkpoint_reconciler_enforces_tasktree_first_matrix_and_records_orphan(tmp_path):
    projects = tmp_path / "projects"
    with_cp_path = projects / "project-a" / "iterations" / "iter_001" / "task_tree.yaml"
    missing_path = projects / "project-b" / "iterations" / "iter_001" / "task_tree.yaml"
    finished_path = projects / "project-c" / "iterations" / "iter_001" / "task_tree.yaml"

    with_cp_thread = _formal_tree(
        with_cp_path, "project-a/iter_001", "node-with-cp", "processing", "active"
    )
    _formal_tree(
        missing_path, "project-b/iter_001", "node-missing", "processing", "active"
    )
    finished_thread = _formal_tree(
        finished_path, "project-c/iter_001", "node-finished", "finished", "active"
    )
    orphan_thread = "omc:orphan-project:iter_001:orphan-node:g1"

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        await _write_checkpoint(storage, with_cp_thread)
        await _write_checkpoint(storage, finished_thread)
        await _write_checkpoint(storage, orphan_thread)
        await _write_checkpoint(storage, "omc:system:adhoc:probe:g1")

        report = await reconcile_checkpoints(storage, projects)
        assert report.resumable == 1
        assert report.missing == 1
        assert report.conflicts == 1
        assert report.orphans == 1

        resumable = TaskTree.load(with_cp_path).get_node("node-with-cp")
        assert resumable.status == TaskPhase.PROCESSING.value
        assert resumable.checkpoint_status == "active"

        missing = TaskTree.load(missing_path).get_node("node-missing")
        assert missing.status == TaskPhase.HOLDING.value
        assert missing.hold_reason == "checkpoint_missing_controlled_recovery"
        assert missing.checkpoint_status == "missing"

        finished = TaskTree.load(finished_path).get_node("node-finished")
        assert finished.status == TaskPhase.FINISHED.value
        assert finished.checkpoint_status == "conflict"

        recoveries = await storage.fetchall(
            "SELECT status,reason,checkpoint_thread_id FROM recoveries ORDER BY status"
        )
        assert {row[0] for row in recoveries} == {"blocked", "conflict", "orphan"}
        assert any(row[2] == orphan_thread and row[0] == "orphan" for row in recoveries)

        second = await reconcile_checkpoints(storage, projects)
        assert second.missing == 0
        assert second.conflicts == 0
        count = await storage.fetchone("SELECT COUNT(*) FROM recoveries")
        assert count[0] == 3
    finally:
        await storage.close()
