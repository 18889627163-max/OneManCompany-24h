from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from onemancompany.agents.base import GatewayChatModel
from onemancompany.core.provider_gateway import ProviderGateway, set_provider_gateway
from onemancompany.core.runtime_context import reset_task_runtime_context, set_task_runtime_context
from onemancompany.core.runtime_storage import RuntimeStorage


class CountingChatModel(BaseChatModel):
    active: int = 0
    high_water: int = 0
    lock: asyncio.Lock | None = None

    @property
    def _llm_type(self) -> str:
        return "counting"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        assert self.lock is not None
        async with self.lock:
            self.active += 1
            self.high_water = max(self.high_water, self.active)
        await asyncio.sleep(0.01)
        async with self.lock:
            self.active -= 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


@pytest.mark.asyncio
async def test_gateway_chat_model_limits_each_actual_model_call(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, default_concurrency=1, transient_retry_limit_for_call=0)
    await gateway.start()
    set_provider_gateway(gateway)
    delegate = CountingChatModel(lock=asyncio.Lock())
    model = GatewayChatModel(
        delegate=delegate,
        provider_context={
            "provider": "test",
            "credential_fingerprint": "hash",
            "account_or_model_pool": "pool",
        },
    )
    try:
        await asyncio.gather(*[model.ainvoke([HumanMessage(content=str(i))]) for i in range(12)])
        assert delegate.high_water == 1
    finally:
        set_provider_gateway(None)
        await gateway.stop()
        await storage.close()


@pytest.mark.asyncio
async def test_gateway_chat_model_uses_task_runtime_node_context(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, default_concurrency=1, transient_retry_limit_for_call=0)
    await gateway.start()
    set_provider_gateway(gateway)
    delegate = CountingChatModel(lock=asyncio.Lock())
    model = GatewayChatModel(
        delegate=delegate,
        provider_context={
            "provider": "test",
            "credential_fingerprint": "hash",
            "account_or_model_pool": "pool",
        },
    )
    token = set_task_runtime_context({"node_id": "node-123"})
    try:
        await model.ainvoke([HumanMessage(content="hello")])
        row = await storage.fetchone("SELECT node_id FROM provider_queue ORDER BY submitted_at DESC LIMIT 1")
        assert row and row[0] == "node-123"
    finally:
        reset_task_runtime_context(token)
        set_provider_gateway(None)
        await gateway.stop()
        await storage.close()
