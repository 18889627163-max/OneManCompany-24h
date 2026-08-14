"""Single-writer SQLite runtime infrastructure.

LangGraph owns checkpoint/store schemas.  This module owns only OMC runtime
coordination tables and exposes short transaction helpers.  External/provider
calls must never execute while a transaction is open.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import sqlite_vec
from loguru import logger
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base.embed import get_text_at_path, tokenize_path
from langgraph.store.sqlite.aio import AsyncSqliteStore

_SCHEMA_VERSION = 4
_OWNED_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_index_config (
    index_version TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL,
    text_fields_json TEXT NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT '',
    provider_fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    activated_at TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS memory_vector_versions (
    index_version TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key TEXT NOT NULL,
    field_name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(index_version, prefix, key, field_name)
);
CREATE INDEX IF NOT EXISTS idx_memory_vector_versions_lookup
    ON memory_vector_versions(index_version, prefix, key);
CREATE TABLE IF NOT EXISTS provider_queue (
    request_id TEXT PRIMARY KEY,
    group_key TEXT NOT NULL,
    node_id TEXT,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    next_retry_at TEXT,
    last_error_class TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_provider_queue_status
    ON provider_queue(status, priority, submitted_at);
CREATE TABLE IF NOT EXISTS provider_retry_state (
    request_id TEXT PRIMARY KEY,
    attempt INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error_class TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(request_id) REFERENCES provider_queue(request_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS execution_leases (
    node_id TEXT NOT NULL,
    execution_generation INTEGER NOT NULL,
    lease_owner TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY(node_id, execution_generation)
);
CREATE TABLE IF NOT EXISTS dispatch_intents (
    parent_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    task_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    node_id TEXT,
    state TEXT NOT NULL,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(parent_id, employee_id, task_key)
);
CREATE TABLE IF NOT EXISTS tool_invocation_ledger (
    node_id TEXT NOT NULL,
    execution_generation INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    business_idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_reference TEXT,
    result_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(node_id, execution_generation, tool_name, business_idempotency_key)
);
CREATE TABLE IF NOT EXISTS memory_outbox (
    event_id TEXT PRIMARY KEY,
    namespace_json TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_outbox_status ON memory_outbox(status, next_retry_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_outbox_dedupe ON memory_outbox(memory_key);
CREATE TABLE IF NOT EXISTS memory_reviews (
    review_id TEXT PRIMARY KEY,
    namespace_json TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    notes TEXT,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id TEXT PRIMARY KEY,
    namespace_json TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    old_memory_key TEXT NOT NULL,
    new_memory_key TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    status TEXT NOT NULL,
    resolution_action TEXT,
    resolved_by TEXT,
    detected_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS recoveries (
    recovery_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    tree_path TEXT NOT NULL,
    mode TEXT NOT NULL,
    expected_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    execution_generation INTEGER NOT NULL,
    checkpoint_thread_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automation_registry (
    automation_id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL,
    schedule TEXT NOT NULL,
    task_key_template TEXT NOT NULL,
    name TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    last_scheduled_at TEXT,
    last_dispatched_node_id TEXT,
    registration_receipt_json TEXT NOT NULL,
    last_dispatch_receipt_json TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automation_registry_status
    ON automation_registry(status, enabled);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


@dataclass(frozen=True)
class ExecutionLease:
    node_id: str
    execution_generation: int
    lease_owner: str
    fencing_token: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str


@dataclass(frozen=True)
class BackupResult:
    database_path: Path
    manifest_path: Path
    integrity_check_result: str


class DispatchIntentConflict(RuntimeError):
    """Raised when a durable dispatch key is reused with a different request."""


class DispatchIntentNotFound(LookupError):
    """Raised when a caller tries to advance a dispatch that was never prepared."""


class ToolInvocationConflict(RuntimeError):
    """Raised when an idempotency key is reused with different tool arguments."""


class ToolInvocationReconciliationRequired(RuntimeError):
    """Raised when a prior side-effecting invocation has an uncertain outcome."""

    def __init__(self, invocation: dict[str, Any]) -> None:
        self.invocation = invocation
        super().__init__(
            "side-effecting tool invocation requires reconciliation: "
            f"{invocation.get('tool_name')}:{invocation.get('business_idempotency_key')} "
            f"status={invocation.get('status')}"
        )


class RuntimeStorage:
    """Lifecycle owner for the runtime database and official LangGraph stores."""

    def __init__(self, db_path: str | Path = ".onemancompany/data/runtime.sqlite3") -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._reject_formal_database_during_pytest()
        self._conn: aiosqlite.Connection | None = None
        self._checkpoint_conn: aiosqlite.Connection | None = None
        self._store_conn: aiosqlite.Connection | None = None
        self.checkpointer: AsyncSqliteSaver | None = None
        self.memory_store: AsyncSqliteStore | None = None
        self._write_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._initialized = False
        self.memory_vector_enabled = False
        self.memory_index_version = "v1"
        self.memory_index_target_version: str | None = None
        self.memory_reindex_required = False
        self._memory_index_config: dict[str, Any] | None = None
        self._memory_index_lock = asyncio.Lock()

    def _reject_formal_database_during_pytest(self) -> None:
        """Fail closed if a test accidentally targets the repository runtime DB.

        Test fixtures should always pass a temporary database path.  This
        last-line guard prevents a missed or late fixture from applying schema
        migrations to the formal runtime database again.
        """
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            return
        repository_root = Path(__file__).resolve().parents[3]
        formal_database = (repository_root / ".onemancompany" / "data" / "runtime.sqlite3").resolve()
        if self.db_path == formal_database:
            raise RuntimeError(
                "pytest attempted to open the repository formal runtime database; "
                "configure an explicit temporary OMC_DATA_ROOT or database path"
            )

    @staticmethod
    def _normalize_memory_index(memory_index: dict | None) -> dict | None:
        if not memory_index:
            return None
        config = dict(memory_index)
        if "fields" in config and "text_fields" not in config:
            config["text_fields"] = config.pop("fields")
        dimensions = config.get("dims")
        if dimensions is None:
            raise ValueError("memory vector index requires dims")
        config["dims"] = int(dimensions)
        config["text_fields"] = list(config.get("text_fields") or ["text"])
        embedder = config.get("embed")
        config["embedding_model"] = str(
            config.get("embedding_model")
            or (
                f"{type(embedder).__module__}.{type(embedder).__qualname__}"
                if embedder is not None
                else ""
            )
        ).strip()
        config["provider_fingerprint"] = str(
            config.get("provider_fingerprint") or "local-or-unspecified"
        ).strip()
        if not config["embedding_model"]:
            raise ValueError("memory vector index requires embedding_model")
        return config

    async def _migrate_owned_schema(self) -> None:
        """Apply additive OMC-owned migrations without touching LangGraph tables."""
        columns = {
            str(row[1])
            for row in await (
                await self._conn.execute("PRAGMA table_info(memory_index_config)")
            ).fetchall()
        }
        additions = {
            "embedding_model": "TEXT NOT NULL DEFAULT ''",
            "provider_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "activated_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                await self._conn.execute(
                    f"ALTER TABLE memory_index_config ADD COLUMN {name} {definition}"
                )
        # Earlier code marked every newly seen version active. Preserve the most
        # recent row as the only active contract before enforcing versioned use.
        active_rows = await (
            await self._conn.execute(
                "SELECT index_version FROM memory_index_config WHERE active=1 "
                "ORDER BY COALESCE(activated_at,created_at) DESC, index_version DESC"
            )
        ).fetchall()
        if len(active_rows) > 1:
            keep = str(active_rows[0][0])
            await self._conn.execute(
                "UPDATE memory_index_config SET active=CASE WHEN index_version=? THEN 1 ELSE 0 END",
                (keep,),
            )
        await self._conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (_SCHEMA_VERSION, iso_now()),
        )
        await self._conn.commit()

    async def _validate_memory_index(self, memory_index: dict | None) -> dict[str, Any]:
        """Persist the requested contract and identify the safe active version.

        A different version is registered as an inactive reindex target. It is
        never allowed to query or overwrite vectors from the active version.
        """
        active_cursor = await self._conn.execute(
            "SELECT index_version,dimensions,text_fields_json,embedding_model,"
            "provider_fingerprint FROM memory_index_config WHERE active=1 LIMIT 1"
        )
        active_row = await active_cursor.fetchone()
        await active_cursor.close()
        if memory_index is None:
            return {
                "active_version": str(active_row[0]) if active_row else None,
                "requested_version": None,
                "usable": False,
                "reindex_required": False,
            }
        version = str(memory_index.get("index_version") or "v1")
        dimensions = int(memory_index["dims"])
        fields_json = json.dumps(memory_index["text_fields"], ensure_ascii=False, sort_keys=True)
        embedding_model = str(memory_index["embedding_model"])
        provider_fingerprint = str(memory_index["provider_fingerprint"])
        row = await self._conn.execute(
            "SELECT dimensions,text_fields_json,embedding_model,provider_fingerprint,active "
            "FROM memory_index_config WHERE index_version=?",
            (version,),
        )
        existing = await row.fetchone()
        await row.close()
        requested_contract = (dimensions, fields_json, embedding_model, provider_fingerprint)
        existing_contract = tuple(existing[:4]) if existing else None
        if existing and existing_contract != requested_contract:
            raise ValueError(
                f"memory index {version} configuration mismatch: "
                f"existing dims={existing[0]}, fields={existing[1]}, model={existing[2]}, "
                f"provider={existing[3]}; requested dims={dimensions}, fields={fields_json}, "
                f"model={embedding_model}, provider={provider_fingerprint}"
            )
        if not existing:
            activate = 0 if active_row else 1
            now = iso_now()
            await self._conn.execute(
                "INSERT INTO memory_index_config(index_version,dimensions,text_fields_json,"
                "embedding_model,provider_fingerprint,created_at,activated_at,active) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    version,
                    dimensions,
                    fields_json,
                    embedding_model,
                    provider_fingerprint,
                    now,
                    now if activate else None,
                    activate,
                ),
            )
            await self._conn.commit()
            if activate:
                active_row = (version, dimensions, fields_json, embedding_model, provider_fingerprint)
        active_version = str(active_row[0]) if active_row else version
        active_contract = tuple(active_row[1:5]) if active_row else requested_contract
        # A pure version-label bump over the same vector space may keep serving
        # the old index during shadow rebuild. Model/provider/dimension changes
        # fall back to structured retrieval until the atomic switch completes.
        usable = active_version == version or active_contract == requested_contract
        return {
            "active_version": active_version,
            "requested_version": version,
            "usable": usable,
            "reindex_required": active_version != version,
        }

    async def initialize(self, *, memory_index: dict | None = None) -> None:
        if self._initialized:
            return
        self._loop = asyncio.get_running_loop()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._checkpoint_conn = await aiosqlite.connect(self.db_path)
            self._store_conn = await aiosqlite.connect(self.db_path)
            for conn in (self._conn, self._checkpoint_conn, self._store_conn):
                await self._configure(conn)
            await self._conn.executescript(_OWNED_SCHEMA)
            await self._migrate_owned_schema()
            normalized_index = self._normalize_memory_index(memory_index)
            index_state = await self._validate_memory_index(normalized_index)
            self._memory_index_config = normalized_index
            self.memory_vector_enabled = bool(normalized_index and index_state["usable"])
            self.memory_index_version = str(
                index_state.get("active_version")
                or (normalized_index or {}).get("index_version")
                or "v1"
            )
            self.memory_index_target_version = (
                str(index_state["requested_version"])
                if index_state.get("requested_version")
                else None
            )
            self.memory_reindex_required = bool(index_state["reindex_required"])
            # Queued/running Python callables cannot survive restart. Preserve their
            # metadata and make reconciliation explicit rather than claiming success.
            await self._conn.execute(
                "UPDATE provider_queue SET status='holding', last_error_class='process_restart' "
                "WHERE status IN ('queued','running')"
            )
            await self._conn.commit()
            self.checkpointer = AsyncSqliteSaver(self._checkpoint_conn)
            # index_version belongs to the OMC contract table, not LangGraph's
            # vector index configuration.
            store_index = None
            if normalized_index is not None:
                store_index = {
                    key: value for key, value in normalized_index.items()
                    if key in {"dims", "embed", "text_fields"}
                }
            self.memory_store = AsyncSqliteStore(self._store_conn, index=store_index)
            await self.checkpointer.setup()
            await self._checkpoint_conn.commit()
            await self.memory_store.setup()
            await self._store_conn.commit()
            self._initialized = True
        except BaseException:
            # Fail closed and release every partially opened handle.  This path
            # includes vector dimension/version drift and schema setup failure.
            try:
                await self.close()
            except Exception as cleanup_exc:
                # Preserve the original startup exception; cleanup is best effort.
                logger.debug("RuntimeStorage startup cleanup failed: {}", type(cleanup_exc).__name__)
            raise

    @staticmethod
    async def _configure(conn: aiosqlite.Connection) -> None:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=FULL")
        await conn.execute("PRAGMA wal_autocheckpoint=1000")
        await conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("RuntimeStorage is not initialized")
        return self._conn

    async def close(self) -> None:
        if not self._initialized and not any((self._conn, self._checkpoint_conn, self._store_conn)):
            return
        # Cleanup must continue if one partially initialized handle fails.
        first_error: BaseException | None = None
        for conn in (self._store_conn, self._checkpoint_conn, self._conn):
            if conn is None:
                continue
            try:
                await conn.commit()
            except BaseException as exc:
                first_error = first_error or exc
            try:
                await conn.close()
            except BaseException as exc:
                first_error = first_error or exc
        self._conn = self._checkpoint_conn = self._store_conn = None
        self.checkpointer = None
        self.memory_store = None
        self._loop = None
        self._initialized = False
        self.memory_vector_enabled = False
        self.memory_index_target_version = None
        self.memory_reindex_required = False
        self._memory_index_config = None
        if first_error is not None:
            raise first_error

    async def put_memory(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        *,
        index: list[str] | bool | None = None,
    ) -> None:
        """Serialize memory writes against an atomic vector index switch."""
        if self.memory_store is None:
            raise RuntimeError("memory store is not initialized")
        async with self._memory_index_lock:
            await self.memory_store.aput(namespace, key, value, index=index)

    async def memory_index_status(self) -> dict[str, Any]:
        """Return a sanitized version/index state for health and administration."""
        rows = await self.fetchall(
            "SELECT index_version,dimensions,embedding_model,active,created_at,activated_at "
            "FROM memory_index_config ORDER BY created_at,index_version"
        )
        return {
            "active_version": self.memory_index_version if rows else None,
            "target_version": self.memory_index_target_version,
            "vector_enabled": self.memory_vector_enabled,
            "reindex_required": self.memory_reindex_required,
            "versions": [
                {
                    "index_version": str(row[0]),
                    "dimensions": int(row[1]),
                    "embedding_model": str(row[2]),
                    "active": bool(row[3]),
                    "created_at": row[4],
                    "activated_at": row[5],
                }
                for row in rows
            ],
        }

    async def reindex_memory_index(
        self,
        *,
        from_version: str = "",
        to_version: str = "",
        batch_size: int = 64,
    ) -> dict[str, Any]:
        """Build a shadow vector version and atomically switch active vectors.

        Structured memory and the current ``store_vectors`` remain readable while
        embeddings are generated. Memory writes are briefly serialized so no item
        can be omitted between the snapshot and switch. Failed builds leave the
        active vectors, active contract and Memory Outbox untouched.
        """
        if self.memory_store is None or self._store_conn is None:
            raise RuntimeError("memory store is not initialized")
        target = self._memory_index_config
        if not target or self.memory_store.embeddings is None:
            raise RuntimeError("configured embedding index is unavailable")
        active_version = self.memory_index_version
        requested_target = str(target.get("index_version") or "")
        source_version = str(from_version or active_version)
        target_version = str(to_version or requested_target)
        if source_version != active_version:
            raise ValueError(
                f"reindex source must be active version {active_version}, got {source_version}"
            )
        if target_version != requested_target:
            raise ValueError(
                f"reindex target must match configured version {requested_target}, got {target_version}"
            )
        batch_size = max(1, min(int(batch_size), 512))
        dimensions = int(target["dims"])
        tokenized_fields = [
            (field, tokenize_path(field)) for field in target["text_fields"]
        ]

        async with self._memory_index_lock:
            # Snapshot structured rows while writes are fenced. External embedding
            # calls happen with no SQLite transaction open.
            async with self._store_conn.execute(
                "SELECT prefix,key,value FROM store ORDER BY prefix,key"
            ) as cursor:
                store_rows = await cursor.fetchall()
            requests: list[tuple[str, str, str, str]] = []
            for prefix, key, raw_value in store_rows:
                value = json.loads(raw_value)
                for field, tokenized_path in tokenized_fields:
                    texts = get_text_at_path(value, tokenized_path)
                    for position, text in enumerate(texts):
                        pathname = f"{field}.{position}" if len(texts) > 1 else field
                        requests.append((str(prefix), str(key), pathname, text))

            staged: list[tuple[str, str, str, str, bytes, str, str]] = []
            now = iso_now()
            for offset in range(0, len(requests), batch_size):
                chunk = requests[offset : offset + batch_size]
                vectors = await self.memory_store.embeddings.aembed_documents(
                    [item[3] for item in chunk]
                )
                if len(vectors) != len(chunk):
                    raise ValueError("embedding provider returned an incomplete reindex batch")
                for (prefix, key, pathname, _), vector in zip(chunk, vectors, strict=True):
                    if len(vector) != dimensions:
                        raise ValueError(
                            "memory embedding dimension mismatch during reindex: "
                            f"configured={dimensions}, actual={len(vector)}"
                        )
                    staged.append(
                        (
                            target_version,
                            prefix,
                            key,
                            pathname,
                            sqlite_vec.serialize_float32(vector),
                            now,
                            now,
                        )
                    )

            # LangGraph serializes store reads/writes with this lock. The switch,
            # archived source snapshot and active-version metadata update are one
            # SQLite transaction on the same connection.
            async with self.memory_store.lock:
                await self._store_conn.execute("BEGIN IMMEDIATE")
                try:
                    active_row = await (
                        await self._store_conn.execute(
                            "SELECT index_version FROM memory_index_config WHERE active=1 LIMIT 1"
                        )
                    ).fetchone()
                    if not active_row or str(active_row[0]) != source_version:
                        raise RuntimeError("active memory index changed during reindex")
                    await self._store_conn.execute(
                        "DELETE FROM memory_vector_versions WHERE index_version=?",
                        (target_version,),
                    )
                    if source_version != target_version:
                        await self._store_conn.execute(
                            "INSERT OR REPLACE INTO memory_vector_versions("
                            "index_version,prefix,key,field_name,embedding,created_at,updated_at) "
                            "SELECT ?,prefix,key,field_name,embedding,created_at,updated_at "
                            "FROM store_vectors",
                            (source_version,),
                        )
                    if staged:
                        await self._store_conn.executemany(
                            "INSERT INTO memory_vector_versions(index_version,prefix,key,field_name,"
                            "embedding,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                            staged,
                        )
                    await self._store_conn.execute("DELETE FROM store_vectors")
                    await self._store_conn.execute(
                        "INSERT INTO store_vectors(prefix,key,field_name,embedding,created_at,updated_at) "
                        "SELECT prefix,key,field_name,embedding,created_at,updated_at "
                        "FROM memory_vector_versions WHERE index_version=?",
                        (target_version,),
                    )
                    await self._store_conn.execute(
                        "UPDATE memory_index_config SET active=0 WHERE active=1"
                    )
                    await self._store_conn.execute(
                        "UPDATE memory_index_config SET active=1,activated_at=? WHERE index_version=?",
                        (now, target_version),
                    )
                    await self._store_conn.execute(
                        "UPDATE store SET value=json_set(CAST(value AS TEXT),"
                        "'$.embedding_status','indexed','$.embedding_index_version',?),"
                        "updated_at=CURRENT_TIMESTAMP",
                        (target_version,),
                    )
                    await self._store_conn.commit()
                except BaseException:
                    await self._store_conn.rollback()
                    raise

            self.memory_index_version = target_version
            self.memory_vector_enabled = True
            self.memory_reindex_required = False
            await self.append_audit(
                "memory_index_reindexed",
                {
                    "from_version": source_version,
                    "to_version": target_version,
                    "memory_count": len(store_rows),
                    "vector_count": len(staged),
                },
            )
            return {
                "status": "completed",
                "mode": "atomic_shadow_rebuild",
                "from_version": source_version,
                "to_version": target_version,
                "memory_count": len(store_rows),
                "vector_count": len(staged),
            }

    def run_sync(self, coroutine):
        """Run a storage coroutine from a synchronous tool worker.

        LangChain executes synchronous tools in worker threads.  All SQLite
        writes still run on the RuntimeStorage owner loop, preserving the
        single async writer boundary.
        """
        if self._loop is None or not self._loop.is_running():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(coroutine)
            raise RuntimeError("RuntimeStorage owner loop is unavailable")
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self._loop:
            raise RuntimeError("Synchronous runtime storage access cannot block its owner event loop")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._write_lock:
            await self.conn.execute(sql, params)
            await self.conn.commit()

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | tuple | None:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list:
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def list_tables(self) -> set[str]:
        rows = await self.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        return {str(row[0]) for row in rows}

    async def health_check(self) -> bool:
        try:
            row = await self.fetchone("SELECT 1")
            return bool(row and row[0] == 1)
        except Exception:
            return False

    async def integrity_check(self) -> str:
        row = await self.fetchone("PRAGMA integrity_check")
        return str(row[0]) if row else "unavailable"

    async def enqueue_memory_outbox(
        self,
        *,
        namespace: tuple[str, ...],
        memory_key: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> str:
        """Persist a deduplicable memory event before any embedding work."""
        event_id = event_id or uuid.uuid4().hex
        content_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        durable_key = f"{memory_key}:{content_hash}"
        await self.execute(
            "INSERT OR IGNORE INTO memory_outbox(event_id,namespace_json,memory_key,payload_json,status,attempt,next_retry_at,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event_id, json.dumps(list(namespace), ensure_ascii=False), durable_key,
                json.dumps(payload, ensure_ascii=False), "pending", 0, None, iso_now(),
            ),
        )
        row = await self.fetchone(
            "SELECT event_id FROM memory_outbox WHERE memory_key=? ORDER BY created_at LIMIT 1",
            (durable_key,),
        )
        return str(row[0]) if row else event_id

    async def claim_memory_outbox(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Claim pending/retryable events with a short transaction."""
        now = iso_now()
        async with self._write_lock:
            async with self.conn.execute(
                "SELECT event_id,namespace_json,memory_key,payload_json,attempt FROM memory_outbox "
                "WHERE status IN ('pending','holding') AND (next_retry_at IS NULL OR next_retry_at<=?) "
                "ORDER BY created_at LIMIT ?", (now, max(1, int(limit))),
            ) as cursor:
                rows = await cursor.fetchall()
            events = []
            for row in rows:
                await self.conn.execute(
                    "UPDATE memory_outbox SET status='processing',attempt=attempt+1 WHERE event_id=? AND status IN ('pending','holding')",
                    (row[0],),
                )
                events.append({
                    "event_id": str(row[0]),
                    "namespace": tuple(json.loads(row[1])),
                    "memory_key": str(row[2]),
                    "payload": json.loads(row[3]),
                    "attempt": int(row[4]) + 1,
                })
            await self.conn.commit()
            return events

    async def memory_outbox_backlog(self) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) FROM memory_outbox WHERE status IN ('pending','processing','holding')"
        )
        return int(row[0] or 0) if row else 0

    async def finish_memory_outbox(self, event_id: str) -> None:
        await self.execute(
            "UPDATE memory_outbox SET status='completed',processed_at=?,last_error=NULL WHERE event_id=?",
            (iso_now(), event_id),
        )

    async def fail_memory_outbox(self, event_id: str, error: str, *, retry_seconds: int = 30) -> None:
        next_retry = (utc_now() + timedelta(seconds=max(1, retry_seconds))).isoformat()
        await self.execute(
            "UPDATE memory_outbox SET status='holding',next_retry_at=?,last_error=? WHERE event_id=?",
            (next_retry, str(error)[:500], event_id),
        )

    async def append_audit(self, event_type: str, data: dict[str, Any]) -> str:
        event_id = uuid.uuid4().hex
        await self.execute(
            "INSERT INTO audit_events(event_id,event_type,event_data,created_at) VALUES (?,?,?,?)",
            (event_id, event_type, json.dumps(data, ensure_ascii=False, sort_keys=True), iso_now()),
        )
        return event_id

    @staticmethod
    def _dispatch_intent_dict(row: tuple | aiosqlite.Row) -> dict[str, Any]:
        receipt = json.loads(str(row[6])) if row[6] else None
        return {
            "parent_id": str(row[0]),
            "employee_id": str(row[1]),
            "task_key": str(row[2]),
            "request_fingerprint": str(row[3]),
            "node_id": str(row[4]) if row[4] else None,
            "state": str(row[5]),
            "receipt": receipt,
            "created_at": str(row[7]),
            "updated_at": str(row[8]),
        }

    async def get_dispatch_intent(
        self, parent_id: str, employee_id: str, task_key: str
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT parent_id,employee_id,task_key,request_fingerprint,node_id,state,receipt_json,created_at,updated_at "
            "FROM dispatch_intents WHERE parent_id=? AND employee_id=? AND task_key=?",
            (parent_id, employee_id, task_key),
        )
        return self._dispatch_intent_dict(row) if row else None

    async def prepare_dispatch_intent(
        self,
        *,
        parent_id: str,
        employee_id: str,
        task_key: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        """Create or replay the unique durable dispatch intent."""
        now = iso_now()
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await self.conn.execute(
                    "SELECT parent_id,employee_id,task_key,request_fingerprint,node_id,state,receipt_json,created_at,updated_at "
                    "FROM dispatch_intents WHERE parent_id=? AND employee_id=? AND task_key=?",
                    (parent_id, employee_id, task_key),
                )).fetchone()
                if row:
                    if str(row[3]) != request_fingerprint:
                        await self.conn.rollback()
                        raise DispatchIntentConflict(
                            f"dispatch key ({parent_id}, {employee_id}, {task_key}) already exists with a different fingerprint"
                        )
                    await self.conn.rollback()
                    return self._dispatch_intent_dict(row)
                await self.conn.execute(
                    "INSERT INTO dispatch_intents(parent_id,employee_id,task_key,request_fingerprint,node_id,state,receipt_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,NULL,'prepared',NULL,?,?)",
                    (parent_id, employee_id, task_key, request_fingerprint, now, now),
                )
                await self.conn.commit()
            except BaseException:
                if self.conn.in_transaction:
                    await self.conn.rollback()
                raise
        intent = await self.get_dispatch_intent(parent_id, employee_id, task_key)
        if intent is None:  # pragma: no cover
            raise DispatchIntentNotFound(task_key)
        return intent

    async def advance_dispatch_intent(
        self,
        *,
        parent_id: str,
        employee_id: str,
        task_key: str,
        request_fingerprint: str,
        state: str,
        node_id: str | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance a dispatch state monotonically and persist reconciliation data."""
        order = {"prepared": 0, "tree_written": 1, "index_written": 2, "scheduled": 3, "started": 4}
        if state not in order:
            raise ValueError(f"Unknown dispatch intent state: {state}")
        now = iso_now()
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await self.conn.execute(
                    "SELECT parent_id,employee_id,task_key,request_fingerprint,node_id,state,receipt_json,created_at,updated_at "
                    "FROM dispatch_intents WHERE parent_id=? AND employee_id=? AND task_key=?",
                    (parent_id, employee_id, task_key),
                )).fetchone()
                if not row:
                    await self.conn.rollback()
                    raise DispatchIntentNotFound(task_key)
                if str(row[3]) != request_fingerprint:
                    await self.conn.rollback()
                    raise DispatchIntentConflict(task_key)
                old_state = str(row[5])
                next_state = state if order[state] >= order.get(old_state, -1) else old_state
                next_node_id = node_id or (str(row[4]) if row[4] else None)
                if row[4] and node_id and str(row[4]) != node_id:
                    await self.conn.rollback()
                    raise DispatchIntentConflict(
                        f"dispatch intent {task_key} is already bound to node {row[4]}"
                    )
                next_receipt = receipt if receipt is not None else (json.loads(str(row[6])) if row[6] else None)
                await self.conn.execute(
                    "UPDATE dispatch_intents SET node_id=?,state=?,receipt_json=?,updated_at=? "
                    "WHERE parent_id=? AND employee_id=? AND task_key=?",
                    (
                        next_node_id,
                        next_state,
                        json.dumps(next_receipt, ensure_ascii=False, sort_keys=True) if next_receipt is not None else None,
                        now,
                        parent_id,
                        employee_id,
                        task_key,
                    ),
                )
                await self.conn.commit()
            except BaseException:
                if self.conn.in_transaction:
                    await self.conn.rollback()
                raise
        intent = await self.get_dispatch_intent(parent_id, employee_id, task_key)
        if intent is None:  # pragma: no cover
            raise DispatchIntentNotFound(task_key)
        return intent

    @staticmethod
    def _tool_invocation_dict(row: tuple | aiosqlite.Row) -> dict[str, Any]:
        return {
            "node_id": str(row[0]),
            "execution_generation": int(row[1]),
            "tool_name": str(row[2]),
            "tool_call_id": str(row[3]),
            "business_idempotency_key": str(row[4]),
            "request_fingerprint": str(row[5]),
            "result_reference": str(row[6]) if row[6] else None,
            "result": json.loads(str(row[7])) if row[7] else None,
            "status": str(row[8]),
            "created_at": str(row[9]),
            "updated_at": str(row[10]),
        }

    async def get_tool_invocation(
        self,
        *,
        node_id: str,
        execution_generation: int,
        tool_name: str,
        business_idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = await self.fetchone(
            "SELECT node_id,execution_generation,tool_name,tool_call_id,business_idempotency_key,"
            "request_fingerprint,result_reference,result_json,status,created_at,updated_at "
            "FROM tool_invocation_ledger WHERE node_id=? AND execution_generation=? "
            "AND tool_name=? AND business_idempotency_key=?",
            (node_id, execution_generation, tool_name, business_idempotency_key),
        )
        return self._tool_invocation_dict(row) if row else None

    async def prepare_tool_invocation(
        self,
        *,
        node_id: str,
        execution_generation: int,
        tool_name: str,
        tool_call_id: str,
        business_idempotency_key: str,
        request_fingerprint: str,
    ) -> dict[str, Any]:
        """Durably reserve a side-effect boundary or replay a completed result.

        Any non-terminal row from a previous execution has an unknown external
        outcome.  It is deliberately not retried: a reconciler or human must
        confirm the business state first.
        """
        now = iso_now()
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await self.conn.execute(
                    "SELECT node_id,execution_generation,tool_name,tool_call_id,business_idempotency_key,"
                    "request_fingerprint,result_reference,result_json,status,created_at,updated_at "
                    "FROM tool_invocation_ledger WHERE node_id=? AND execution_generation=? "
                    "AND tool_name=? AND business_idempotency_key=?",
                    (node_id, execution_generation, tool_name, business_idempotency_key),
                )).fetchone()
                if row:
                    invocation = self._tool_invocation_dict(row)
                    if invocation["request_fingerprint"] != request_fingerprint:
                        await self.conn.rollback()
                        raise ToolInvocationConflict(
                            f"tool invocation key {business_idempotency_key!r} for {tool_name} "
                            "already exists with different arguments"
                        )
                    await self.conn.rollback()
                    if invocation["status"] == "completed":
                        invocation["replayed"] = True
                        return invocation
                    raise ToolInvocationReconciliationRequired(invocation)
                await self.conn.execute(
                    "INSERT INTO tool_invocation_ledger(node_id,execution_generation,tool_name,tool_call_id,"
                    "business_idempotency_key,request_fingerprint,result_reference,result_json,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,NULL,NULL,'prepared',?,?)",
                    (
                        node_id,
                        execution_generation,
                        tool_name,
                        tool_call_id,
                        business_idempotency_key,
                        request_fingerprint,
                        now,
                        now,
                    ),
                )
                await self.conn.commit()
            except BaseException:
                if self.conn.in_transaction:
                    await self.conn.rollback()
                raise
        invocation = await self.get_tool_invocation(
            node_id=node_id,
            execution_generation=execution_generation,
            tool_name=tool_name,
            business_idempotency_key=business_idempotency_key,
        )
        if invocation is None:  # pragma: no cover
            raise RuntimeError("tool invocation reservation disappeared")
        invocation["replayed"] = False
        return invocation

    async def complete_tool_invocation(
        self,
        *,
        node_id: str,
        execution_generation: int,
        tool_name: str,
        business_idempotency_key: str,
        request_fingerprint: str,
        result: dict[str, Any],
        result_reference: str | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await self.conn.execute(
                    "SELECT request_fingerprint,status FROM tool_invocation_ledger "
                    "WHERE node_id=? AND execution_generation=? AND tool_name=? AND business_idempotency_key=?",
                    (node_id, execution_generation, tool_name, business_idempotency_key),
                )).fetchone()
                if not row:
                    await self.conn.rollback()
                    raise LookupError("tool invocation was not prepared")
                if str(row[0]) != request_fingerprint:
                    await self.conn.rollback()
                    raise ToolInvocationConflict(business_idempotency_key)
                if str(row[1]) == "completed":
                    await self.conn.rollback()
                else:
                    await self.conn.execute(
                        "UPDATE tool_invocation_ledger SET result_reference=?,result_json=?,status='completed',updated_at=? "
                        "WHERE node_id=? AND execution_generation=? AND tool_name=? AND business_idempotency_key=?",
                        (
                            result_reference,
                            result_json,
                            now,
                            node_id,
                            execution_generation,
                            tool_name,
                            business_idempotency_key,
                        ),
                    )
                    await self.conn.commit()
            except BaseException:
                if self.conn.in_transaction:
                    await self.conn.rollback()
                raise
        invocation = await self.get_tool_invocation(
            node_id=node_id,
            execution_generation=execution_generation,
            tool_name=tool_name,
            business_idempotency_key=business_idempotency_key,
        )
        if invocation is None:  # pragma: no cover
            raise RuntimeError("completed tool invocation disappeared")
        return invocation

    async def fail_tool_invocation(
        self,
        *,
        node_id: str,
        execution_generation: int,
        tool_name: str,
        business_idempotency_key: str,
        request_fingerprint: str,
        error: str,
    ) -> None:
        """Persist an uncertain side-effect failure without making it replayable."""
        safe_error = str(error)[:1000]
        await self.execute(
            "UPDATE tool_invocation_ledger SET status='failed',result_json=?,updated_at=? "
            "WHERE node_id=? AND execution_generation=? AND tool_name=? "
            "AND business_idempotency_key=? AND request_fingerprint=? AND status!='completed'",
            (
                json.dumps({"status": "error", "message": safe_error}, ensure_ascii=False),
                iso_now(),
                node_id,
                execution_generation,
                tool_name,
                business_idempotency_key,
                request_fingerprint,
            ),
        )

    async def record_recovery(
        self,
        *,
        node_id: str,
        tree_path: str,
        mode: str,
        expected_status: str,
        reason: str,
        execution_generation: int,
        checkpoint_thread_id: str,
        status: str,
    ) -> str:
        """Append or refresh an idempotent checkpoint reconciliation finding."""
        identity = json.dumps(
            {
                "node_id": node_id,
                "tree_path": str(Path(tree_path).resolve()) if tree_path else "",
                "reason": reason,
                "thread_id": checkpoint_thread_id,
                "status": status,
            },
            sort_keys=True,
        )
        recovery_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        now = iso_now()
        await self.execute(
            "INSERT INTO recoveries(recovery_id,node_id,tree_path,mode,expected_status,reason,"
            "execution_generation,checkpoint_thread_id,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(recovery_id) DO UPDATE SET "
            "expected_status=excluded.expected_status,status=excluded.status,updated_at=excluded.updated_at",
            (
                recovery_id,
                node_id,
                tree_path,
                mode,
                expected_status,
                reason,
                execution_generation,
                checkpoint_thread_id,
                status,
                now,
                now,
            ),
        )
        return recovery_id

    async def mark_dispatch_started_for_node(
        self, node_id: str, receipt: dict[str, Any]
    ) -> bool:
        """Mark the intent for ``node_id`` started at the executor boundary."""
        now = iso_now()
        async with self._write_lock:
            cursor = await self.conn.execute(
                "UPDATE dispatch_intents SET state='started',receipt_json=?,updated_at=? WHERE node_id=?",
                (json.dumps(receipt, ensure_ascii=False, sort_keys=True), now, node_id),
            )
            await self.conn.commit()
            return cursor.rowcount == 1

    async def acquire_lease(
        self, node_id: str, generation: int, owner: str, *, ttl_seconds: int = 60
    ) -> ExecutionLease | None:
        now = utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await (await self.conn.execute(
                    "SELECT lease_owner,fencing_token,expires_at FROM execution_leases "
                    "WHERE node_id=? AND execution_generation=?",
                    (node_id, generation),
                )).fetchone()
                if row and datetime.fromisoformat(str(row[2])) > now:
                    await self.conn.rollback()
                    return None
                token = int(row[1]) + 1 if row else 1
                values = (node_id, generation, owner, token, now.isoformat(), now.isoformat(), expires.isoformat())
                await self.conn.execute(
                    "INSERT INTO execution_leases(node_id,execution_generation,lease_owner,fencing_token,acquired_at,heartbeat_at,expires_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(node_id,execution_generation) DO UPDATE SET "
                    "lease_owner=excluded.lease_owner,fencing_token=excluded.fencing_token,acquired_at=excluded.acquired_at," 
                    "heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at",
                    values,
                )
                await self.conn.commit()
                return ExecutionLease(node_id, generation, owner, token, now.isoformat(), now.isoformat(), expires.isoformat())
            except BaseException:
                await self.conn.rollback()
                raise

    async def heartbeat_lease(self, lease: ExecutionLease, *, ttl_seconds: int = 60) -> bool:
        now = utc_now()
        async with self._write_lock:
            cursor = await self.conn.execute(
                "UPDATE execution_leases SET heartbeat_at=?,expires_at=? WHERE node_id=? AND execution_generation=? "
                "AND lease_owner=? AND fencing_token=?",
                (now.isoformat(), (now + timedelta(seconds=ttl_seconds)).isoformat(), lease.node_id,
                 lease.execution_generation, lease.lease_owner, lease.fencing_token),
            )
            await self.conn.commit()
            return cursor.rowcount == 1

    async def release_lease(self, lease: ExecutionLease) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "DELETE FROM execution_leases WHERE node_id=? AND execution_generation=? AND lease_owner=? AND fencing_token=?",
                (lease.node_id, lease.execution_generation, lease.lease_owner, lease.fencing_token),
            )
            await self.conn.commit()
            return cursor.rowcount == 1

    async def validate_fencing_token(self, node_id: str, generation: int, token: int) -> bool:
        row = await self.fetchone(
            "SELECT fencing_token,expires_at FROM execution_leases WHERE node_id=? AND execution_generation=?",
            (node_id, generation),
        )
        return bool(row and int(row[0]) == token and datetime.fromisoformat(str(row[1])) > utc_now())

    async def force_expire_lease(self, node_id: str, generation: int) -> None:
        await self.execute(
            "UPDATE execution_leases SET expires_at=? WHERE node_id=? AND execution_generation=?",
            ((utc_now() - timedelta(seconds=1)).isoformat(), node_id, generation),
        )

    async def backup(
        self,
        backup_dir: str | Path,
        *,
        application_version: str = "unknown",
        backup_id: str | None = None,
    ) -> BackupResult:
        backup_dir = Path(backup_dir).expanduser().resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = backup_id or utc_now().strftime("%Y%m%dT%H%M%SZ")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", stamp):
            raise ValueError("backup_id must contain only letters, digits, dot, underscore, or hyphen")
        target = backup_dir / f"runtime-{stamp}.sqlite3"
        # sqlite3 backup API produces a transactionally consistent image while WAL writes continue.
        def _copy() -> None:
            source = sqlite3.connect(self.db_path)
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        await asyncio.to_thread(_copy)
        def _verify() -> dict[str, Any]:
            conn = sqlite3.connect(target)
            try:
                header = target.read_bytes()[:16]
                if header != b"SQLite format 3\x00":
                    raise RuntimeError("backup is not a SQLite 3 database")
                quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
                integrity_check = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
                page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
                page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
                tables = sorted(
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                )
                required = {"checkpoints", "store", "memory_outbox", "audit_events"}
                missing = required - set(tables)
                if missing:
                    raise RuntimeError("backup missing required tables: " + ",".join(sorted(missing)))
                if quick_check != "ok" or integrity_check != "ok":
                    raise RuntimeError(
                        f"backup verification failed: quick_check={quick_check}, integrity_check={integrity_check}"
                    )
                return {
                    "quick_check_result": quick_check,
                    "integrity_check_result": integrity_check,
                    "sqlite_page_count": page_count,
                    "sqlite_page_size": page_size,
                    "verified_tables": tables,
                }
            finally:
                conn.close()
        verification = await asyncio.to_thread(_verify)
        checksum = await asyncio.to_thread(lambda: hashlib.sha256(target.read_bytes()).hexdigest())
        manifest_path = target.with_suffix(target.suffix + ".manifest.json")
        manifest = {
            "backup_id": stamp,
            "schema_version": _SCHEMA_VERSION,
            "application_version": application_version,
            "created_at": iso_now(),
            "database_path": str(target),
            "manifest_path": str(manifest_path),
            "database_size_bytes": target.stat().st_size,
            "database_checksum": f"sha256:{checksum}",
            "backup_method": "sqlite_online_backup_api",
            **verification,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        await self.append_audit("sqlite_backup_completed", {"path": str(target), **manifest})
        return BackupResult(target, manifest_path, str(verification["integrity_check_result"]))


_runtime_storage: RuntimeStorage | None = None


def get_runtime_storage() -> RuntimeStorage | None:
    return _runtime_storage


def set_runtime_storage(storage: RuntimeStorage | None) -> None:
    global _runtime_storage
    _runtime_storage = storage
