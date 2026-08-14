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
