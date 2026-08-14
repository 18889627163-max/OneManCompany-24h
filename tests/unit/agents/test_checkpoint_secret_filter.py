from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from onemancompany.agents.base import BaseAgentRunner
from onemancompany.core.runtime_storage import RuntimeStorage


@pytest.mark.asyncio
async def test_sanitized_agent_input_does_not_persist_secrets_in_sqlite_checkpoint(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        graph = StateGraph(MessagesState)

        async def finish(_state: MessagesState):
            return {"messages": [AIMessage(content="completed")]}

        graph.add_node("finish", finish)
        graph.add_edge(START, "finish")
        graph.add_edge("finish", END)
        compiled = graph.compile(checkpointer=storage.checkpointer)

        runner = BaseAgentRunner()
        checkpoint_input = runner._checkpoint_input(
            "System Authorization: Bearer system.jwt.token",
            "password=hunter2 api_key=sk-abcdefghijklmnop",
        )
        assert checkpoint_input is not None
        await compiled.ainvoke(
            checkpoint_input,
            config={"configurable": {"thread_id": "omc:test:iter:test-node:g1"}},
        )

        # SQLite may still have uncheckpointed pages in WAL, so scan the database
        # and its sidecar files while all connections remain open.
        persisted = b"".join(
            path.read_bytes()
            for path in sorted(tmp_path.glob("runtime.sqlite3*"))
            if path.is_file()
        )
        for secret in (
            b"system.jwt.token",
            b"hunter2",
            b"sk-abcdefghijklmnop",
        ):
            assert secret not in persisted
        assert b"[REDACTED]" in persisted
    finally:
        await storage.close()
