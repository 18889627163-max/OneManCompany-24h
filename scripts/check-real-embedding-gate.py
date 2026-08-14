#!/usr/bin/env python3
"""Run the real-cloud embedding gate inside a fresh isolated OMC data root.

The gate never loads or mutates the repository's formal Runtime SQLite. It
probes an explicitly configured OpenAI-compatible embedding endpoint, creates
an isolated sqlite-vec index, verifies vector retrieval plus memory ACL/status
filters, checks prompt injection budgets, and writes a sanitized JSON report.
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
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORMAL_DATA_ROOT = (ROOT / ".onemancompany").resolve()
DEFAULT_REPORT = ROOT / "docs/24h-work-mode/reports/REAL-EMBEDDING-GATE-REPORT.json"
REQUIRED_ENV = (
    "OMC_MEMORY_EMBEDDING_BASE_URL",
    "OMC_MEMORY_EMBEDDING_API_KEY",
    "OMC_MEMORY_EMBEDDING_MODEL",
    "OMC_MEMORY_EMBEDDING_DIMENSIONS",
    "OMC_MEMORY_INDEX_VERSION",
)


def _bootstrap_venv() -> None:
    try:
        import yaml  # noqa: F401
        import langchain_openai  # noqa: F401
        import sqlite_vec  # noqa: F401
    except ModuleNotFoundError:
        venv_python = ROOT / ".venv/bin/python"
        if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.rstrip("/").encode("utf-8")).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _fresh_data_root(requested: Path | None) -> tuple[Path, bool]:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="omc-real-embedding-gate-")).resolve(), True
    path = requested.expanduser().resolve()
    if path == FORMAL_DATA_ROOT or _is_relative_to(path, FORMAL_DATA_ROOT):
        raise ValueError("embedding gate data root must not be the formal .onemancompany tree")
    if path.exists() and any(path.iterdir()):
        raise ValueError("embedding gate requires a new or empty data root")
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


def _write_isolated_identity(data_root: Path) -> None:
    import yaml

    employees = data_root / "company/human_resource/employees"
    for employee_id in ("00007", "00008", "00009", "00010"):
        employee_dir = employees / employee_id
        employee_dir.mkdir(parents=True, exist_ok=True)
        (employee_dir / "profile.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": f"Embedding Gate {employee_id}",
                    "role": "Gate Test Employee",
                    "skills": ["memory-gate"],
                    "employee_number": employee_id,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    project_dir = data_root / "company/business/projects/embedding-gate"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "project_id": "embedding-gate",
                "team": [{"employee_id": "00008"}, {"employee_id": "00009"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _rendered_chars(rows: list[dict[str, Any]]) -> int:
    return sum(
        len(
            f"{row.get('memory_id')} {row.get('scope')} {row.get('status')} "
            f"{row.get('source_node_id')} {row.get('text', '')}"
        )
        for row in rows
    )


async def _run_gate(data_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    # OMC_DATA_ROOT must be fixed before importing modules with path globals.
    os.environ["OMC_DATA_ROOT"] = str(data_root)
    os.environ["OMC_MEMORY_ENABLED"] = "true"
    os.environ["OMC_AUTOMATION_ENABLED"] = "false"
    os.environ["OMC_RESTORE_PERSISTED_TASKS"] = "false"

    from langchain_openai import OpenAIEmbeddings
    from onemancompany.core.memory_service import MemoryService
    from onemancompany.core.runtime_storage import RuntimeStorage
    from onemancompany.main import _prepare_memory_index

    probe_client = OpenAIEmbeddings(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
        dimensions=config["dimensions"],
    )
    try:
        direct_probe = await probe_client.aembed_query("onemancompany real embedding gate probe")
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        suffix = f" status_code={status_code}" if status_code is not None else ""
        raise RuntimeError(
            f"embedding endpoint probe failed: {type(exc).__name__}{suffix}"
        ) from None
    if len(direct_probe) != config["dimensions"]:
        raise ValueError(
            "embedding dimension mismatch: "
            f"configured={config['dimensions']}, actual={len(direct_probe)}"
        )

    settings = SimpleNamespace(
        omc_memory_enabled=True,
        omc_memory_embedding_base_url=config["base_url"],
        omc_memory_embedding_api_key=config["api_key"],
        omc_memory_embedding_model=config["model"],
        omc_memory_embedding_dimensions=config["dimensions"],
        omc_memory_index_version=config["index_version"],
    )
    memory_index, embedding_status, vector_status = await _prepare_memory_index(settings)
    if memory_index is None or embedding_status != "healthy" or vector_status != "healthy":
        raise RuntimeError("real embedding probe did not return a usable vector index")

    db_path = data_root / "data/runtime.sqlite3"
    storage = RuntimeStorage(db_path)
    await storage.initialize(memory_index=memory_index)
    try:
        service = MemoryService(storage)
        own = await service.propose(
            employee_id="00008",
            memory_type="episodic",
            subject="checkpoint_recovery",
            text="Resume from the durable checkpoint and never replay a completed side effect.",
            scope="employee",
            source_node_id="embedding-gate-private-node",
            source_thread_id="omc:embedding-gate:iter_001:private:g1",
        )
        other_private = await service.propose(
            employee_id="00007",
            memory_type="episodic",
            subject="private_recovery_note",
            text="A highly relevant private note about checkpoint side-effect replay.",
            scope="employee",
            source_node_id="embedding-gate-other-private-node",
        )
        verified = await service.propose(
            employee_id="00008",
            memory_type="semantic",
            subject="recovery_authority",
            text="TaskTree receipts and acceptance audit are authoritative during checkpoint recovery.",
            scope="project",
            project_id="embedding-gate/iter_001",
            evidence_refs=["tool-receipt:embedding-gate-1"],
            source_node_id="embedding-gate-project-node",
            source_iteration_id="iter_001",
            source_thread_id="omc:embedding-gate:iter_001:project:g1",
            confidence=1.0,
            trusted_source=True,
        )
        candidate = await service.propose(
            employee_id="00008",
            memory_type="semantic",
            subject="unverified_claim",
            text="A model-only claim says the recovery task passed.",
            scope="project",
            project_id="embedding-gate/iter_001",
            source_node_id="embedding-gate-candidate-node",
        )

        query = "How should checkpoint recovery avoid replaying completed side effects?"
        member_rows = await service.search(
            employee_id="00008",
            project_id="embedding-gate/iter_001",
            query=query,
            limit=8,
            max_chars=6000,
        )
        teammate_rows = await service.search(
            employee_id="00009",
            project_id="embedding-gate/iter_001",
            query=query,
            limit=8,
            max_chars=6000,
        )
        outsider_rows = await service.search(
            employee_id="00010",
            project_id="embedding-gate/iter_001",
            query=query,
            limit=8,
            max_chars=6000,
        )
        budget_rows = await service.search(
            employee_id="00008",
            project_id="embedding-gate/iter_001",
            query=query,
            limit=8,
            max_chars=320,
        )

        member_ids = {row["memory_id"] for row in member_rows}
        teammate_ids = {row["memory_id"] for row in teammate_rows}
        outsider_ids = {row["memory_id"] for row in outsider_rows}
        required_metadata = {
            "memory_id", "scope", "status", "source_node_id", "verified_at", "expires_at"
        }
        checks = {
            "probe_healthy": embedding_status == "healthy" and vector_status == "healthy",
            "vector_index_enabled": storage.memory_vector_enabled,
            "own_private_retrieved": own["memory_id"] in member_ids,
            "other_private_filtered": other_private["memory_id"] not in member_ids,
            "verified_project_retrieved": verified["memory_id"] in member_ids,
            "candidate_filtered": candidate["memory_id"] not in member_ids,
            "teammate_can_read_project": verified["memory_id"] in teammate_ids,
            "teammate_cannot_read_private": own["memory_id"] not in teammate_ids,
            "outsider_cannot_read_project": verified["memory_id"] not in outsider_ids,
            "deduplicated": len(member_ids) == len(member_rows),
            "metadata_complete": all(required_metadata.issubset(row) for row in member_rows),
            "default_prompt_budget": len(member_rows) <= 8 and _rendered_chars(member_rows) <= 6000,
            "tight_prompt_budget": len(budget_rows) <= 8 and _rendered_chars(budget_rows) <= 320,
            "semantic_scores_present": any(row.get("score") is not None for row in member_rows),
        }
        vector_count_row = await storage.fetchone("SELECT COUNT(*) FROM store_vectors")
        outbox_count_row = await storage.fetchone("SELECT COUNT(*) FROM memory_outbox")
        checks["vectors_persisted"] = bool(vector_count_row and int(vector_count_row[0]) >= 4)
        checks["formal_outbox_not_imported"] = bool(outbox_count_row and int(outbox_count_row[0]) == 0)
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise AssertionError("embedding gate checks failed: " + ", ".join(failed))

        index_status = await storage.memory_index_status()
        integrity = await storage.integrity_check()
        async with storage._store_conn.execute("SELECT vec_version()") as cursor:
            sqlite_vec_row = await cursor.fetchone()
        return {
            "checks": checks,
            "embedding_status": embedding_status,
            "vector_status": vector_status,
            "sqlite_integrity": integrity,
            "sqlite_vec_version": str(sqlite_vec_row[0]),
            "memory_index": index_status,
            "vector_count": int(vector_count_row[0]),
            "outbox_count": int(outbox_count_row[0]),
            "retrieval": {
                "member_count": len(member_rows),
                "teammate_count": len(teammate_rows),
                "outsider_count": len(outsider_rows),
                "budget_count": len(budget_rows),
                "member_rendered_chars": _rendered_chars(member_rows),
                "budget_rendered_chars": _rendered_chars(budget_rows),
                "member_scopes": [row.get("scope") for row in member_rows],
                "member_statuses": [row.get("status") for row in member_rows],
            },
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
        "gate": "real_cloud_embedding_isolated",
        "started_at": _now(),
        "formal_data_root": str(FORMAL_DATA_ROOT),
        "formal_outbox_touched": False,
        "formal_launch_allowed": False,
    }
    data_root: Path | None = None
    ephemeral = False
    try:
        config = _config_from_env()
        data_root, ephemeral = _fresh_data_root(args.data_root)
        _write_isolated_identity(data_root)
        report["configuration"] = {
            "provider_fingerprint": _fingerprint(config["base_url"]),
            "model": config["model"],
            "dimensions": config["dimensions"],
            "index_version": config["index_version"],
        }
        report["isolated_data_root"] = str(data_root)
        report["result"] = asyncio.run(_run_gate(data_root, config))
        report["status"] = "passed"
        return_code = 0
    except Exception as exc:  # noqa: BLE001 - gate must report every failure safely
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
