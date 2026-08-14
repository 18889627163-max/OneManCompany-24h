from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.core.checkpoint_reconciler import reconcile_checkpoints
from onemancompany.core.runtime_storage import RuntimeStorage
from onemancompany.core.task_tree import TaskTree


@pytest.mark.asyncio
async def test_reconciler_reads_checkpoint_created_by_previous_storage_lifecycle(tmp_path):
    projects = tmp_path / "projects"
    tree_path = projects / "restart-project" / "iterations" / "iter_001" / "task_tree.yaml"
    tree = TaskTree(
        project_id="restart-project/iter_001",
        mode="standard",
        workflow_contract_version=2,
    )
    node = tree.create_root(employee_id="00006", description="restart recovery")
    node.task_key = "restart-recovery"
    node.status = "processing"
    node.execution_generation = 1
    node.checkpoint_thread_id = f"omc:restart-project:iter_001:{node.id}:g1"
    node.checkpoint_status = "active"
    tree.save(tree_path)

    db_path = tmp_path / "runtime.sqlite3"
    first = RuntimeStorage(db_path)
    await first.initialize()
    graph = StateGraph(MessagesState)

    async def persist(state: MessagesState):
        return state

    graph.add_node("persist", persist)
    graph.add_edge(START, "persist")
    graph.add_edge("persist", END)
    compiled = graph.compile(checkpointer=first.checkpointer)
    await compiled.ainvoke(
        {"messages": [HumanMessage(content="resume me")]},
        config={"configurable": {"thread_id": node.checkpoint_thread_id}},
    )
    await first.close()

    second = RuntimeStorage(db_path)
    await second.initialize()
    try:
        report = await reconcile_checkpoints(second, projects)
        assert report.resumable == 1
        assert report.missing == 0
        persisted = TaskTree.load(tree_path).get_node(node.id)
        assert persisted.status == "processing"
        assert persisted.checkpoint_status == "active"
    finally:
        await second.close()
