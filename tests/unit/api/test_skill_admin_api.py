from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import onemancompany.agents.onboarding as onboarding_mod
import onemancompany.core.config as config_mod
from onemancompany.api.routes import router
from onemancompany.core.runtime_storage import RuntimeStorage


def _app(storage: RuntimeStorage) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.runtime_storage = storage
    return app


@pytest.mark.asyncio
async def test_skill_reconcile_requires_admin_and_appends_redacted_audit(monkeypatch, tmp_path):
    employees_dir = tmp_path / "employees"
    employee_dir = employees_dir / "00002"
    (employee_dir / "skills").mkdir(parents=True)
    (employee_dir / "profile.yaml").write_text("name: HR\nrole: hr\nskills: []\n", encoding="utf-8")
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", employees_dir)
    monkeypatch.setattr(onboarding_mod, "_DEFAULT_SKILLS_DIR", tmp_path / "defaults")
    default_skill = tmp_path / "defaults" / "task_lifecycle"
    default_skill.mkdir(parents=True)
    (default_skill / "SKILL.md").write_text("default skill", encoding="utf-8")
    try:
        app = _app(storage)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/api/admin/skills/reconcile", json={"employee_id": "00002", "dry_run": True})
            preview = await client.post(
                "/api/admin/skills/reconcile",
                json={"employee_id": "00002", "dry_run": True},
                headers={
                    "X-OMC-Admin-Token": "admin-secret",
                    "X-OMC-Admin-Identity": "operator token=do-not-log",
                },
            )
            assert denied.status_code == 401
            assert preview.status_code == 200
            assert preview.json()["dry_run"] is True
            assert not (employee_dir / "skills" / "task_lifecycle" / "SKILL.md").exists()
            applied = await client.post(
                "/api/admin/skills/reconcile",
                json={"employee_id": "00002", "dry_run": False},
                headers={"X-OMC-Admin-Token": "admin-secret", "X-OMC-Admin-Identity": "00003"},
            )

        assert applied.status_code == 200
        assert (employee_dir / "skills" / "task_lifecycle" / "SKILL.md").read_text(encoding="utf-8") == "default skill"
        rows = await storage.fetchall(
            "SELECT event_data FROM audit_events WHERE event_type = ? ORDER BY created_at",
            ("default_skills_reconciled",),
        )
        assert len(rows) == 2
        payloads = [json.loads(row[0]) for row in rows]
        assert payloads[0]["employee_id"] == "00002"
        assert payloads[0]["dry_run"] is True
        assert payloads[1]["dry_run"] is False
        assert payloads[1]["operator"] == "00003"
        assert "do-not-log" not in json.dumps(payloads)
        assert payloads[1]["files"][0]["source_sha256"]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_skill_reconcile_rejects_unknown_or_unsafe_employee_id(monkeypatch, tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
    try:
        app = _app(storage)
        headers = {"X-OMC-Admin-Token": "admin-secret"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unsafe = await client.post(
                "/api/admin/skills/reconcile",
                json={"employee_id": "../00002", "dry_run": True},
                headers=headers,
            )
            missing = await client.post(
                "/api/admin/skills/reconcile",
                json={"employee_id": "00099", "dry_run": True},
                headers=headers,
            )
        assert unsafe.status_code == 422
        assert missing.status_code == 404
    finally:
        await storage.close()
