#!/usr/bin/env python3
"""Verify durable embedding pending/backoff/recovery in an isolated data root.

The configured provider is probed for real, then a controlled transport outage
is injected through the same embedding adapter used by the SQLite vector store.
The gate proves that structured memory survives, the outbox holds durably, and
the same memory is indexed after the provider path is restored. Formal runtime
data is never opened or imported.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DATA_ROOT = (ROOT / ".onemancompany").resolve()
DEFAULT_REPORT = ROOT / "docs/24h-work-mode/reports/EMBEDDING-RECOVERY-GATE-REPORT.json"
REQUIRED_ENV = (
    "OMC_MEMORY_EMBEDDING_BASE_URL",
    "OMC_MEMORY_EMBEDDING_API_KEY",
    "OMC_MEMORY_EMBEDDING_MODEL",
    "OMC_MEMORY_EMBEDDING_DIMENSIONS",
    "OMC_MEMORY_INDEX_VERSION",
)


def _bootstrap_venv() -> None:
    try:
        import langchain_openai  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ModuleNotFoundError:
        venv_python = ROOT / ".venv/bin/python"
        if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _fresh_data_root(requested: Path | None) -> tuple[Path, bool]:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="omc-embedding-recovery-gate-")).resolve(), True
    path = requested.expanduser().resolve()
    if path == FORMAL_DATA_ROOT or _is_relative_to(path, FORMAL_DATA_ROOT):
        raise ValueError("embedding recovery gate data root must not be the formal .onemancompany tree")
    if path.exists() and any(path.iterdir()):
        raise ValueError("embedding recovery gate requires a new or empty data root")
    path.mkdir(parents=True, exist_ok=True)
    return path, False


def _config_from_env() -> dict[str, Any]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise ValueError("missing required embedding configuration: " + ", ".join(missing))
    try:
        dimensions = int(os.environ["OMC_MEMORY_EMBEDDING_DIMENSIONS"])
    except ValueError as exc:
        raise ValueError("OMC_MEMORY_EMBEDDING_DIMENSIONS must be an integer") from exc
    if dimensions <= 0:
        raise ValueError("OMC_MEMORY_EMBEDDING_DIMENSIONS must be positive")
    return {
        "base_url": os.environ["OMC_MEMORY_EMBEDDING_BASE_URL"].strip(),
        "api_key": os.environ["OMC_MEMORY_EMBEDDING_API_KEY"].strip(),
        "model": os.environ["OMC_MEMORY_EMBEDDING_MODEL"].strip(),
        "dimensions": dimensions,
        "index_version": os.environ["OMC_MEMORY_INDEX_VERSION"].strip(),
    }


async def _run_gate(data_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    os.environ["OMC_DATA_ROOT"] = str(data_root)
    os.environ["OMC_MEMORY_ENABLED"] = "true"
    os.environ["OMC_AUTOMATION_ENABLED"] = "false"
    os.environ["OMC_RESTORE_PERSISTED_TASKS"] = "false"

    from langchain_core.embeddings import Embeddings
    from langchain_openai import OpenAIEmbeddings
    from onemancompany.core.memory_service import MemoryService
    from onemancompany.core.memory_worker import MemoryOutboxWorker
    from onemancompany.core.runtime_storage import RuntimeStorage

    delegate = OpenAIEmbeddings(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
        dimensions=config["dimensions"],
        check_embedding_ctx_length=False,
    )
    probe = await delegate.aembed_query("onemancompany embedding recovery gate probe")
    if len(probe) != config["dimensions"]:
        raise ValueError(
            "embedding dimension mismatch: "
            f"configured={config['dimensions']}, actual={len(probe)}"
        )

    class ControlledTransportEmbedding(Embeddings):
        blocked = True

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            if self.blocked:
                raise ConnectionError("controlled embedding transport outage")
            return delegate.embed_documents(texts)

        def embed_query(self, text: str) -> list[float]:
            if self.blocked:
                raise ConnectionError("controlled embedding transport outage")
            return delegate.embed_query(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            if self.blocked:
                raise ConnectionError("controlled embedding transport outage")
            return await delegate.aembed_documents(texts)

        async def aembed_query(self, text: str) -> list[float]:
            if self.blocked:
                raise ConnectionError("controlled embedding transport outage")
            return await delegate.aembed_query(text)

    controlled = ControlledTransportEmbedding()
    storage = RuntimeStorage(data_root / "data/runtime.sqlite3")
    await storage.initialize(memory_index={
        "dims": config["dimensions"],
        "embed": controlled,
        "text_fields": ["text"],
        "index_version": config["index_version"],
        "embedding_model": config["model"],
        "provider_fingerprint": hashlib.sha256(
            config["base_url"].rstrip("/").encode("utf-8")
        ).hexdigest(),
        "provider_available": False,
    })
    try:
        payload = {
            "employee_id": "00008",
            "scope": "employee",
            "memory_type": "episodic",
            "subject": "embedding_recovery_gate",
            "text": "Structured memory remains durable while vector embedding is unavailable.",
            "source_node_id": "embedding-recovery-node",
            "source_iteration_id": "iter_embedding_recovery",
            "source_thread_id": "omc:embedding-recovery:iter_001:node:g1",
            "evidence_refs": ["gate:controlled-transport-outage"],
        }
        await storage.enqueue_memory_outbox(
            namespace=("employee", "00008", "episodic"),
            memory_key="embedding-recovery-node:episodic",
            payload=payload,
            event_id="embedding-recovery-event",
        )
        worker = MemoryOutboxWorker(storage)
        first = (await storage.claim_memory_outbox(limit=1))[0]
        await worker._process(first)

        held = await storage.fetchone(
            "SELECT status,attempt,next_retry_at,last_error FROM memory_outbox WHERE event_id=?",
            ("embedding-recovery-event",),
        )
        pending = await MemoryService(storage).search(employee_id="00008", query="")
        vector_count_before = int((await storage.fetchone("SELECT COUNT(*) FROM store_vectors"))[0])
        if len(pending) != 1:
            raise AssertionError("structured pending memory was not persisted exactly once")
        memory_id = str(pending[0]["memory_id"])

        controlled.blocked = False
        await storage.execute(
            "UPDATE memory_outbox SET next_retry_at=NULL WHERE event_id=?",
            ("embedding-recovery-event",),
        )
        second = (await storage.claim_memory_outbox(limit=1))[0]
        await worker._process(second)

        completed = await storage.fetchone(
            "SELECT status,attempt,next_retry_at,last_error FROM memory_outbox WHERE event_id=?",
            ("embedding-recovery-event",),
        )
        recovered = await MemoryService(storage).search(
            employee_id="00008", query="durable vector recovery"
        )
        vector_count_after = int((await storage.fetchone("SELECT COUNT(*) FROM store_vectors"))[0])
        store_count = int((await storage.fetchone("SELECT COUNT(*) FROM store"))[0])
        index_status = await storage.memory_index_status()
        checks = {
            "real_provider_probe_healthy": len(probe) == config["dimensions"],
            "failure_status_holding": bool(held and held[0] == "holding"),
            "failure_attempt_recorded": bool(held and int(held[1]) == 1),
            "failure_next_retry_recorded": bool(held and held[2]),
            "failure_class_sanitized": bool(held and held[3] == "ConnectionError"),
            "structured_memory_persisted": pending[0].get("embedding_status") == "pending",
            "no_vector_during_outage": vector_count_before == 0,
            "recovery_status_completed": bool(completed and completed[0] == "completed"),
            "recovery_attempt_recorded": bool(completed and int(completed[1]) == 2),
            "recovery_retry_state_cleared": bool(completed and completed[2] is None and completed[3] is None),
            "same_memory_reused": len(recovered) == 1 and recovered[0].get("memory_id") == memory_id,
            "embedding_marked_indexed": len(recovered) == 1 and recovered[0].get("embedding_status") == "indexed",
            "semantic_score_available": len(recovered) == 1 and recovered[0].get("score") is not None,
            "single_structured_record": store_count == 1,
            "single_vector_record": vector_count_after == 1,
            "provider_health_recovered": bool(
                index_status.get("embedding_available") and index_status.get("vector_enabled")
            ),
            "formal_outbox_not_imported": int(
                (await storage.fetchone("SELECT COUNT(*) FROM memory_outbox"))[0]
            ) == 1,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise AssertionError("embedding recovery gate checks failed: " + ", ".join(failed))
        return {
            "checks": checks,
            "outbox_after_failure": {
                "status": str(held[0]),
                "attempt": int(held[1]),
                "next_retry_recorded": bool(held[2]),
                "last_error": str(held[3]),
            },
            "outbox_after_recovery": {
                "status": str(completed[0]),
                "attempt": int(completed[1]),
                "next_retry_at": completed[2],
                "last_error": completed[3],
            },
            "memory_id_reused": memory_id,
            "vector_count_before": vector_count_before,
            "vector_count_after": vector_count_after,
            "store_count": store_count,
            "memory_index": index_status,
            "sqlite_integrity": await storage.integrity_check(),
        }
    finally:
        await storage.close()


def main() -> int:
    _bootstrap_venv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, help="new or empty isolated OMC data root")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--keep-data-root", action="store_true")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "gate": "real_embedding_recovery_isolated",
        "checked_date": "2026-08-14",
        "started_at": _now(),
        "formal_data_root": str(FORMAL_DATA_ROOT),
        "formal_outbox_touched": False,
        "formal_launch_allowed": False,
        "fault_injection": "controlled_embedding_transport_outage",
    }
    data_root: Path | None = None
    ephemeral = False
    try:
        config = _config_from_env()
        data_root, ephemeral = _fresh_data_root(args.data_root)
        report["configuration"] = {
            "provider_fingerprint": hashlib.sha256(
                config["base_url"].rstrip("/").encode("utf-8")
            ).hexdigest(),
            "model": config["model"],
            "dimensions": config["dimensions"],
            "index_version": config["index_version"],
        }
        report["isolated_data_root"] = str(data_root)
        report["result"] = asyncio.run(_run_gate(data_root, config))
        report["status"] = "passed"
        return_code = 0
    except Exception as exc:  # noqa: BLE001 - gate must sanitize every failure
        report["status"] = "blocked" if isinstance(exc, ValueError) else "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:1000]
        return_code = 2 if report["status"] == "blocked" else 1
    finally:
        report["completed_at"] = _now()
        report_path = args.report.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if data_root and ephemeral and not args.keep_data_root:
            shutil.rmtree(data_root, ignore_errors=True)
        print(json.dumps({
            "status": report["status"],
            "report": str(report_path),
            "formal_outbox_touched": False,
        }, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
