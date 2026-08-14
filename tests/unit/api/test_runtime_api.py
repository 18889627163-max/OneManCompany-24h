from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from onemancompany.api.routes import router
import onemancompany.core.config as config_mod
from onemancompany.core.provider_gateway import ProviderGateway
from onemancompany.core.runtime_storage import RuntimeStorage


def _app(storage=None, gateway=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.runtime_storage = storage
    app.state.provider_gateway = gateway
    app.state.memory_embedding_status = "disabled"
    app.state.memory_vector_status = "disabled"
    return app


@pytest.mark.asyncio
async def test_runtime_health_reports_sanitized_unavailable_state(monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", False)
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "runtime_storage": "unavailable",
        "checkpoint_store": "unavailable",
        "memory_store": "disabled",
        "sqlite_vec": "disabled",
        "embedding": "disabled",
        "memory_index_active_version": None,
        "memory_index_target_version": None,
        "memory_reindex_required": False,
        "provider_gateway": "degraded",
        "automation_registry": "unavailable",
        "automation_registered": 0,
        "provider_running": 0,
        "provider_queued": 0,
        "oldest_provider_request_at": None,
        "memory_worker_backlog": 0,
        "oldest_memory_event_at": None,
        "checkpoint_conflicts": 0,
        "checkpoint_findings_total": 0,
        "checkpoint_legacy_system_orphans": 0,
    }


@pytest.mark.asyncio
async def test_runtime_health_reports_backlog_without_exposing_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    gateway = ProviderGateway(storage)
    await gateway.start()
    try:
        await storage.execute(
            "INSERT INTO memory_outbox(event_id,namespace_json,memory_key,payload_json,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("evt", '["employee","00006","episodic"]', "secret-key", '{"token":"never-return"}', "pending", "2026-08-13T00:00:00+00:00"),
        )
        app = _app(storage, gateway)
        app.state.memory_embedding_status = "degraded"
        app.state.memory_vector_status = "unavailable"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/runtime/health")
        data = response.json()
        assert response.status_code == 200
        assert data["runtime_storage"] == "healthy"
        assert data["checkpoint_store"] == "healthy"
        assert data["memory_store"] == "healthy"
        assert data["memory_worker_backlog"] == 1
        assert data["oldest_memory_event_at"] == "2026-08-13T00:00:00+00:00"
        assert "never-return" not in response.text
        assert "secret-key" not in response.text
    finally:
        await gateway.stop()
        await storage.close()


@pytest.mark.asyncio
async def test_runtime_backup_requires_token_and_uses_online_backup(monkeypatch, tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        backup_dir = tmp_path / "backups"
        monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
        monkeypatch.setattr(config_mod.settings, "omc_runtime_backup_dir", str(backup_dir))
        app = _app(storage)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/api/admin/runtime/backup")
            response = await client.post(
                "/api/admin/runtime/backup",
                params={"backup_id": "20260813T120000Z"},
                headers={"X-OMC-Admin-Token": "admin-secret"},
            )

        assert denied.status_code == 401
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["backup_id"] == "20260813T120000Z"
        assert data["integrity_check"] == "ok"
        assert (backup_dir / data["database_file"]).is_file()
        assert (backup_dir / data["manifest_file"]).is_file()
        assert Path(data["database_path"]) == backup_dir / data["database_file"]
        assert Path(data["manifest_path"]) == backup_dir / data["manifest_file"]
        assert data["database_checksum"].startswith("sha256:")
        assert data["database_size_bytes"] > 0
        assert data["sqlite_page_count"] > 0
        assert "admin-secret" not in response.text
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_runtime_backup_resolves_relative_directory_under_data_root(monkeypatch, tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        data_root = tmp_path / "isolated-data-root"
        monkeypatch.setattr(config_mod, "DATA_ROOT", data_root)
        monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
        monkeypatch.setattr(config_mod.settings, "omc_runtime_backup_dir", "backups/db")
        app = _app(storage)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/admin/runtime/backup",
                params={"backup_id": "isolated"},
                headers={"X-OMC-Admin-Token": "admin-secret"},
            )

        assert response.status_code == 200
        data = response.json()
        expected_dir = data_root / "backups" / "db"
        assert Path(data["database_path"]).parent == expected_dir
        assert Path(data["manifest_path"]).parent == expected_dir
        assert (expected_dir / data["database_file"]).is_file()
        assert (expected_dir / data["manifest_file"]).is_file()
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_runtime_reconciliation_separates_legacy_system_orphans_and_redacts_payload(
    monkeypatch, tmp_path
):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
        await storage.record_recovery(
            node_id="legacy-node",
            tree_path="",
            mode="standard",
            expected_status="task_node_exists",
            reason="checkpoint_without_tasktree_node",
            execution_generation=1,
            checkpoint_thread_id=(
                "omc:_sys_automation_health-check:unknown-iteration:legacy-node:g1"
            ),
            status="orphan",
        )
        await storage.record_recovery(
            node_id="formal-node",
            tree_path="",
            mode="standard",
            expected_status="task_node_exists",
            reason="checkpoint_without_tasktree_node",
            execution_generation=1,
            checkpoint_thread_id="omc:project-a:iter_001:formal-node:g1",
            status="orphan",
        )
        await storage.execute(
            "INSERT INTO memory_outbox(event_id,namespace_json,memory_key,payload_json,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "evt-system",
                '["employee","00008","episodic"]',
                "system-key",
                '{"employee_id":"00008","scope":"employee","memory_type":"episodic",'
                '"project_id":"_sys_automation_health-check","source_node_id":"node-1",'
                '"token":"never-return"}',
                "pending",
                "2026-08-14T00:00:00+00:00",
            ),
        )
        app = _app(storage)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/api/admin/runtime/reconciliation")
            response = await client.get(
                "/api/admin/runtime/reconciliation",
                headers={"X-OMC-Admin-Token": "admin-secret"},
            )
            health = await client.get("/api/runtime/health")

        assert denied.status_code == 401
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "read_only"
        assert data["checkpoint_findings_total"] == 2
        assert data["checkpoint_actionable"] == 1
        assert data["checkpoint_legacy_system_orphans"] == 1
        assert data["memory_outbox_backlog"] == 1
        assert data["memory_outbox_by_project_kind"] == {"system_automation": 1}
        assert data["memory_outbox_unattempted"] == 1
        assert data["memory_outbox_missing_evidence"] == 1
        assert "never-return" not in response.text
        assert "system-key" not in response.text
        health_data = health.json()
        assert health_data["checkpoint_conflicts"] == 1
        assert health_data["checkpoint_findings_total"] == 2
        assert health_data["checkpoint_legacy_system_orphans"] == 1
    finally:
        await storage.close()
