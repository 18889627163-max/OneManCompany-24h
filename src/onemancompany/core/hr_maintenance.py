"""Audited maintenance operations for invalid archived employee records."""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_EMPLOYEE_ID = re.compile(r"^[0-9]{5}$")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_backup_evidence(*, data_root: Path, manifest_path: Path, source_dir: Path) -> dict:
    backup_root = (data_root / "backups").resolve()
    manifest_path = manifest_path.resolve()
    if backup_root not in manifest_path.parents:
        raise ValueError("backup manifest must be under the runtime backup directory")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = manifest_path.with_name(str(manifest.get("archive", ""))).resolve()
    if archive.parent != manifest_path.parent or not archive.is_file():
        raise ValueError("backup archive referenced by manifest is missing")
    if _sha256(archive) != manifest.get("archive_sha256"):
        raise ValueError("backup archive checksum mismatch")

    relative_source = source_dir.resolve().relative_to(data_root.resolve()).as_posix()
    expected = {
        item["path"]: item
        for item in manifest.get("files", [])
        if str(item.get("path", "")).startswith(relative_source + "/")
    }
    actual = {}
    for path in sorted(source_dir.rglob("*")):
        if path.is_file():
            relative = path.resolve().relative_to(data_root.resolve()).as_posix()
            actual[relative] = {"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)}
    if not actual or actual != expected:
        raise ValueError("backup manifest does not exactly cover the archived employee directory")

    with tarfile.open(archive, "r:gz") as bundle:
        archived_names = {member.name for member in bundle.getmembers() if member.isfile()}
    if not set(actual).issubset(archived_names):
        raise ValueError("backup archive is missing archived employee files")
    return {"manifest": str(manifest_path), "archive": str(archive), "archive_sha256": manifest["archive_sha256"]}


async def quarantine_archived_employee(
    employee_id: str,
    *,
    reason: str,
    backup_manifest_path: str,
    dry_run: bool,
    storage,
    data_root: Path,
    operator: str,
) -> dict:
    """Move one invalid ex-employee directory into quarantine after backup proof."""
    if not _EMPLOYEE_ID.fullmatch(employee_id):
        raise ValueError("employee_id must contain exactly five digits")
    hr_root = data_root / "company" / "human_resource"
    active_dir = hr_root / "employees" / employee_id
    source_dir = hr_root / "ex-employees" / employee_id
    quarantine_root = hr_root / "quarantine-employees"
    if not source_dir.is_dir() or not (source_dir / "profile.yaml").is_file():
        raise FileNotFoundError("archived employee record not found")
    evidence = _verify_backup_evidence(
        data_root=data_root,
        manifest_path=Path(backup_manifest_path),
        source_dir=source_dir,
    )
    source_profile_sha256 = _sha256(source_dir / "profile.yaml")
    active_profile = active_dir / "profile.yaml"
    active_profile_sha256 = _sha256(active_profile) if active_profile.is_file() else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = quarantine_root / f"{employee_id}-invalid-{stamp}"
    if destination.exists():
        destination = quarantine_root / f"{employee_id}-invalid-{stamp}-{uuid.uuid4().hex[:8]}"
    payload = {
        "operator": operator,
        "employee_id": employee_id,
        "reason": reason[:1000],
        "dry_run": dry_run,
        "source": str(source_dir),
        "destination": str(destination),
        "source_profile_sha256": source_profile_sha256,
        "active_profile_sha256": active_profile_sha256,
        "backup": evidence,
    }
    await storage.append_audit("archived_employee_quarantine_planned", payload)
    if dry_run:
        return {"status": "dry_run", **payload}

    quarantine_root.mkdir(parents=True, exist_ok=True)
    source_dir.rename(destination)
    if active_profile_sha256 and _sha256(active_profile) != active_profile_sha256:
        destination.rename(source_dir)
        raise RuntimeError("active employee profile changed during quarantine; operation rolled back")
    completed = {**payload, "dry_run": False, "status": "completed"}
    await storage.append_audit("archived_employee_quarantined", completed)
    return completed
