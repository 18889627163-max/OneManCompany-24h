"""Subprocess worker for the isolated standard-v2 real-service recovery gate.

Each invocation creates a real RuntimeStorage/AsyncSqliteSaver lifecycle.  The
``crash`` phase deliberately terminates with ``os._exit(87)`` only after the
selected graph node and its LangGraph checkpoint have reached durable storage.
The ``resume`` phase starts a fresh process and continues the same thread.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.agents.tree_tools import accept_child
from onemancompany.core.runtime_context import reset_task_runtime_context, set_task_runtime_context
from onemancompany.core.runtime_storage import RuntimeStorage, set_runtime_storage
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree, get_tree, get_tree_lock, register_tree
from onemancompany.core.tool_registry import ToolMeta, execute_tool, tool_registry
from onemancompany.core.vessel import (
    ScheduleEntry,
    _current_task_id,
    _current_vessel,
    employee_manager,
)

PROJECT_ID = "recovery-drill-20260815"
ITERATION_ID = "iter_001"
PARENT_ID = "a00000000001"
EMPLOYEE_ID = "00006"
REVIEWER_ID = "00003"
SCENARIOS = {
    "dispatch": "b00000000001",
    "executor_started": "b00000000002",
    "side_effect": "b00000000003",
}
STAGES = ("dispatch", "executor_started", "side_effect")


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _thread_id(node_id: str) -> str:
    return f"omc:{PROJECT_ID}:{ITERATION_ID}:{node_id}:g1"


def _tree_path(data_root: Path) -> Path:
    return (
        data_root
        / "company"
        / "business"
        / "projects"
        / PROJECT_ID
        / "iterations"
        / ITERATION_ID
        / "task_tree.yaml"
    )


def _replace_id(tree: TaskTree, node: Any, node_id: str) -> None:
    old_id = node.id
    tree._nodes.pop(old_id)
    node.id = node_id
    tree._nodes[node_id] = node
    if tree.root_id == old_id:
        tree.root_id = node_id


def _save_tree(path: Path, tree: TaskTree) -> None:
    register_tree(path, tree)
    with get_tree_lock(path):
        tree.save(path)


def _ensure_tree(path: Path) -> None:
    if path.exists():
        return
    tree = TaskTree(
        project_id=f"{PROJECT_ID}/{ITERATION_ID}",
        mode="standard",
        workflow_contract_version=2,
    )
    parent = tree.create_root(REVIEWER_ID, "Review the isolated service recovery drill")
    _replace_id(tree, parent, PARENT_ID)
    parent.node_type = NodeType.REVIEW.value
    parent.status = TaskPhase.PROCESSING.value
    parent.task_key = "recovery-drill-review"
    parent.execution_generation = 1

    for scenario, node_id in SCENARIOS.items():
        child = tree.add_child(
            PARENT_ID,
            EMPLOYEE_ID,
            f"Recover exactly once after the {scenario} durability boundary",
            [
                "resume the same checkpoint thread",
                "do not duplicate dispatch, executor receipt, or side effect",
                "finish only after durable reconciliation",
            ],
            title=f"Recovery drill: {scenario}",
        )
        old_id = child.id
        tree._nodes.pop(old_id)
        parent.children_ids = [node_id if item == old_id else item for item in parent.children_ids]
        child.id = node_id
        child.task_key = f"recovery-{scenario}"
        child.dispatch_request_fingerprint = _dispatch_fingerprint(scenario, node_id)
        child.execution_generation = 1
        child.checkpoint_thread_id = _thread_id(node_id)
        child.checkpoint_status = "new"
        child.execution_checkpoint = {"phase": "created", "scenario": scenario}
        tree._nodes[node_id] = child
    _save_tree(path, tree)


def _dispatch_fingerprint(scenario: str, node_id: str) -> str:
    payload = json.dumps(
        {"scenario": scenario, "node_id": node_id, "employee_id": EMPLOYEE_ID},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _counter_path(data_root: Path, scenario: str) -> Path:
    return data_root / "evidence" / f"{scenario}-side-effect-counter.txt"


def _increment_counter(path: Path) -> int:
    value = int(path.read_text(encoding="utf-8") or "0") if path.exists() else 0
    value += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")
    return value


@tool
def recovery_business_side_effect(
    scenario: str,
    idempotency_key: str,
    employee_id: str = "",
) -> dict[str, Any]:
    """Execute the isolated recovery drill's externally visible side effect."""
    data_root = Path(os.environ["OMC_RECOVERY_GATE_DATA_ROOT"])
    count = _increment_counter(_counter_path(data_root, scenario))
    return {
        "status": "success",
        "scenario": scenario,
        "idempotency_key": idempotency_key,
        "employee_id": employee_id,
        "external_count": count,
    }


def _load_node(tree_path: Path, node_id: str):
    tree = get_tree(tree_path)
    node = tree.get_node(node_id)
    if node is None:
        raise RuntimeError(f"drill node {node_id} is missing")
    return tree, node


async def _dispatch_stage(storage: RuntimeStorage, tree_path: Path, scenario: str, node_id: str):
    tree, node = _load_node(tree_path, node_id)
    fingerprint = node.dispatch_request_fingerprint
    intent = await storage.prepare_dispatch_intent(
        parent_id=PARENT_ID,
        employee_id=EMPLOYEE_ID,
        task_key=node.task_key,
        request_fingerprint=fingerprint,
    )
    receipt = {
        "node_id": node_id,
        "task_key": node.task_key,
        "tree_persisted": True,
        "scheduler_registered": True,
        "executor_started": False,
        "checkpoint_thread_id": node.checkpoint_thread_id,
        "recorded_at": _now(),
    }
    prior_state = intent["state"]
    intent = await storage.advance_dispatch_intent(
        parent_id=PARENT_ID,
        employee_id=EMPLOYEE_ID,
        task_key=node.task_key,
        request_fingerprint=fingerprint,
        state="scheduled",
        node_id=node_id,
        receipt=receipt,
    )
    node.status = TaskPhase.PROCESSING.value
    node.dispatch_verification = receipt
    node.checkpoint_status = "active"
    node.last_checkpoint_at = _now()
    node.execution_checkpoint = {
        **dict(node.execution_checkpoint or {}),
        "phase": "dispatch_persisted",
        "dispatch_state": intent["state"],
    }
    _save_tree(tree_path, tree)
    if prior_state == "prepared" and intent["state"] == "scheduled":
        await storage.append_audit(
            "recovery_drill_dispatch_persisted",
            {"scenario": scenario, "node_id": node_id, "thread_id": node.checkpoint_thread_id},
        )
    return {"messages": [AIMessage(content=f"{scenario}: dispatch persisted")]}


async def _executor_stage(storage: RuntimeStorage, tree_path: Path, scenario: str, node_id: str):
    tree, node = _load_node(tree_path, node_id)
    intent = await storage.get_dispatch_intent(PARENT_ID, EMPLOYEE_ID, node.task_key)
    if intent is None:
        raise RuntimeError("executor boundary reached without dispatch intent")
    was_started = intent["state"] == "started"
    receipt = {
        **dict(intent.get("receipt") or {}),
        "executor_started": True,
        "executor_started_at": (intent.get("receipt") or {}).get("executor_started_at") or _now(),
        "checkpoint_thread_id": node.checkpoint_thread_id,
    }
    intent = await storage.advance_dispatch_intent(
        parent_id=PARENT_ID,
        employee_id=EMPLOYEE_ID,
        task_key=node.task_key,
        request_fingerprint=node.dispatch_request_fingerprint,
        state="started",
        node_id=node_id,
        receipt=receipt,
    )
    node.dispatch_verification = receipt
    node.last_checkpoint_at = _now()
    node.execution_checkpoint = {
        **dict(node.execution_checkpoint or {}),
        "phase": "executor_started",
        "dispatch_state": intent["state"],
        "executor_started": True,
    }
    _save_tree(tree_path, tree)
    if not was_started:
        await storage.append_audit(
            "recovery_drill_executor_started",
            {"scenario": scenario, "node_id": node_id, "thread_id": node.checkpoint_thread_id},
        )
    return {"messages": [AIMessage(content=f"{scenario}: executor started")]}


async def _side_effect_stage(storage: RuntimeStorage, tree_path: Path, scenario: str, node_id: str):
    result = await execute_tool(
        EMPLOYEE_ID,
        "recovery_business_side_effect",
        {"scenario": scenario, "idempotency_key": f"recovery:{scenario}"},
    )
    if result.get("status") != "success":
        raise RuntimeError(f"side effect did not complete: {result}")
    tree, node = _load_node(tree_path, node_id)
    node.last_checkpoint_at = _now()
    node.execution_checkpoint = {
        **dict(node.execution_checkpoint or {}),
        "phase": "side_effect_completed",
        "side_effects_confirmed": [f"ledger:{node_id}:recovery_business_side_effect"],
        "external_count": result.get("external_count"),
    }
    _save_tree(tree_path, tree)
    await storage.append_audit(
        "recovery_drill_side_effect_completed",
        {"scenario": scenario, "node_id": node_id, "thread_id": node.checkpoint_thread_id},
    )
    return {"messages": [AIMessage(content=f"{scenario}: side effect completed")]}


async def _complete_stage(storage: RuntimeStorage, tree_path: Path, scenario: str, node_id: str):
    tree, node = _load_node(tree_path, node_id)
    node.status = TaskPhase.COMPLETED.value
    node.result = f"Recovery drill {scenario} completed with durable reconciliation evidence."
    node.checkpoint_status = "completed"
    node.last_checkpoint_at = _now()
    node.execution_checkpoint = {
        **dict(node.execution_checkpoint or {}),
        "phase": "completed",
        "completed_at": _now(),
    }
    _save_tree(tree_path, tree)
    await storage.enqueue_memory_outbox(
        namespace=("employee", EMPLOYEE_ID, "episodic"),
        memory_key=f"recovery-drill:{node_id}:episodic",
        payload={
            "event_type": "task_completed",
            "source_project_id": PROJECT_ID,
            "source_iteration_id": ITERATION_ID,
            "source_node_id": node_id,
            "source_thread_id": node.checkpoint_thread_id,
            "scenario": scenario,
            "status": "candidate",
        },
        event_id=f"memory-{node_id}",
    )
    return {"messages": [AIMessage(content=f"{scenario}: task completed")]}


def _build_graph(storage: RuntimeStorage, tree_path: Path, scenario: str, node_id: str, interrupt_after: str | None):
    graph = StateGraph(MessagesState)

    async def dispatch(_state: MessagesState):
        return await _dispatch_stage(storage, tree_path, scenario, node_id)

    async def executor_started(_state: MessagesState):
        return await _executor_stage(storage, tree_path, scenario, node_id)

    async def side_effect(_state: MessagesState):
        return await _side_effect_stage(storage, tree_path, scenario, node_id)

    async def complete(_state: MessagesState):
        return await _complete_stage(storage, tree_path, scenario, node_id)

    graph.add_node("dispatch", dispatch)
    graph.add_node("executor_started", executor_started)
    graph.add_node("side_effect", side_effect)
    graph.add_node("complete", complete)
    graph.add_edge(START, "dispatch")
    graph.add_edge("dispatch", "executor_started")
    graph.add_edge("executor_started", "side_effect")
    graph.add_edge("side_effect", "complete")
    graph.add_edge("complete", END)
    kwargs = {"checkpointer": storage.checkpointer}
    if interrupt_after:
        kwargs["interrupt_after"] = [interrupt_after]
    return graph.compile(**kwargs)


async def _snapshot(storage: RuntimeStorage, tree_path: Path, scenario: str) -> dict[str, Any]:
    node_id = SCENARIOS[scenario]
    tree = TaskTree.load(tree_path, skeleton_only=False)
    node = tree.get_node(node_id)
    intent = await storage.get_dispatch_intent(PARENT_ID, EMPLOYEE_ID, f"recovery-{scenario}")
    ledger = await storage.fetchone(
        "SELECT status,result_reference,result_json FROM tool_invocation_ledger "
        "WHERE node_id=? AND execution_generation=1 AND tool_name='recovery_business_side_effect'",
        (node_id,),
    )
    checkpoint_count = await storage.fetchone(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (_thread_id(node_id),)
    )
    audit_rows = await storage.fetchall(
        "SELECT event_type,COUNT(*) FROM audit_events WHERE event_type LIKE 'recovery_drill_%' "
        "AND json_extract(event_data,'$.node_id')=? GROUP BY event_type ORDER BY event_type",
        (node_id,),
    )
    counter = _counter_path(Path(os.environ["OMC_RECOVERY_GATE_DATA_ROOT"]), scenario)
    return {
        "scenario": scenario,
        "node": node.to_dict() if node else None,
        "dispatch_intent": intent,
        "tool_ledger": {
            "status": str(ledger[0]),
            "result_reference": str(ledger[1]) if ledger[1] else None,
            "result": json.loads(str(ledger[2])) if ledger and ledger[2] else None,
        } if ledger else None,
        "checkpoint_count": int(checkpoint_count[0]) if checkpoint_count else 0,
        "audit_counts": {str(row[0]): int(row[1]) for row in audit_rows},
        "external_side_effect_count": int(counter.read_text(encoding="utf-8")) if counter.exists() else 0,
    }


def _explicit_accept(tree_path: Path, scenario: str) -> dict[str, Any]:
    node_id = SCENARIOS[scenario]
    tree = TaskTree.load(tree_path, skeleton_only=False)
    register_tree(tree_path, tree)
    employee_manager._current_entries["recovery-gate-review"] = ScheduleEntry(
        node_id=PARENT_ID,
        tree_path=str(tree_path),
    )
    vessel_token = _current_vessel.set(SimpleNamespace(employee_id=REVIEWER_ID))
    task_token = _current_task_id.set(PARENT_ID)
    try:
        return accept_child.invoke({
            "node_id": node_id,
            "notes": f"Explicitly accepted after {scenario} recovery reconciliation.",
            "criteria_results": [
                {"criterion": "same checkpoint thread", "passed": True},
                {"criterion": "receipts and side effect exactly once", "passed": True},
            ],
            "evidence_refs": [
                f"checkpoint:{_thread_id(node_id)}",
                f"dispatch_intent:{PARENT_ID}:{EMPLOYEE_ID}:recovery-{scenario}",
                f"tool_ledger:{node_id}:recovery_business_side_effect",
            ],
        })
    finally:
        _current_task_id.reset(task_token)
        _current_vessel.reset(vessel_token)
        employee_manager._current_entries.pop("recovery-gate-review", None)


async def run(phase: str, scenario: str, data_root: Path, output: Path) -> None:
    data_root = data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ["OMC_RECOVERY_GATE_DATA_ROOT"] = str(data_root)
    tree_path = _tree_path(data_root)
    _ensure_tree(tree_path)
    storage = RuntimeStorage(data_root / "data" / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    tool_registry.register(
        recovery_business_side_effect,
        ToolMeta(name="recovery_business_side_effect", category="base", side_effecting=True),
    )
    node_id = SCENARIOS[scenario]
    thread_id = _thread_id(node_id)
    context_token = set_task_runtime_context({
        "node_id": node_id,
        "employee_id": EMPLOYEE_ID,
        "project_id": PROJECT_ID,
        "iteration_id": ITERATION_ID,
        "execution_generation": 1,
        "checkpoint_thread_id": thread_id,
        "tree_path": str(tree_path),
    })
    config = {"configurable": {"thread_id": thread_id}}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if phase == "crash":
            graph = _build_graph(storage, tree_path, scenario, node_id, scenario)
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=f"Run recovery scenario {scenario}")]},
                config=config,
            )
            payload = {
                "phase": phase,
                "scenario": scenario,
                "thread_id": thread_id,
                "human_messages": sum(isinstance(msg, HumanMessage) for msg in result["messages"]),
                "snapshot": await _snapshot(storage, tree_path, scenario),
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # Intentionally bypass close/finally after the checkpoint has been saved.
            os._exit(87)

        graph = _build_graph(storage, tree_path, scenario, node_id, None)
        checkpoint_before = await storage.checkpointer.aget(config)
        result = await graph.ainvoke(None, config=config)
        acceptance = _explicit_accept(tree_path, scenario)
        payload = {
            "phase": phase,
            "scenario": scenario,
            "thread_id": thread_id,
            "checkpoint_before": checkpoint_before is not None,
            "human_messages": sum(isinstance(msg, HumanMessage) for msg in result["messages"]),
            "assistant_messages": sum(isinstance(msg, AIMessage) for msg in result["messages"]),
            "acceptance": acceptance,
            "snapshot": await _snapshot(storage, tree_path, scenario),
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        reset_task_runtime_context(context_token)
        set_runtime_storage(None)
        await storage.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["crash", "resume"])
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("data_root", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.phase, arguments.scenario, arguments.data_root, arguments.output))
