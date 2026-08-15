"""Priority-aware provider gateway that owns the actual invocation permit."""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import Any, Awaitable, Callable, TypeVar

from onemancompany.core.runtime_storage import RuntimeStorage, iso_now, utc_now

T = TypeVar("T")


class ProviderPriority(IntEnum):
    BUSINESS = 0
    REVIEW = 0
    RECOVERY = 10
    MEMORY = 20
    EMBEDDING = 30


class ErrorDisposition(str, Enum):
    TRANSIENT = "transient"
    BLOCKED = "blocked"
    FATAL = "fatal"


_TRANSIENT = (
    "concurrency limit", "rate limit", "too many requests", "429", "request timeout",
    "timed out", "timeout", "connection reset", "connection aborted", "connection refused",
    "502", "503", "504", "temporarily unavailable", "provider unavailable",
)
_BLOCKED = (
    "authentication", "invalid api key", "incorrect api key", "unauthorized", "forbidden",
    "model not found", "unknown model", "billing", "quota exceeded", "insufficient quota",
)


def classify_provider_error(error: BaseException) -> ErrorDisposition:
    text = f"{type(error).__name__}: {error}".lower()
    if any(marker in text for marker in _BLOCKED):
        return ErrorDisposition.BLOCKED
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError)) or any(marker in text for marker in _TRANSIENT):
        return ErrorDisposition.TRANSIENT
    return ErrorDisposition.FATAL


def credential_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24] if value else "anonymous"


@dataclass(order=True)
class _Waiter:
    priority: int
    sequence: int
    future: asyncio.Future = field(compare=False)


class _PriorityLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.active = 0
        self.waiters: list[_Waiter] = []
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def acquire(self, priority: int) -> None:
        async with self.lock:
            if self.active < self.limit and not self.waiters:
                self.active += 1
                return
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self.sequence += 1
            heapq.heappush(self.waiters, _Waiter(priority, self.sequence, future))
        try:
            await future
        except BaseException:
            async with self.lock:
                if not future.done():
                    future.cancel()
                self.waiters = [row for row in self.waiters if row.future is not future]
                heapq.heapify(self.waiters)
            raise

    async def release(self) -> None:
        async with self.lock:
            while self.waiters:
                waiter = heapq.heappop(self.waiters)
                if waiter.future.cancelled():
                    continue
                waiter.future.set_result(None)
                return
            self.active = max(0, self.active - 1)


class ProviderGateway:
    """Queues calls by provider credential pool and holds permit during invoke()."""

    def __init__(
        self,
        storage: RuntimeStorage,
        *,
        default_concurrency: int = 1,
        transient_retry_limit_for_call: int | None = None,
        max_backoff_seconds: float = 300.0,
    ) -> None:
        self.storage = storage
        self.default_concurrency = max(1, default_concurrency)
        self.transient_retry_limit_for_call = transient_retry_limit_for_call
        self.max_backoff_seconds = max_backoff_seconds
        self._limiters: dict[str, _PriorityLimiter] = {}
        self._started = False
        self._metric_lock = asyncio.Lock()
        self._running = 0
        self._queued = 0
        self._running_by_priority: dict[int, int] = {}
        self._queued_by_priority: dict[int, int] = {}

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    @staticmethod
    def group_key(context: dict[str, Any]) -> str:
        provider = str(context.get("provider") or "default")
        credential = str(context.get("credential") or context.get("credential_fingerprint") or "")
        fingerprint = credential if context.get("credential_fingerprint") else credential_fingerprint(credential)
        pool = str(context.get("account_or_model_pool") or context.get("model") or "default")
        return f"{provider}:{fingerprint}:{pool}"

    async def invoke(
        self,
        context: dict[str, Any],
        priority: ProviderPriority,
        invoke: Callable[[], Awaitable[T]],
    ) -> T:
        if not self._started:
            await self.start()
        group = self.group_key(context)
        limiter = self._limiters.setdefault(group, _PriorityLimiter(self.default_concurrency))
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        node_id = str(context.get("node_id") or "")
        submitted = iso_now()
        # A durable request ID may be resumed by a new process. Preserve the
        # failure count, original submission time, and retry metadata instead of
        # replacing the row (which would also cascade-delete retry state).
        await self.storage.execute(
            "INSERT INTO provider_queue(request_id,group_key,node_id,priority,status,attempt,submitted_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET "
            "group_key=excluded.group_key,node_id=excluded.node_id,priority=excluded.priority,"
            "status='queued',started_at=NULL,completed_at=NULL",
            (request_id, group, node_id, int(priority), "queued", 0, submitted),
        )
        existing = await self.storage.fetchone(
            "SELECT attempt,next_retry_at FROM provider_queue WHERE request_id=?",
            (request_id,),
        )
        attempt = int(existing[0]) if existing else 0
        if existing and existing[1]:
            try:
                retry_at = datetime.fromisoformat(str(existing[1]).replace("Z", "+00:00"))
                remaining = (retry_at - utc_now()).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(remaining)
            except (TypeError, ValueError):
                # Invalid legacy metadata must not make a recoverable provider
                # request permanently unrunnable.
                remaining = 0.0
        while True:
            async with self._metric_lock:
                self._queued += 1
                priority_value = int(priority)
                self._queued_by_priority[priority_value] = (
                    self._queued_by_priority.get(priority_value, 0) + 1
                )
            await limiter.acquire(int(priority))
            async with self._metric_lock:
                self._queued = max(0, self._queued - 1)
                self._queued_by_priority[priority_value] = max(
                    0, self._queued_by_priority.get(priority_value, 0) - 1
                )
                self._running += 1
                self._running_by_priority[priority_value] = (
                    self._running_by_priority.get(priority_value, 0) + 1
                )
            try:
                await self.storage.execute(
                    "UPDATE provider_queue SET status='running',attempt=?,started_at=? WHERE request_id=?",
                    (attempt, iso_now(), request_id),
                )
                result = await invoke()
                await self._mark_completed(request_id, attempt)
                if attempt > 0:
                    callback = context.get("on_recovered")
                    if callback:
                        maybe = callback(attempt)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                return result
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    await self.storage.execute(
                        "UPDATE provider_queue SET status='holding',last_error_class='cancelled' WHERE request_id=?",
                        (request_id,),
                    )
                    raise
                disposition = classify_provider_error(error)
                if disposition is not ErrorDisposition.TRANSIENT:
                    status = "blocked" if disposition is ErrorDisposition.BLOCKED else "failed"
                    await self._record_error(request_id, attempt, disposition, error, status=status)
                    raise
                attempt += 1
                delay = min(self.max_backoff_seconds, max(1.0, 2 ** min(attempt - 1, 8)))
                delay *= random.uniform(0.75, 1.25)
                next_retry = (utc_now() + timedelta(seconds=delay)).isoformat()
                await self._record_error(
                    request_id,
                    attempt,
                    disposition,
                    error,
                    status="holding",
                    next_retry=next_retry,
                )
                callback = context.get("on_holding")
                if callback:
                    maybe = callback(attempt, next_retry, str(error))
                    if asyncio.iscoroutine(maybe):
                        await maybe
                retry_limit = context.get(
                    "transient_retry_limit_for_call",
                    self.transient_retry_limit_for_call,
                )
                if retry_limit is not None and attempt > int(retry_limit):
                    raise
            finally:
                async with self._metric_lock:
                    self._running = max(0, self._running - 1)
                    self._running_by_priority[priority_value] = max(
                        0, self._running_by_priority.get(priority_value, 0) - 1
                    )
                await limiter.release()
            await asyncio.sleep(delay)
            await self.storage.execute("UPDATE provider_queue SET status='queued' WHERE request_id=?", (request_id,))

    async def _mark_completed(self, request_id: str, attempt: int) -> None:
        """Atomically clear active retry timing while preserving attempt history."""
        now = iso_now()
        async with self.storage._write_lock:
            await self.storage.conn.execute(
                "UPDATE provider_queue SET status='completed',completed_at=?,next_retry_at=NULL,"
                "last_error=NULL,last_error_class=NULL WHERE request_id=?",
                (now, request_id),
            )
            if attempt > 0:
                await self.storage.conn.execute(
                    "UPDATE provider_retry_state SET attempt=?,next_retry_at=NULL,last_error_class=NULL,"
                    "last_error=NULL,updated_at=? WHERE request_id=?",
                    (attempt, now, request_id),
                )
            await self.storage.conn.commit()

    async def _record_error(
        self, request_id: str, attempt: int, disposition: ErrorDisposition,
        error: BaseException, *, status: str, next_retry: str | None = None,
    ) -> None:
        # Error text is bounded and should already be free of credentials; never
        # persist invocation context or headers here.
        message = str(error)[:1000]
        now = iso_now()
        async with self.storage._write_lock:
            await self.storage.conn.execute(
                "UPDATE provider_queue SET status=?,attempt=?,next_retry_at=?,last_error_class=?,last_error=? WHERE request_id=?",
                (status, attempt, next_retry, disposition.value, message, request_id),
            )
            await self.storage.conn.execute(
                "INSERT INTO provider_retry_state(request_id,attempt,next_retry_at,last_error_class,last_error,updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(request_id) DO UPDATE SET attempt=excluded.attempt,next_retry_at=excluded.next_retry_at," 
                "last_error_class=excluded.last_error_class,last_error=excluded.last_error,updated_at=excluded.updated_at",
                (request_id, attempt, next_retry, disposition.value, message, now),
            )
            await self.storage.conn.commit()

    async def health_check(self) -> bool:
        return self._started and await self.storage.health_check()

    async def wait_for_background_turn(
        self,
        priority: ProviderPriority = ProviderPriority.MEMORY,
        *,
        poll_interval: float = 0.01,
    ) -> None:
        """Wait until no higher-priority provider work is queued or running.

        Provider calls cannot be safely pre-empted once sent. Background workers
        therefore check this admission gate before starting each external call;
        any business/recovery demand already visible to the gateway goes first.
        """
        threshold = int(priority)
        while True:
            async with self._metric_lock:
                higher_priority_active = any(
                    value > 0 and queued_priority < threshold
                    for queued_priority, value in self._queued_by_priority.items()
                ) or any(
                    value > 0 and running_priority < threshold
                    for running_priority, value in self._running_by_priority.items()
                )
            if not higher_priority_active:
                return
            await asyncio.sleep(max(0.001, float(poll_interval)))

    async def metrics(self) -> dict[str, Any]:
        async with self._metric_lock:
            running, queued = self._running, self._queued
        row = await self.storage.fetchone(
            "SELECT MIN(submitted_at) FROM provider_queue WHERE status IN ('queued','holding')"
        )
        return {"running": running, "queued": queued, "oldest_queue_at": row[0] if row and row[0] else None}


_provider_gateway: ProviderGateway | None = None


def set_provider_gateway(gateway: ProviderGateway | None) -> None:
    global _provider_gateway
    _provider_gateway = gateway


def get_provider_gateway() -> ProviderGateway | None:
    return _provider_gateway
