from __future__ import annotations

import asyncio

import pytest

from onemancompany.core.provider_gateway import (
    ErrorDisposition, ProviderGateway, ProviderPriority, classify_provider_error,
)
from onemancompany.core.runtime_storage import RuntimeStorage


def test_provider_error_classification():
    assert classify_provider_error(RuntimeError("HTTP 429 rate limit")) is ErrorDisposition.TRANSIENT
    assert classify_provider_error(RuntimeError("request timeout")) is ErrorDisposition.TRANSIENT
    assert classify_provider_error(RuntimeError("invalid api key authentication failed")) is ErrorDisposition.BLOCKED
    assert classify_provider_error(RuntimeError("bad local invariant")) is ErrorDisposition.FATAL


@pytest.mark.asyncio
async def test_gateway_wraps_real_calls_and_enforces_group_limit(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, default_concurrency=1, transient_retry_limit_for_call=0)
    await gateway.start()
    active = 0
    high_water = 0
    lock = asyncio.Lock()

    async def actual_call():
        nonlocal active, high_water
        async with lock:
            active += 1
            high_water = max(high_water, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return "ok"

    try:
        results = await asyncio.gather(*[
            gateway.invoke(
                context={"provider": "test", "credential": "secret", "account_or_model_pool": "pool", "node_id": str(i)},
                priority=ProviderPriority.BUSINESS,
                invoke=actual_call,
            ) for i in range(12)
        ])
        assert results == ["ok"] * 12
        assert high_water == 1
        metrics = await gateway.metrics()
        assert metrics["running"] == 0
        assert metrics["queued"] == 0
    finally:
        await gateway.stop()
        await storage.close()

@pytest.mark.asyncio
async def test_retry_limit_zero_still_persists_next_retry_and_calls_holding_callback(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(
        storage,
        transient_retry_limit_for_call=0,
        max_backoff_seconds=0.01,
    )
    callbacks = []

    async def fail():
        raise RuntimeError("HTTP 429 Concurrency limit exceeded for user")

    try:
        with pytest.raises(RuntimeError, match="Concurrency limit"):
            await gateway.invoke(
                {
                    "request_id": "durable-429",
                    "provider": "test",
                    "credential_fingerprint": "credential-a",
                    "on_holding": lambda attempt, retry_at, error: callbacks.append(
                        (attempt, retry_at, error)
                    ),
                },
                ProviderPriority.BUSINESS,
                fail,
            )
        row = await storage.fetchone(
            "SELECT status,attempt,next_retry_at FROM provider_queue WHERE request_id=?",
            ("durable-429",),
        )
        assert row[0] == "holding"
        assert row[1] == 1
        assert row[2]
        assert callbacks and callbacks[0][0] == 1
        retry = await storage.fetchone(
            "SELECT attempt,next_retry_at FROM provider_retry_state WHERE request_id=?",
            ("durable-429",),
        )
        assert retry[0] == 1
        assert retry[1] == row[2]
    finally:
        await gateway.stop()
        await storage.close()


@pytest.mark.asyncio
async def test_same_request_id_resumes_without_resetting_attempt_or_retry_state(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    first = RuntimeStorage(path)
    await first.initialize()
    first_gateway = ProviderGateway(
        first,
        transient_retry_limit_for_call=0,
        max_backoff_seconds=0.01,
    )

    async def fail():
        raise RuntimeError("HTTP 429 rate limit")

    with pytest.raises(RuntimeError):
        await first_gateway.invoke(
            {
                "request_id": "resume-provider-request",
                "provider": "test",
                "credential_fingerprint": "credential-a",
            },
            ProviderPriority.RECOVERY,
            fail,
        )
    await first.close()

    second = RuntimeStorage(path)
    await second.initialize()
    second_gateway = ProviderGateway(second, max_backoff_seconds=0.01)
    calls = 0

    async def succeed():
        nonlocal calls
        calls += 1
        return "ok"

    try:
        result = await second_gateway.invoke(
            {
                "request_id": "resume-provider-request",
                "provider": "test",
                "credential_fingerprint": "credential-a",
            },
            ProviderPriority.RECOVERY,
            succeed,
        )
        assert result == "ok"
        assert calls == 1
        row = await second.fetchone(
            "SELECT status,attempt,submitted_at FROM provider_queue WHERE request_id=?",
            ("resume-provider-request",),
        )
        assert row[0] == "completed"
        assert row[1] == 1
        retry = await second.fetchone(
            "SELECT attempt,next_retry_at,last_error_class,last_error "
            "FROM provider_retry_state WHERE request_id=?",
            ("resume-provider-request",),
        )
        assert retry[0] == 1
        assert retry[1:] == (None, None, None)
    finally:
        await second_gateway.stop()
        await second.close()


@pytest.mark.asyncio
async def test_business_waiter_runs_before_memory_waiter_for_same_provider_pool(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, default_concurrency=1)
    await gateway.start()
    blocker_started = asyncio.Event()
    release_blocker = asyncio.Event()
    order: list[str] = []

    context = {
        "provider": "test",
        "credential_fingerprint": "shared-account",
        "account_or_model_pool": "shared-pool",
    }

    async def blocker():
        blocker_started.set()
        await release_blocker.wait()
        order.append("blocker")
        return "blocker"

    async def record(name: str):
        order.append(name)
        return name

    try:
        active = asyncio.create_task(
            gateway.invoke(
                {**context, "request_id": "priority-blocker"},
                ProviderPriority.BUSINESS,
                blocker,
            )
        )
        await blocker_started.wait()
        memory = asyncio.create_task(
            gateway.invoke(
                {**context, "request_id": "priority-memory"},
                ProviderPriority.MEMORY,
                lambda: record("memory"),
            )
        )
        await asyncio.sleep(0)
        business = asyncio.create_task(
            gateway.invoke(
                {**context, "request_id": "priority-business"},
                ProviderPriority.BUSINESS,
                lambda: record("business"),
            )
        )
        for _ in range(100):
            if (await gateway.metrics())["queued"] == 2:
                break
            await asyncio.sleep(0.001)
        assert (await gateway.metrics())["queued"] == 2
        release_blocker.set()
        await asyncio.gather(active, memory, business)
        assert order == ["blocker", "business", "memory"]
    finally:
        await gateway.stop()
        await storage.close()


@pytest.mark.asyncio
async def test_recovery_callback_runs_after_a_held_request_succeeds(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage, max_backoff_seconds=0.01)
    events: list[tuple[str, int]] = []
    calls = 0

    async def flaky_call():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("HTTP 429 Concurrency limit exceeded for user")
        return "ok"

    try:
        result = await gateway.invoke(
            {
                "request_id": "held-then-recovered",
                "provider": "test",
                "credential_fingerprint": "credential-a",
                "on_holding": lambda attempt, *_: events.append(("holding", attempt)),
                "on_recovered": lambda attempt: events.append(("recovered", attempt)),
            },
            ProviderPriority.BUSINESS,
            flaky_call,
        )
        assert result == "ok"
        assert events == [("holding", 1), ("recovered", 1)]
    finally:
        await gateway.stop()
        await storage.close()
