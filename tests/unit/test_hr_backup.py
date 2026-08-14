from __future__ import annotations

import json

import pytest

from scripts.hr_backup import create_archive, verify_archive


def test_hr_archive_includes_active_ex_and_quarantine_and_restores_isolated(tmp_path):
    data_root = tmp_path / "data"
    samples = {
        "company/human_resource/employees/00010/profile.yaml": b"name: active\n",
        "company/human_resource/ex-employees/00010/profile.yaml": b"name: archived\n",
        "company/human_resource/quarantine-employees/00100-legacy/profile.yaml": b"name: legacy\n",
    }
    for relative, content in samples.items():
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = tmp_path / "employees.tar.gz"
    manifest_path = tmp_path / "employees.manifest.json"
    manifest = create_archive(data_root, archive, manifest_path)
    assert manifest["file_count"] == 3
    assert {item["path"] for item in manifest["files"]} == set(samples)

    isolated = tmp_path / "isolated-restore"
    result = verify_archive(archive, manifest_path, isolated)
    assert result["status"] == "verified"
    for relative, content in samples.items():
        assert (isolated / relative).read_bytes() == content


def test_hr_archive_rejects_manifest_tampering(tmp_path):
    data_root = tmp_path / "data"
    path = data_root / "company/human_resource/ex-employees/00010/profile.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("bad: legacy\n", encoding="utf-8")
    archive = tmp_path / "employees.tar.gz"
    manifest_path = tmp_path / "employees.manifest.json"
    create_archive(data_root, archive, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest mismatch"):
        verify_archive(archive, manifest_path)
