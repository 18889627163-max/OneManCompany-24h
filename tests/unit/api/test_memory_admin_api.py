from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from onemancompany.api.routes import router
import onemancompany.core.config as config_mod
from onemancompany.core.memory_service import MemoryService
from onemancompany.core.runtime_storage import RuntimeStorage


@pytest.fixture
async def storage(tmp_path):
    value = RuntimeStorage(tmp_path / "runtime.sqlite3")
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


def _app(storage=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.runtime_storage = storage
    app.state.provider_gateway = None
    app.state.memory_embedding_status = "degraded"
    app.state.memory_vector_status = "unavailable"
    return app


def _headers(**extra: str) -> dict[str, str]:
    return {
        "X-OMC-Admin-Token": "admin-secret",
        "X-OMC-Admin-Identity": "00003",
        **extra,
    }


@pytest.mark.asyncio
async def test_admin_memory_requires_token_and_loopback(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    app = _app(storage)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        missing = await client.get("/api/admin/memories")
        invalid = await client.get(
            "/api/admin/memories", headers={"X-OMC-Admin-Token": "wrong"}
        )

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.25", 1234)),
        base_url="http://test",
    ) as client:
        remote = await client.get("/api/admin/memories", headers=_headers())

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert remote.status_code == 403
    assert "admin-secret" not in missing.text + invalid.text + remote.text


@pytest.mark.asyncio
async def test_admin_memory_returns_503_when_disabled_or_unavailable(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    app = _app(storage)

    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", False)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        disabled = await client.get("/api/admin/memories", headers=_headers())

    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    app.state.runtime_storage = None
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        unavailable = await client.get("/api/admin/memories", headers=_headers())

    assert disabled.status_code == 503
    assert unavailable.status_code == 503


@pytest.mark.asyncio
async def test_admin_list_and_detail_redact_legacy_secrets(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    await storage.memory_store.aput(
        ("company", "procedural"),
        "legacy-key",
        {
            "memory_id": "legacy-memory",
            "scope": "company",
            "memory_type": "procedural",
            "subject": "legacy",
            "status": "candidate",
            "text": "Authorization: Bearer legacy.jwt.token",
            "structured_value": {
                "api_key": "api_key=sk-abcdefghijklmnop",
                "nested": ["password=hunter2"],
            },
            "created_at": "2026-08-13T00:00:00+00:00",
        },
        index=False,
    )
    app = _app(storage)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        listed = await client.get("/api/admin/memories", headers=_headers())
        detail = await client.get(
            "/api/admin/memories/legacy-memory", headers=_headers()
        )

    assert listed.status_code == detail.status_code == 200
    combined = listed.text + detail.text
    for secret in ("legacy.jwt.token", "sk-abcdefghijklmnop", "hunter2"):
        assert secret not in combined
    assert "[REDACTED]" in combined


@pytest.mark.asyncio
async def test_admin_review_transitions_and_append_only_audit(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    service = MemoryService(storage)
    old = await service.propose(
        employee_id="00008",
        memory_type="procedural",
        subject="restore-runbook",
        text="old",
        scope="company",
    )
    replacement = await service.propose(
        employee_id="00008",
        memory_type="procedural",
        subject="restore-runbook",
        text="new",
        scope="company",
    )
    rejected_candidate = await service.propose(
        employee_id="00008",
        memory_type="procedural",
        subject="unsafe-runbook",
        text="candidate",
        scope="company",
    )
    app = _app(storage)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        approved = await client.post(
            f"/api/admin/memories/{replacement['memory_id']}/approve",
            headers=_headers(**{"X-OMC-Admin-Identity": "admin token=identity-secret"}),
            json={"notes": "approved password=review-secret"},
        )
        rejected = await client.post(
            f"/api/admin/memories/{rejected_candidate['memory_id']}/reject",
            headers=_headers(),
            json={"notes": "insufficient evidence"},
        )
        superseded = await client.post(
            f"/api/admin/memories/{old['memory_id']}/supersede",
            headers=_headers(),
            json={
                "superseded_by": replacement["memory_id"],
                "notes": "replaced by verified runbook",
            },
        )

    assert approved.status_code == rejected.status_code == superseded.status_code == 200
    assert approved.json()["status"] == "verified"
    assert rejected.json()["status"] == "rejected"
    assert superseded.json()["status"] == "superseded"
    assert superseded.json()["superseded_by"] == replacement["memory_id"]

    reviews = await storage.fetchall(
        "SELECT decision,decided_by,notes FROM memory_reviews ORDER BY decided_at"
    )
    assert [row[0] for row in reviews] == ["approve", "reject", "supersede"]
    serialized_reviews = json.dumps([tuple(row) for row in reviews])
    assert "identity-secret" not in serialized_reviews
    assert "review-secret" not in serialized_reviews
    audits = await storage.fetchall(
        "SELECT event_type,event_data FROM audit_events "
        "WHERE event_type LIKE 'memory_%' ORDER BY sequence"
    )
    assert [row[0] for row in audits][-3:] == [
        "memory_approved",
        "memory_rejected",
        "memory_superseded",
    ]


@pytest.mark.asyncio
async def test_admin_supersede_rejects_missing_or_unverified_target(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    service = MemoryService(storage)
    old = await service.propose(
        employee_id="00008", memory_type="procedural", subject="rule", text="old", scope="company"
    )
    candidate = await service.propose(
        employee_id="00008", memory_type="procedural", subject="rule", text="new", scope="company"
    )
    app = _app(storage)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        missing = await client.post(
            f"/api/admin/memories/{old['memory_id']}/supersede",
            headers=_headers(),
            json={"superseded_by": "does-not-exist"},
        )
        unverified = await client.post(
            f"/api/admin/memories/{old['memory_id']}/supersede",
            headers=_headers(),
            json={"superseded_by": candidate["memory_id"]},
        )

    assert missing.status_code == 404
    assert unverified.status_code == 409
    assert "verified" in unverified.json()["detail"]


@pytest.mark.asyncio
async def test_admin_reindex_and_checkpoint_prune_are_controlled_contracts(storage, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "omc_admin_token", "admin-secret")
    monkeypatch.setattr(config_mod.settings, "omc_memory_enabled", True)
    await storage.enqueue_memory_outbox(
        namespace=("employee", "00008", "episodic"),
        memory_key="node:episodic:hash",
        payload={"token": "outbox-secret", "text": "password=payload-secret"},
        event_id="outbox-event",
    )
    app = _app(storage)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        reindex = await client.post(
            "/api/admin/memory/reindex",
            params={"from_version": "v1", "to_version": "v2"},
            headers=_headers(),
        )
        prune = await client.post(
            "/api/admin/checkpoints/prune",
            params={"older_than_days": 30},
            headers=_headers(),
        )

    assert reindex.status_code == prune.status_code == 200
    assert reindex.json()["status"] == "accepted"
    assert reindex.json()["mode"] == "outbox_job_contract"
    assert prune.json()["status"] == "dry_run"
    assert prune.json()["older_than_days"] == 30
    combined = reindex.text + prune.text
    for secret in ("admin-secret", "outbox-secret", "payload-secret"):
        assert secret not in combined
