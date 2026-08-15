"""Low-priority durable Memory Outbox worker."""
from __future__ import annotations

import asyncio
from loguru import logger

from onemancompany.core.memory_service import MemoryService
from onemancompany.core.provider_gateway import (
    ProviderGateway,
    ProviderPriority,
    get_provider_gateway,
)
from onemancompany.core.runtime_storage import RuntimeStorage


class MemoryOutboxWorker:
    def __init__(
        self,
        storage: RuntimeStorage,
        *,
        interval_seconds: float = 5.0,
        provider_gateway: ProviderGateway | None = None,
    ) -> None:
        self.storage = storage
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.provider_gateway = provider_gateway
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="omc-memory-outbox")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                gateway = self.provider_gateway or get_provider_gateway()
                if gateway is not None:
                    await gateway.wait_for_background_turn(ProviderPriority.MEMORY)
                events = await self.storage.claim_memory_outbox(limit=2)
                for event in events:
                    await self._process(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Memory outbox worker iteration failed: {}", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                logger.trace("Memory outbox polling interval elapsed")

    async def _process(self, event: dict) -> None:
        try:
            service = MemoryService(self.storage)
            payload = dict(event["payload"])
            memory = await service.propose(
                employee_id=str(payload.get("employee_id") or "system"),
                memory_type=str(payload["memory_type"]),
                subject=str(payload.get("subject") or ""),
                text=str(payload.get("text") or ""),
                scope=str(payload.get("scope") or "employee"),
                project_id=str(payload.get("project_id") or ""),
                structured_value=payload.get("structured_value") or {},
                evidence_refs=payload.get("evidence_refs") or [],
                source_node_id=str(payload.get("source_node_id") or ""),
                source_iteration_id=str(payload.get("source_iteration_id") or ""),
                source_thread_id=str(payload.get("source_thread_id") or ""),
                confidence=float(payload.get("confidence", 0.5)),
                expires_at=payload.get("expires_at"),
                dedupe_key=str(event.get("memory_key") or "") or None,
                trusted_source=bool(payload.get("trusted_source", False)),
                index_immediately=False,
            )
            if memory.get("embedding_status") != "indexed":
                async def _index_memory():
                    return await service.ensure_indexed(
                        namespace=tuple(event["namespace"]),
                        key=str(memory["key"]),
                    )

                gateway = self.provider_gateway or get_provider_gateway()
                if gateway is None:
                    await _index_memory()
                else:
                    await gateway.wait_for_background_turn(ProviderPriority.EMBEDDING)
                    config = dict(self.storage._memory_index_config or {})
                    await gateway.invoke(
                        context={
                            "request_id": f"memory:{event['event_id']}:embedding",
                            "provider": "memory-embedding",
                            "credential_fingerprint": str(
                                config.get("provider_fingerprint") or "memory"
                            ),
                            "account_or_model_pool": str(
                                config.get("embedding_model") or "embedding"
                            ),
                            # The outbox, not an in-memory sleep loop, owns retry.
                            "transient_retry_limit_for_call": 0,
                        },
                        priority=ProviderPriority.EMBEDDING,
                        invoke=_index_memory,
                    )
            await self.storage.finish_memory_outbox(event["event_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Start at 30 seconds and back off durably without affecting the
            # business task that emitted this memory event.
            retry_exponent = min(6, max(0, int(event.get("attempt", 1)) - 1))
            retry_seconds = min(1800, 30 * (2 ** retry_exponent))
            await self.storage.fail_memory_outbox(
                event["event_id"],
                type(exc).__name__,
                retry_seconds=retry_seconds,
            )
