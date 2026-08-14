"""Low-priority durable Memory Outbox worker."""
from __future__ import annotations

import asyncio
from loguru import logger

from onemancompany.core.memory_service import MemoryService
from onemancompany.core.runtime_storage import RuntimeStorage


class MemoryOutboxWorker:
    def __init__(self, storage: RuntimeStorage, *, interval_seconds: float = 5.0) -> None:
        self.storage = storage
        self.interval_seconds = max(1.0, float(interval_seconds))
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
            await service.propose(
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
            )
            await self.storage.finish_memory_outbox(event["event_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.storage.fail_memory_outbox(event["event_id"], type(exc).__name__)
