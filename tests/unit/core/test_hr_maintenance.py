from __future__ import annotations

import json

import pytest

from onemancompany.core.hr_maintenance import quarantine_archived_employee
from onemancompany.core.runtime_storage import RuntimeStorage
from scripts.hr_backup import create_archive


@pytest.mark.asyncio
async def test_quarantine_requires_backup_preserves_active_and_is_idempotently_named(tmp_path):
    data_root = tmp_path / "runtime"
    active = data_root / "company/human_resource/employees/00010/profile.yaml"
    archived = data_root / "company/human_resource/ex-employees/00010/profile.yaml"
    active.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    active.write_text("name: active\n", encoding="utf-8")
    archived.write_text("desk_position: !!python/tuple [0, 0]\n", encoding="utf-8")
    backup_dir = data_root / "backups/employees"
    archive = backup_dir / "employees_test.tar.gz"
    manifest = backup_dir / "employees_test.manifest.json"
    create_archive(data_root, archive, manifest)
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        preview = await quarantine_archived_employee(
            "00010", reason="unsafe legacy YAML", backup_manifest_path=str(manifest),
            dry_run=True, storage=storage, data_root=data_root, operator="00003",
        )
        assert preview["status"] == "dry_run"
        assert archived.exists()
        result = await quarantine_archived_employee(
            "00010", reason="unsafe legacy YAML", backup_manifest_path=str(manifest),
            dry_run=False, storage=storage, data_root=data_root, operator="00003",
        )
        assert result["status"] == "completed"
        assert not archived.exists()
        assert active.read_text(encoding="utf-8") == "name: active\n"
        assert __import__('pathlib').Path(result["destination"]).exists()
        rows = await storage.fetchall("SELECT event_type,event_data FROM audit_events ORDER BY created_at")
        assert [row[0] for row in rows] == [
            "archived_employee_quarantine_planned",
            "archived_employee_quarantine_planned",
            "archived_employee_quarantined",
        ]
        assert all(json.loads(row[1])["source_profile_sha256"].startswith("sha256:") for row in rows)
    finally:
        await storage.close()
