from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from onemancompany.core.memory_service import (
    MAX_INJECTED_CHARS,
    MemoryAccessError,
    MemoryService,
    redact_sensitive,
)
from onemancompany.core.memory_worker import MemoryOutboxWorker
from onemancompany.core.runtime_storage import RuntimeStorage


@pytest.fixture
async def storage(tmp_path):
    value = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


def _write_project(root: Path, project_id: str, members: list[str]) -> None:
    project_dir = root / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "project_id": project_id,
                "team": [{"employee_id": item} for item in members],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_employee_private_memory_is_not_visible_to_other_employee(storage):
    service = MemoryService(storage)
    own = await service.propose(
        employee_id="00008",
        memory_type="episodic",
        subject="recovery",
        text="Use the verified backup before restore.",
        scope="employee",
    )
    await service.propose(
        employee_id="00007",
        memory_type="episodic",
        subject="private",
        text="00007 private history",
        scope="employee",
    )

    rows = await service.search(employee_id="00008", query="", limit=8)

    assert own["memory_id"] in {row["memory_id"] for row in rows}
    assert all(row["namespace_id"] != "00007" for row in rows)


@pytest.mark.asyncio
async def test_project_memory_requires_formal_membership(storage, tmp_path, monkeypatch):
    import onemancompany.core.memory_service as memory_mod

    projects = tmp_path / "projects"
    _write_project(projects, "p1", ["00008", "00009"])
    monkeypatch.setattr(memory_mod, "PROJECTS_DIR", projects)
    service = MemoryService(storage)

    fact = await service.propose(
        employee_id="00008",
        memory_type="semantic",
        subject="project_path",
        text="The implementation path is /srv/p1.",
        scope="project",
        project_id="p1/iter_001",
        evidence_refs=["tool-receipt:1"],
        source_node_id="node-1",
        trusted_source=True,
    )
    assert fact["status"] == "verified"

    member_rows = await service.search(
        employee_id="00009", project_id="p1/iter_002", query="path"
    )
    outsider_rows = await service.search(
        employee_id="00010", project_id="p1/iter_002", query="path"
    )

    assert fact["memory_id"] in {row["memory_id"] for row in member_rows}
    assert fact["memory_id"] not in {row["memory_id"] for row in outsider_rows}
    with pytest.raises(MemoryAccessError):
        await service.propose(
            employee_id="00010",
            memory_type="semantic",
            subject="forbidden",
            text="must not write",
            scope="project",
            project_id="p1",
        )


@pytest.mark.asyncio
async def test_model_project_summary_stays_candidate_even_with_self_reported_evidence(
    storage, tmp_path, monkeypatch
):
    import onemancompany.core.memory_service as memory_mod

    projects = tmp_path / "projects"
    _write_project(projects, "p1", ["00008"])
    monkeypatch.setattr(memory_mod, "PROJECTS_DIR", projects)

    value = await MemoryService(storage).propose(
        employee_id="00008",
        memory_type="semantic",
        subject="claim",
        text="The task passed.",
        scope="project",
        project_id="p1",
        evidence_refs=["model-supplied-ref"],
        source_node_id="node-1",
    )

    assert value["status"] == "candidate"


@pytest.mark.asyncio
async def test_company_memory_requires_explicit_approval_and_is_audited(storage):
    service = MemoryService(storage)
    candidate = await service.propose(
        employee_id="00008",
        memory_type="procedural",
        subject="restore_runbook",
        text="Stop writers before isolated restore.",
        scope="company",
    )
    assert candidate["status"] == "candidate"
    assert await service.search(employee_id="00008", query="restore") == []

    approved = await service.approve_company(
        memory_key=candidate["key"], admin_id="00003", notes="approved"
    )
    assert approved["status"] == "verified"
    rows = await service.search(employee_id="00008", query="restore")
    assert approved["memory_id"] in {row["memory_id"] for row in rows}
    review = await storage.fetchone(
        "SELECT decision,decided_by FROM memory_reviews WHERE memory_key=?",
        (candidate["key"],),
    )
    assert tuple(review) == ("approve", "00003")


@pytest.mark.asyncio
async def test_unverified_expired_disputed_and_superseded_are_excluded(storage):
    namespace = ("employee", "00008", "episodic")
    statuses = ["candidate", "disputed", "superseded", "rejected"]
    for status in statuses:
        await storage.memory_store.aput(
            namespace,
            status,
            {
                "memory_id": status,
                "scope": "employee",
                "namespace_id": "00008",
                "status": status,
                "text": status,
                "created_at": "2026-08-13T00:00:00+00:00",
            },
            index=False,
        )
    await storage.memory_store.aput(
        namespace,
        "expired",
        {
            "memory_id": "expired",
            "scope": "employee",
            "namespace_id": "00008",
            "status": "active",
            "text": "expired",
            "expires_at": "2000-01-01T00:00:00+00:00",
            "created_at": "2000-01-01T00:00:00+00:00",
        },
        index=False,
    )

    assert await MemoryService(storage).search(employee_id="00008", query="") == []


@pytest.mark.asyncio
async def test_sensitive_values_are_recursively_redacted(storage):
    service = MemoryService(storage)
    value = await service.propose(
        employee_id="00008",
        memory_type="episodic",
        subject="password=hunter2",
        text="Authorization: Bearer abc.def and api_key=sk-abcdefghijklmnop",
        structured_value={
            "nested": {"token": "token=my-secret-token"},
            "items": ["password=secret-value", "safe"],
        },
    )
    serialized = json.dumps(value, ensure_ascii=False)

    assert "hunter2" not in serialized
    assert "abc.def" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert "my-secret-token" not in serialized
    assert "secret-value" not in serialized
    assert "[REDACTED]" in serialized
    assert redact_sensitive("Bearer top-secret") == "Bearer [REDACTED]"


@pytest.mark.asyncio
async def test_retrieval_is_bounded_to_eight_and_six_thousand_characters(storage):
    service = MemoryService(storage)
    for index in range(12):
        await service.propose(
            employee_id="00008",
            memory_type="episodic",
            subject=f"history-{index}",
            text=(f"memory-{index} " + "x" * 900),
        )

    rows = await service.search(employee_id="00008", query="", limit=100)
    rendered = sum(
        len(
            f"{row.get('memory_id')} {row.get('scope')} {row.get('status')} "
            f"{row.get('source_node_id')} {row.get('text', '')}"
        )
        for row in rows
    )

    assert len(rows) <= 8
    assert rendered <= MAX_INJECTED_CHARS


@pytest.mark.asyncio
async def test_outbox_is_deduplicated_and_worker_is_restart_safe(storage):
    payload = {
        "employee_id": "00008",
        "scope": "employee",
        "memory_type": "episodic",
        "subject": "completed task",
        "text": "Recovered safely.",
        "source_node_id": "node-1",
    }
    first = await storage.enqueue_memory_outbox(
        namespace=("employee", "00008", "episodic"),
        memory_key="node-1:episodic",
        payload=payload,
        event_id="event-1",
    )
    second = await storage.enqueue_memory_outbox(
        namespace=("employee", "00008", "episodic"),
        memory_key="node-1:episodic",
        payload=payload,
        event_id="event-2",
    )
    assert first == second == "event-1"
    assert await storage.memory_outbox_backlog() == 1

    event = (await storage.claim_memory_outbox(limit=1))[0]
    await MemoryOutboxWorker(storage)._process(event)
    assert await storage.memory_outbox_backlog() == 0

    row = await storage.fetchone(
        "SELECT status,attempt FROM memory_outbox WHERE event_id='event-1'"
    )
    assert tuple(row) == ("completed", 1)
    memories = await MemoryService(storage).search(employee_id="00008", query="")
    assert len(memories) == 1
    assert memories[0]["embedding_status"] == "pending"


@pytest.mark.asyncio
async def test_worker_failure_holds_event_without_failing_business_state(storage, monkeypatch):
    payload = {
        "employee_id": "00008",
        "scope": "employee",
        "memory_type": "episodic",
        "subject": "task",
        "text": "result",
    }
    await storage.enqueue_memory_outbox(
        namespace=("employee", "00008", "episodic"),
        memory_key="node-2:episodic",
        payload=payload,
        event_id="event-fail",
    )
    event = (await storage.claim_memory_outbox(limit=1))[0]

    async def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(MemoryService, "propose", fail)
    await MemoryOutboxWorker(storage)._process(event)

    row = await storage.fetchone(
        "SELECT status,last_error,next_retry_at FROM memory_outbox WHERE event_id='event-fail'"
    )
    assert row[0] == "holding"
    assert row[1] == "RuntimeError"
    assert row[2]
