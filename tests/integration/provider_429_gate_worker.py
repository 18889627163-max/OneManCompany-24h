"""Subprocess worker for the isolated real HTTP Provider 429 recovery gate."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.agents.base import GatewayChatModel
from onemancompany.core.provider_gateway import ProviderGateway, set_provider_gateway
from onemancompany.core.provider_task_state import ProviderTaskStateBridge
from onemancompany.core.runtime_context import reset_task_runtime_context, set_task_runtime_context
from onemancompany.core.runtime_storage import RuntimeStorage, set_runtime_storage
from onemancompany.core.task_tree import TaskTree

PROJECT_ID = "provider-gate"
ITERATION_ID = "iter_001"
NODE_ID = "provider-gate-node"
PARENT_ID = "provider-gate-parent"
EMPLOYEE_ID = "00006"
THREAD_ID = f"omc:{PROJECT_ID}:{ITERATION_ID}:{NODE_ID}:g1"
DISPATCH_KEY = "provider-gate-child"
DISPATCH_FINGERPRINT = hashlib.sha256(b"provider-gate-dispatch-v1").hexdigest()
TOOL_NAME = "provider_gate_side_effect"
TOOL_KEY = "provider-gate-side-effect-v1"
TOOL_FINGERPRINT = hashlib.sha256(b"provider-gate-side-effect-v1").hexdigest()


def _tree_path(data_root: Path) -> Path:
    return data_root / "projects" / PROJECT_ID / "iterations" / ITERATION_ID / "task_tree.yaml"


def _ensure_tree(path: Path) -> None:
    if path.exists():
        return
    tree = TaskTree(
        project_id=f"{PROJECT_ID}/{ITERATION_ID}",
        mode="standard",
        workflow_contract_version=2,
    )
    node = tree.create_root(employee_id=EMPLOYEE_ID, description="isolated provider HTTP 429 gate")
    original_id = node.id
    tree._nodes.pop(original_id)
    node.id = NODE_ID
    node.title = "Provider 429 recovery gate"
    node.task_key = "provider-429-gate"
    node.execution_generation = 1
    node.checkpoint_thread_id = THREAD_ID
    node.checkpoint_status = "active"
    node.status = "processing"
    node.execution_checkpoint = {
        "phase": "provider_gate_started",
        "checkpoint_thread_id": THREAD_ID,
        "side_effects_confirmed": [],
    }
    tree._nodes[NODE_ID] = node
    tree.root_id = NODE_ID
    tree.save(path)


def _increment(path: Path) -> int:
    value = int(path.read_text(encoding="utf-8") or "0") if path.exists() else 0
    value += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")
    return value


async def _durable_side_effect(storage: RuntimeStorage, counter: Path) -> dict[str, Any]:
    intent = await storage.prepare_dispatch_intent(
        parent_id=PARENT_ID,
        employee_id=EMPLOYEE_ID,
        task_key=DISPATCH_KEY,
        request_fingerprint=DISPATCH_FINGERPRINT,
    )
    if intent["state"] != "started":
        await storage.advance_dispatch_intent(
            parent_id=PARENT_ID,
            employee_id=EMPLOYEE_ID,
            task_key=DISPATCH_KEY,
            request_fingerprint=DISPATCH_FINGERPRINT,
            state="started",
            node_id=NODE_ID,
            receipt={
                "dispatch_registered": True,
                "executor_started": True,
                "source": "isolated-provider-gate",
            },
        )

    invocation = await storage.prepare_tool_invocation(
        node_id=NODE_ID,
        execution_generation=1,
        tool_name=TOOL_NAME,
        tool_call_id="provider-gate-tool-call",
        business_idempotency_key=TOOL_KEY,
        request_fingerprint=TOOL_FINGERPRINT,
    )
    if invocation.get("replayed"):
        return dict(invocation.get("result") or {})

    count = _increment(counter)
    result = {"status": "success", "external_counter": count}
    await storage.complete_tool_invocation(
        node_id=NODE_ID,
        execution_generation=1,
        tool_name=TOOL_NAME,
        business_idempotency_key=TOOL_KEY,
        request_fingerprint=TOOL_FINGERPRINT,
        result=result,
        result_reference=str(counter),
    )
    return result


def _build_graph(storage: RuntimeStorage, model: GatewayChatModel, counter: Path):
    graph = StateGraph(MessagesState)

    async def side_effect(_state: MessagesState):
        result = await _durable_side_effect(storage, counter)
        return {"messages": [AIMessage(content=f"durable side effect {result['external_counter']}")]}

    async def chat(state: MessagesState):
        response = await model.ainvoke(state["messages"])
        return {"messages": [response]}

    graph.add_node("side_effect", side_effect)
    graph.add_node("chat", chat)
    graph.add_edge(START, "side_effect")
    graph.add_edge("side_effect", "chat")
    graph.add_edge("chat", END)
    return graph.compile(checkpointer=storage.checkpointer)


async def _snapshot(storage: RuntimeStorage, tree_path: Path) -> dict[str, Any]:
    tree = TaskTree.load(tree_path)
    node = tree.get_node(NODE_ID)
    provider = await storage.fetchone(
        "SELECT request_id,status,attempt,next_retry_at,last_error_class,priority "
        "FROM provider_queue WHERE node_id=? ORDER BY submitted_at LIMIT 1",
        (NODE_ID,),
    )
    retry = None
    if provider:
        retry = await storage.fetchone(
            "SELECT attempt,next_retry_at,last_error_class FROM provider_retry_state WHERE request_id=?",
            (provider[0],),
        )
    dispatch_count = await storage.fetchone(
        "SELECT COUNT(*) FROM dispatch_intents WHERE parent_id=? AND employee_id=? AND task_key=?",
        (PARENT_ID, EMPLOYEE_ID, DISPATCH_KEY),
    )
    tool_count = await storage.fetchone(
        "SELECT COUNT(*) FROM tool_invocation_ledger WHERE node_id=? AND execution_generation=1 "
        "AND tool_name=? AND business_idempotency_key=?",
        (NODE_ID, TOOL_NAME, TOOL_KEY),
    )
    checkpoint_count = await storage.fetchone(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (THREAD_ID,)
    )
    return {
        "node": {
            "status": node.status if node else None,
            "hold_reason": node.hold_reason if node else None,
            "checkpoint_status": node.checkpoint_status if node else None,
            "checkpoint_thread_id": node.checkpoint_thread_id if node else None,
            "next_retry_at": node.next_retry_at if node else None,
            "execution_checkpoint": dict(node.execution_checkpoint or {}) if node else {},
        },
        "provider": {
            "request_id": str(provider[0]) if provider else None,
            "status": str(provider[1]) if provider else None,
            "attempt": int(provider[2]) if provider else None,
            "next_retry_at": str(provider[3]) if provider and provider[3] else None,
            "last_error_class": str(provider[4]) if provider and provider[4] else None,
            "priority": int(provider[5]) if provider else None,
        },
        "retry": {
            "attempt": int(retry[0]) if retry else None,
            "next_retry_at": str(retry[1]) if retry and retry[1] else None,
            "last_error_class": str(retry[2]) if retry and retry[2] else None,
        },
        "dispatch_count": int(dispatch_count[0]) if dispatch_count else 0,
        "tool_ledger_count": int(tool_count[0]) if tool_count else 0,
        "checkpoint_count": int(checkpoint_count[0]) if checkpoint_count else 0,
    }


async def run(phase: str, data_root: Path, base_url: str, output: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    tree_path = _tree_path(data_root)
    _ensure_tree(tree_path)
    counter = data_root / "evidence" / "side-effect-counter.txt"
    storage = RuntimeStorage(data_root / "data" / "runtime.sqlite3")
    await storage.initialize()
    set_runtime_storage(storage)
    gateway = ProviderGateway(
        storage,
        default_concurrency=1,
        transient_retry_limit_for_call=0,
        max_backoff_seconds=1.0,
    )
    await gateway.start()
    set_provider_gateway(gateway)
    bridge = ProviderTaskStateBridge(
        tree_path=tree_path,
        node_id=NODE_ID,
        checkpoint_thread_id=THREAD_ID,
        storage=storage,
    )
    model = GatewayChatModel(
        delegate=ChatOpenAI(
            base_url=base_url,
            api_key="isolated-provider-gate-key",
            model="provider-gate-model",
            max_retries=0,
            timeout=10,
        ),
        provider_context={
            "provider": "isolated-openai-compatible-http",
            "credential_fingerprint": "isolated-provider-gate",
            "account_or_model_pool": "provider-gate-model",
        },
        priority=0,
    )
    graph = _build_graph(storage, model, counter)
    config = {"configurable": {"thread_id": THREAD_ID}}
    token = set_task_runtime_context({
        "node_id": NODE_ID,
        "employee_id": EMPLOYEE_ID,
        "project_id": PROJECT_ID,
        "iteration_id": ITERATION_ID,
        "execution_generation": 1,
        "checkpoint_thread_id": THREAD_ID,
        "tree_path": str(tree_path),
        "on_holding": bridge.on_holding,
        "on_recovered": bridge.on_recovered,
        "transient_retry_limit_for_call": 0,
    })
    payload: dict[str, Any] = {"phase": phase, "thread_id": THREAD_ID}
    try:
        if phase == "hold":
            try:
                await graph.ainvoke(
                    {"messages": [HumanMessage(content="execute the isolated provider gate")]},
                    config=config,
                )
            except Exception as exc:
                payload["provider_exception_type"] = type(exc).__name__
            else:
                raise RuntimeError("hold phase unexpectedly succeeded")
            payload["snapshot"] = await _snapshot(storage, tree_path)
        else:
            payload["before_resume"] = await _snapshot(storage, tree_path)
            checkpoint_before = await storage.checkpointer.aget(config)
            result = await graph.ainvoke(None, config=config)
            payload["checkpoint_before"] = checkpoint_before is not None
            payload["human_messages"] = sum(
                1 for message in result["messages"] if isinstance(message, HumanMessage)
            )
            payload["assistant_messages"] = sum(
                1 for message in result["messages"] if isinstance(message, AIMessage)
            )
            payload["snapshot"] = await _snapshot(storage, tree_path)
        payload["side_effect_counter"] = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        reset_task_runtime_context(token)
        set_provider_gateway(None)
        set_runtime_storage(None)
        await gateway.stop()
        await storage.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("hold", "resume"), required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.phase, args.data_root.resolve(), args.base_url.rstrip("/"), args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
