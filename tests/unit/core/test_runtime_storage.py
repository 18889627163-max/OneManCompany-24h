from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from onemancompany.core.runtime_storage import RuntimeStorage


@pytest.mark.asyncio
async def test_runtime_storage_initializes_official_saver_store_and_owned_schema(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        assert storage.checkpointer is not None
        assert storage.memory_store is not None
        assert await storage.health_check()
        tables = await storage.list_tables()
        assert {"schema_migrations", "provider_queue", "execution_leases", "dispatch_intents", "audit_events"} <= tables
        # Official components own their schemas; an actual write proves their async contract.
        await storage.memory_store.aput(("employee", "00006", "episodic"), "m1", {"text": "worked"})
        rows = await storage.memory_store.asearch(("employee", "00006", "episodic"), limit=10)
        assert [row.key for row in rows] == ["m1"]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_execution_lease_fencing_and_takeover(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        first = await storage.acquire_lease("node", 1, "worker-a", ttl_seconds=30)
        assert first is not None and first.fencing_token == 1
        assert await storage.acquire_lease("node", 1, "worker-b", ttl_seconds=30) is None
        await storage.force_expire_lease("node", 1)
        second = await storage.acquire_lease("node", 1, "worker-b", ttl_seconds=30)
        assert second is not None and second.fencing_token == 2
        assert await storage.validate_fencing_token("node", 1, 1) is False
        assert await storage.validate_fencing_token("node", 1, 2) is True
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_online_backup_is_integral_and_manifested(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        await storage.append_audit("test", {"ok": True})
        result = await storage.backup(
            tmp_path / "backups",
            application_version="test",
            backup_id="20260813T120000Z",
        )
        assert result.database_path.exists()
        assert result.manifest_path.exists()
        assert result.integrity_check_result == "ok"
        assert result.database_path.name == "runtime-20260813T120000Z.sqlite3"

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["backup_id"] == "20260813T120000Z"
        assert manifest["database_path"] == str(result.database_path)
        assert manifest["database_size_bytes"] == result.database_path.stat().st_size
        assert manifest["sqlite_page_count"] > 0
        assert manifest["sqlite_page_size"] > 0
        assert manifest["quick_check_result"] == "ok"
        assert manifest["integrity_check_result"] == "ok"
        assert manifest["database_checksum"].startswith("sha256:")
        assert {"checkpoints", "store", "memory_outbox", "audit_events"} <= set(
            manifest["verified_tables"]
        )
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_online_backup_rejects_invalid_backup_id(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        with pytest.raises(ValueError, match="backup_id"):
            await storage.backup(tmp_path / "backups", backup_id="../../escape")
    finally:
        await storage.close()

@pytest.mark.asyncio
async def test_dispatch_intent_is_idempotent_and_rejects_changed_request(tmp_path):
    """The durable dispatch key returns its original intent and fails closed on drift."""
    from onemancompany.core.runtime_storage import DispatchIntentConflict, RuntimeStorage

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        first = await storage.prepare_dispatch_intent(
            parent_id="parent-1",
            employee_id="00006",
            task_key="phase1-smoke-backend",
            request_fingerprint="sha256:abc",
        )
        replay = await storage.prepare_dispatch_intent(
            parent_id="parent-1",
            employee_id="00006",
            task_key="phase1-smoke-backend",
            request_fingerprint="sha256:abc",
        )

        assert first == replay
        assert first["state"] == "prepared"
        assert first["node_id"] is None

        with pytest.raises(DispatchIntentConflict):
            await storage.prepare_dispatch_intent(
                parent_id="parent-1",
                employee_id="00006",
                task_key="phase1-smoke-backend",
                request_fingerprint="sha256:different",
            )
    finally:
        await storage.close()

@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_close_is_idempotent(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    first_conn = storage.conn
    first_saver = storage.checkpointer
    first_store = storage.memory_store

    await storage.initialize()
    assert storage.conn is first_conn
    assert storage.checkpointer is first_saver
    assert storage.memory_store is first_store

    await storage.close()
    await storage.close()
    assert storage.checkpointer is None
    assert storage.memory_store is None


@pytest.mark.asyncio
async def test_checkpoint_survives_storage_restart(tmp_path):
    from langgraph.checkpoint.base import empty_checkpoint

    path = tmp_path / "runtime.sqlite3"
    thread = {"configurable": {"thread_id": "omc:p:i:n:g1", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"durable": "yes"}

    first = RuntimeStorage(path)
    await first.initialize()
    await first.checkpointer.aput(thread, checkpoint, {}, {})
    await first.close()

    second = RuntimeStorage(path)
    await second.initialize()
    try:
        restored = await second.checkpointer.aget(thread)
        assert restored is not None
        assert restored["channel_values"]["durable"] == "yes"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_vector_memory_store_uses_sqlite_vec_and_rejects_dimension_drift(tmp_path):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    path = tmp_path / "runtime.sqlite3"
    storage = RuntimeStorage(path)
    await storage.initialize(memory_index={
        "dims": 4,
        "embed": DeterministicFakeEmbedding(size=4),
        "fields": ["text"],
    })
    try:
        await storage.memory_store.aput(
            ("employee", "00006", "episodic"),
            "m-vector",
            {"text": "SQLite checkpoint recovery"},
        )
        rows = await storage.memory_store.asearch(
            ("employee", "00006", "episodic"),
            query="checkpoint recovery",
            limit=1,
        )
        assert rows and rows[0].key == "m-vector"
        tables = await storage.list_tables()
        assert "vector_migrations" in tables
    finally:
        await storage.close()

    incompatible = RuntimeStorage(path)
    with pytest.raises(Exception):
        await incompatible.initialize(memory_index={
            "dims": 5,
            "embed": DeterministicFakeEmbedding(size=5),
            "text_fields": ["text"],
            "index_version": "v1",
        })
    await incompatible.close()


@pytest.mark.asyncio
async def test_backup_restores_checkpoint_memory_and_runtime_tables(tmp_path):
    import sqlite3
    from langgraph.checkpoint.base import empty_checkpoint

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        await storage.memory_store.aput(
            ("project", "p1", "semantic"), "fact-1", {"text": "verified fact"}
        )
        thread = {"configurable": {"thread_id": "omc:p1:i1:n1:g1", "checkpoint_ns": ""}}
        await storage.checkpointer.aput(thread, empty_checkpoint(), {}, {})
        await storage.execute(
            "INSERT INTO memory_outbox(event_id,namespace_json,memory_key,payload_json,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("evt-1", '["project","p1","semantic"]', "fact-1", '{}', "pending", "2026-08-13T00:00:00+00:00"),
        )
        result = await storage.backup(tmp_path / "backups", application_version="test")
    finally:
        await storage.close()

    restored = sqlite3.connect(result.database_path)
    try:
        tables = {r[0] for r in restored.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"checkpoints", "store", "memory_outbox"} <= tables
        assert restored.execute("SELECT COUNT(*) FROM memory_outbox WHERE event_id='evt-1'").fetchone()[0] == 1
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()

@pytest.mark.asyncio
async def test_initialize_failure_closes_partial_connections_and_can_retry(tmp_path):
    from langchain_core.embeddings import DeterministicFakeEmbedding

    path = tmp_path / "runtime.sqlite3"
    initial = RuntimeStorage(path)
    await initial.initialize(memory_index={
        "dims": 4,
        "embed": DeterministicFakeEmbedding(size=4),
        "text_fields": ["text"],
        "index_version": "v1",
    })
    await initial.close()

    failed = RuntimeStorage(path)
    with pytest.raises(ValueError, match="configuration mismatch"):
        await failed.initialize(memory_index={
            "dims": 5,
            "embed": DeterministicFakeEmbedding(size=5),
            "text_fields": ["text"],
            "index_version": "v1",
        })
    assert failed._conn is None
    assert failed._checkpoint_conn is None
    assert failed._store_conn is None
    assert failed.checkpointer is None
    assert failed.memory_store is None

    recovered = RuntimeStorage(path)
    await recovered.initialize(memory_index={
        "dims": 4,
        "embed": DeterministicFakeEmbedding(size=4),
        "text_fields": ["text"],
        "index_version": "v1",
    })
    await recovered.close()

@pytest.mark.asyncio
async def test_side_effect_ledger_replays_completed_result_and_rejects_argument_drift(tmp_path):
    from onemancompany.core.runtime_storage import ToolInvocationConflict

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        prepared = await storage.prepare_tool_invocation(
            node_id="node-1",
            execution_generation=1,
            tool_name="dispatch_child",
            tool_call_id="call-1",
            business_idempotency_key="child-a",
            request_fingerprint="fp-1",
        )
        assert prepared["status"] == "prepared"
        assert prepared["replayed"] is False

        completed = await storage.complete_tool_invocation(
            node_id="node-1",
            execution_generation=1,
            tool_name="dispatch_child",
            business_idempotency_key="child-a",
            request_fingerprint="fp-1",
            result={"status": "success", "node_id": "child-node"},
            result_reference="child-node",
        )
        assert completed["status"] == "completed"

        replay = await storage.prepare_tool_invocation(
            node_id="node-1",
            execution_generation=1,
            tool_name="dispatch_child",
            tool_call_id="call-2",
            business_idempotency_key="child-a",
            request_fingerprint="fp-1",
        )
        assert replay["replayed"] is True
        assert replay["result"] == {"status": "success", "node_id": "child-node"}

        with pytest.raises(ToolInvocationConflict):
            await storage.prepare_tool_invocation(
                node_id="node-1",
                execution_generation=1,
                tool_name="dispatch_child",
                tool_call_id="call-3",
                business_idempotency_key="child-a",
                request_fingerprint="different-fingerprint",
            )
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_side_effect_ledger_requires_reconciliation_for_uncertain_prepared_or_failed_rows(tmp_path):
    from onemancompany.core.runtime_storage import ToolInvocationReconciliationRequired

    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        kwargs = {
            "node_id": "node-2",
            "execution_generation": 1,
            "tool_name": "accept_child",
            "tool_call_id": "call-1",
            "business_idempotency_key": "child-2",
            "request_fingerprint": "fp-2",
        }
        await storage.prepare_tool_invocation(**kwargs)
        with pytest.raises(ToolInvocationReconciliationRequired) as prepared_error:
            await storage.prepare_tool_invocation(**kwargs)
        assert prepared_error.value.invocation["status"] == "prepared"

        await storage.fail_tool_invocation(
            node_id="node-2",
            execution_generation=1,
            tool_name="accept_child",
            business_idempotency_key="child-2",
            request_fingerprint="fp-2",
            error="process ended after external call",
        )
        with pytest.raises(ToolInvocationReconciliationRequired) as failed_error:
            await storage.prepare_tool_invocation(**kwargs)
        assert failed_error.value.invocation["status"] == "failed"
    finally:
        await storage.close()
