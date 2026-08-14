"""Subprocess worker for crash/resume recovery integration tests."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.core.runtime_context import (
    reset_task_runtime_context,
    set_task_runtime_context,
)
from onemancompany.core.runtime_storage import RuntimeStorage, set_runtime_storage
from onemancompany.core.task_tree import TaskTree
from onemancompany.core.tool_registry import ToolMeta, execute_tool, tool_registry


NODE_ID = "recovery-node"
THREAD_ID = "omc:recovery-project:iter_001:recovery-node:g1"


def _increment(path: Path) -> int:
    count = int(path.read_text() or "0") if path.exists() else 0
    count += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count))
    return count


@tool
def recovery_side_effect(employee_id: str = "") -> dict:
    """Execute the durable recovery probe side effect exactly once."""
    count = _increment(Path(os.environ["RECOVERY_SIDE_EFFECT_COUNTER"]))
    return {"status": "success", "count": count, "employee_id": employee_id}


def _tree_path(data_root: Path) -> Path:
    return data_root / "projects" / "recovery-project" / "iterations" / "iter_001" / "task_tree.yaml"


def _ensure_tree(path: Path) -> None:
    if path.exists():
        return
    tree = TaskTree(
        project_id="recovery-project/iter_001",
        mode="standard",
        workflow_contract_version=2,
    )
    node = tree.create_root(employee_id="00006", description="formal recovery probe")
    original_id = node.id
    tree._nodes.pop(original_id)
    node.id = NODE_ID
    node.task_key = "recovery-probe"
    node.execution_generation = 1
    node.checkpoint_thread_id = THREAD_ID
    node.checkpoint_status = "active"
    node.status = "processing"
    tree._nodes[NODE_ID] = node
    tree.root_id = NODE_ID
    tree.save(path)


def _graph(storage: RuntimeStorage):
    graph = StateGraph(MessagesState)

    async def side_effect(_state: MessagesState):
        result = await execute_tool("00006", "recovery_side_effect", {})
        if result.get("status") != "success":
            raise RuntimeError(f"side effect did not complete: {result}")
        return {"messages": [AIMessage(content="side effect complete")]}

    async def finalize(_state: MessagesState):
        _increment(Path(os.environ["RECOVERY_FINAL_COUNTER"]))
        return {"messages": [AIMessage(content="finalize complete")]}

    graph.add_node("side_effect", side_effect)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "side_effect")
    graph.add_edge("side_effect", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=storage.checkpointer, interrupt_after=["side_effect"])


async def run(phase: str, data_root: Path, output: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    tree_path = _tree_path(data_root)
    _ensure_tree(tree_path)
    storage = RuntimeStorage(data_root / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    tool_registry.register(
        recovery_side_effect,
        ToolMeta(
            name="recovery_side_effect",
            category="base",
            side_effecting=True,
        ),
    )
    context_token = set_task_runtime_context({
        "node_id": NODE_ID,
        "employee_id": "00006",
        "project_id": "recovery-project",
        "iteration_id": "iter_001",
        "execution_generation": 1,
        "checkpoint_thread_id": THREAD_ID,
        "tree_path": str(tree_path),
    })
    graph = _graph(storage)
    config = {"configurable": {"thread_id": THREAD_ID}}

    if phase == "crash":
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="formal recovery task")]},
            config=config,
        )
        checkpoint = await storage.checkpointer.aget(config)
        ledger = await storage.fetchone(
            "SELECT status FROM tool_invocation_ledger WHERE node_id=? AND tool_name=?",
            (NODE_ID, "recovery_side_effect"),
        )
        output.write_text(json.dumps({
            "messages": len(result["messages"]),
            "checkpoint": checkpoint is not None,
            "ledger_status": ledger[0] if ledger else None,
        }))
        # Deliberately bypass finally/close to emulate process death after the
        # checkpoint and side-effect receipt have reached durable storage.
        os._exit(87)

    checkpoint_before = await storage.checkpointer.aget(config)
    result = await graph.ainvoke(None, config=config)
    messages = result["messages"]
    payload = {
        "checkpoint_before": checkpoint_before is not None,
        "human_messages": sum(1 for message in messages if isinstance(message, HumanMessage)),
        "side_effect_messages": sum(
            1 for message in messages if getattr(message, "content", "") == "side effect complete"
        ),
        "finalize_messages": sum(
            1 for message in messages if getattr(message, "content", "") == "finalize complete"
        ),
        "thread_id": THREAD_ID,
    }
    output.write_text(json.dumps(payload))
    reset_task_runtime_context(context_token)
    set_runtime_storage(None)
    await storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["crash", "resume"])
    parser.add_argument("data_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.phase, args.data_root, args.output))
