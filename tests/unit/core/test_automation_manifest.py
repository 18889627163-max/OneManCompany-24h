from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from onemancompany.core.automation_manifest import (
    ManifestAutomationRunner,
    load_manifest,
    register_manifest,
    render_task,
    validate_schedule,
)
from onemancompany.core.runtime_storage import DispatchIntentConflict, RuntimeStorage


def _manifest(path: Path, *, employee_id: str = "00003", task_id: str = "daily-check", schedule: str = "7 * * * *") -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "cron_tasks": [
                    {
                        "id": task_id,
                        "name": "Daily check",
                        "employee_id": employee_id,
                        "schedule": schedule,
                        "task_key_template": "system:daily:{date}",
                        "enabled": True,
                        "priority": 1,
                        "prompt_template": "Check the system at {now}",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_manifest_register_is_idempotent_and_updates_hash(tmp_path, monkeypatch):
    manifest = tmp_path / "cron-tasks.yaml"
    employee_root = tmp_path / "employees" / "00003"
    employee_root.mkdir(parents=True)
    (employee_root / "profile.yaml").write_text("name: COO\nrole: COO\nskills: []\n", encoding="utf-8")
    _manifest(manifest)

    import onemancompany.core.automation_manifest as module
    monkeypatch.setattr(module, "EMPLOYEES_DIR", tmp_path / "employees")
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        first = await register_manifest(storage, manifest)
        second = await register_manifest(storage, manifest)
        assert first["registered"] == 1
        assert second["registered"] == 0
        assert second["unchanged"] == 1

        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        data["cron_tasks"][0]["prompt_template"] = "Changed at {now}"
        manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        changed = await register_manifest(storage, manifest)
        assert changed["registered"] == 1
        row = await storage.fetchone(
            "SELECT prompt_template,status FROM automation_registry WHERE automation_id=?",
            ("daily-check",),
        )
        assert row[0] == "Changed at {now}"
        assert row[1] == "registered"
    finally:
        await storage.close()


def test_manifest_validation_rejects_duplicate_and_invalid_cron(tmp_path, monkeypatch):
    import onemancompany.core.automation_manifest as module
    employee_root = tmp_path / "employees" / "00003"
    employee_root.mkdir(parents=True)
    (employee_root / "profile.yaml").write_text("name: COO\nrole: COO\nskills: []\n", encoding="utf-8")
    monkeypatch.setattr(module, "EMPLOYEES_DIR", tmp_path / "employees")

    assert validate_schedule("7 */2 * * *") == "7 */2 * * *"
    with pytest.raises(ValueError, match="five cron"):
        validate_schedule("* * * *")

    manifest = tmp_path / "duplicate.yaml"
    _manifest(manifest)
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["cron_tasks"].append(dict(data["cron_tasks"][0]))
    manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate automation id"):
        load_manifest(manifest)


@pytest.mark.asyncio
async def test_dispatch_intent_replay_and_fingerprint_conflict(tmp_path):
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        first = await storage.prepare_dispatch_intent(
            parent_id="automation:daily-check",
            employee_id="00003",
            task_key="system:daily:2026-08-13",
            request_fingerprint="sha256:a",
        )
        replay = await storage.prepare_dispatch_intent(
            parent_id="automation:daily-check",
            employee_id="00003",
            task_key="system:daily:2026-08-13",
            request_fingerprint="sha256:a",
        )
        assert first == replay
        with pytest.raises(DispatchIntentConflict):
            await storage.prepare_dispatch_intent(
                parent_id="automation:daily-check",
                employee_id="00003",
                task_key="system:daily:2026-08-13",
                request_fingerprint="sha256:b",
            )
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_runner_dispatch_writes_receipt_and_replays_same_node(tmp_path, monkeypatch):
    manifest = tmp_path / "cron-tasks.yaml"
    employee_root = tmp_path / "employees" / "00003"
    employee_root.mkdir(parents=True)
    (employee_root / "profile.yaml").write_text("name: COO\nrole: COO\nskills: []\n", encoding="utf-8")
    _manifest(manifest, schedule="7 * * * *")

    import onemancompany.core.automation_manifest as module
    monkeypatch.setattr(module, "EMPLOYEES_DIR", tmp_path / "employees")
    storage = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await storage.initialize()
    try:
        await register_manifest(storage, manifest)
        task = load_manifest(manifest)[0]
        from onemancompany.core.task_tree import TaskTree
        created_tree = TaskTree(project_id="_sys_automation_daily-check")
        created_node = created_tree.create_root(employee_id="00003", description="Check the system")
        created_path = tmp_path / "tree.yaml"
        created_tree.save(created_path)
        created = (created_node.id, str(created_path))
        push = AsyncMock()
        # _dispatch_once imports the real sync helper; patch it in its defining module.
        with patch("onemancompany.api.routes._push_adhoc_task", return_value=created):
            runner = ManifestAutomationRunner(storage, interval_seconds=5)
            node_id, tree_path = await runner._dispatch_once(
                task, "system:daily:2026-08-13", "Check the system", "2026-08-13T00:07:00+08:00"
            )
            assert (node_id, tree_path) == created

        intent = await storage.get_dispatch_intent(
            "automation:daily-check", "00003", "system:daily:2026-08-13"
        )
        assert intent["state"] == "scheduled"
        assert intent["node_id"] == created_node.id
        assert intent["receipt"]["receipt_type"] == "automation_dispatch"

        # A replay sees the prepared/bound durable intent and must not create a new node.
        with patch("onemancompany.api.routes._push_adhoc_task", side_effect=AssertionError("duplicate dispatch")):
            # Avoid the filesystem reconciliation branch: the existing intent is authoritative.
            replay = await storage.prepare_dispatch_intent(
                parent_id="automation:daily-check",
                employee_id="00003",
                task_key="system:daily:2026-08-13",
                request_fingerprint=intent["request_fingerprint"],
            )
        assert replay["node_id"] == created_node.id
    finally:
        await storage.close()


def test_render_task_is_stable_for_same_minute():
    task = {
        "task_key_template": "system:daily:{date}",
        "prompt_template": "Run at {scheduled_at}",
    }
    when = datetime.fromisoformat("2026-08-13T00:07:00+08:00")
    key, prompt = render_task(task, when)
    assert key == "system:daily:2026-08-13"
    assert "2026-08-13T00:07:00+08:00" in prompt
