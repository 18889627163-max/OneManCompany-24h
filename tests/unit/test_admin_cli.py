from __future__ import annotations

import onemancompany.admin_cli as cli


def test_skills_reconcile_cli_defaults_to_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_request", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or 0)
    assert cli.main(["skills", "reconcile", "--employee", "00002"]) == 0
    assert calls == [("POST", "/api/admin/skills/reconcile", {"body": {"employee_id": "00002", "dry_run": True}})]


def test_skills_reconcile_cli_execute(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_request", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or 0)
    assert cli.main(["skills", "reconcile", "--employee", "00002", "--execute"]) == 0
    assert calls == [("POST", "/api/admin/skills/reconcile", {"body": {"employee_id": "00002", "dry_run": False}})]


def test_runtime_reconciliation_cli_is_read_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or 0,
    )
    assert cli.main(["runtime", "reconciliation"]) == 0
    assert calls == [("GET", "/api/admin/runtime/reconciliation", {})]


def test_memory_status_cli_is_read_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or 0,
    )
    assert cli.main(["memory", "status"]) == 0
    assert calls == [("GET", "/api/admin/memory/status", {})]


def test_memory_reindex_cli_uses_long_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "_request",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or 0,
    )
    assert cli.main(["memory", "reindex", "--from", "v1", "--to", "v2"]) == 0
    assert calls == [(
        "POST",
        "/api/admin/memory/reindex",
        {"params": {"from_version": "v1", "to_version": "v2"}, "timeout": 3600},
    )]
