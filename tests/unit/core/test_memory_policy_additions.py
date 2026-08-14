from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from onemancompany.core.memory_service import MemoryAccessError, MemoryService
from onemancompany.core.runtime_storage import RuntimeStorage


@pytest.fixture
async def storage(tmp_path):
    value = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


def _project(root: Path, project_id: str, members: list[str]) -> None:
    path = root / project_id
    path.mkdir(parents=True)
    (path / "project.yaml").write_text(
        yaml.safe_dump({"project_id": project_id, "team": [{"employee_id": x} for x in members]}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_former_employee_cannot_create_new_memory(storage, tmp_path, monkeypatch):
    import onemancompany.core.memory_service as module

    employees = tmp_path / "employees"
    former = tmp_path / "ex-employees"
    (former / "00008").mkdir(parents=True)
    monkeypatch.setattr(module, "EMPLOYEES_DIR", employees)
    monkeypatch.setattr(module, "EX_EMPLOYEES_DIR", former)

    with pytest.raises(MemoryAccessError):
        await MemoryService(storage).propose(
            employee_id="00008", memory_type="episodic", subject="old", text="must be blocked"
        )


@pytest.mark.asyncio
async def test_project_conflict_is_disputed_then_approval_supersedes_old(storage, tmp_path, monkeypatch):
    import onemancompany.core.memory_service as module

    projects = tmp_path / "projects"
    _project(projects, "p1", ["00008"])
    monkeypatch.setattr(module, "PROJECTS_DIR", projects)
    service = MemoryService(storage)

    old = await service.propose(
        employee_id="00008", memory_type="semantic", subject="port", text="8080",
        scope="project", project_id="p1", evidence_refs=["receipt-old"], source_node_id="old",
        trusted_source=True,
    )
    new = await service.propose(
        employee_id="00008", memory_type="semantic", subject="port", text="9090",
        scope="project", project_id="p1", evidence_refs=["receipt-new"], source_node_id="new",
        trusted_source=True,
    )
    assert old["status"] == "verified"
    assert new["status"] == "candidate"
    old_row = await service.get_memory(old["key"])
    assert old_row["status"] == "disputed"
    conflict = await storage.fetchone("SELECT status FROM memory_conflicts WHERE new_memory_key=?", (new["key"],))
    assert conflict[0] == "open"

    approved = await service.approve(memory_id_or_key=new["key"], admin_id="00003", notes="reviewed")
    assert approved["status"] == "verified"
    assert approved["supersedes"] == old["memory_id"]
    assert (await service.get_memory(old["key"]))["status"] == "superseded"
    assert (await storage.fetchone("SELECT status FROM memory_conflicts WHERE new_memory_key=?", (new["key"],)))[0] == "resolved"


@pytest.mark.asyncio
async def test_admin_list_and_reject_are_audited(storage):
    service = MemoryService(storage)
    candidate = await service.propose(
        employee_id="00008", memory_type="procedural", subject="rule", text="candidate", scope="company"
    )
    rows = await service.list_memories(status="candidate", scope="company")
    assert any(row["key"] == candidate["key"] for row in rows)
    rejected = await service.reject(memory_id_or_key=candidate["memory_id"], admin_id="admin", notes="not enough evidence")
    assert rejected["status"] == "rejected"
    audit = await storage.fetchone("SELECT decision,decided_by FROM memory_reviews WHERE memory_key=? ORDER BY decided_at DESC LIMIT 1", (candidate["key"],))
    assert tuple(audit) == ("reject", "admin")


@pytest.mark.asyncio
async def test_admin_memory_listing_redacts_legacy_values(storage):
    await storage.memory_store.aput(
        ("company", "procedural"), "legacy", {"memory_id": "m", "status": "candidate", "text": "token=secret-value"}, index=False
    )
    rows = await MemoryService(storage).list_memories()
    assert rows[0]["text"] == "token=[REDACTED]"
    assert "secret-value" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_manual_supersede_requires_compatible_verified_target(storage):
    service = MemoryService(storage)
    old = await service.propose(
        employee_id="00008", memory_type="procedural", subject="restore", text="old", scope="company"
    )
    new = await service.propose(
        employee_id="00008", memory_type="procedural", subject="restore", text="new", scope="company"
    )

    with pytest.raises(ValueError, match="verified"):
        await service.supersede(
            memory_id_or_key=old["memory_id"],
            admin_id="admin",
            superseded_by=new["memory_id"],
        )

    verified = await service.approve(
        memory_id_or_key=new["memory_id"], admin_id="admin", notes="approved replacement"
    )
    result = await service.supersede(
        memory_id_or_key=old["memory_id"],
        admin_id="admin",
        superseded_by=verified["memory_id"],
        notes="replace old runbook",
    )
    assert result["status"] == "superseded"
    assert result["superseded_by"] == verified["memory_id"]
    assert (await service.get_memory(verified["memory_id"]))["supersedes"] == old["memory_id"]
