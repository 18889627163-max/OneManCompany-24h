#!/usr/bin/env python3
"""Create, verify, and safely restore non-secret HR filesystem archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

HR_PATHS = (
    "company/human_resource/employees",
    "company/human_resource/ex-employees",
    "company/human_resource/quarantine-employees",
)


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "company":
        raise ValueError(f"unsafe archive member: {name}")
    return path.as_posix()


def create_archive(data_root: Path, archive: Path, manifest_path: Path) -> dict:
    data_root = data_root.resolve()
    archive.parent.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    with tarfile.open(archive, "w:gz") as bundle:
        for relative_root in HR_PATHS:
            root = data_root / relative_root
            if not root.exists():
                continue
            bundle.add(root, arcname=relative_root, recursive=False)
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(data_root).as_posix()
                if path.is_dir():
                    bundle.add(path, arcname=relative, recursive=False)
                    continue
                if path.is_file():
                    data = path.read_bytes()
                    bundle.add(path, arcname=relative, recursive=False)
                    files.append({"path": relative, "size": len(data), "sha256": _sha256_bytes(data)})
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "archive": archive.name,
        "archive_sha256": _sha256_bytes(archive.read_bytes()),
        "roots": list(HR_PATHS),
        "file_count": len(files),
        "files": files,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_archive(archive: Path, manifest_path: Path, extract_dir: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_archive_hash = manifest.get("archive_sha256")
    actual_archive_hash = _sha256_bytes(archive.read_bytes())
    if expected_archive_hash != actual_archive_hash:
        raise ValueError(f"archive checksum mismatch: expected {expected_archive_hash}, got {actual_archive_hash}")
    expected = {item["path"]: item for item in manifest.get("files", [])}
    observed: dict[str, dict] = {}
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            name = _safe_name(member.name)
            if member.issym() or member.islnk():
                raise ValueError(f"links are not allowed in HR archive: {name}")
            if not member.isfile():
                continue
            handle = bundle.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read archive member: {name}")
            data = handle.read()
            observed[name] = {"path": name, "size": len(data), "sha256": _sha256_bytes(data)}
        if observed != expected:
            missing = sorted(set(expected) - set(observed))
            extra = sorted(set(observed) - set(expected))
            changed = sorted(k for k in set(expected) & set(observed) if expected[k] != observed[k])
            raise ValueError(f"HR archive manifest mismatch: missing={missing}, extra={extra}, changed={changed}")
        if extract_dir is not None:
            extract_dir.mkdir(parents=True, exist_ok=True)
            for member in members:
                _safe_name(member.name)
                bundle.extract(member, path=extract_dir, filter="data")
    return {"status": "verified", "file_count": len(observed), "archive_sha256": actual_archive_hash}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--data-root", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--manifest", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--extract-dir", type=Path)
    args = parser.parse_args(argv)
    if args.action == "create":
        manifest = create_archive(args.data_root, args.archive, args.manifest)
        result = {
            "status": "created",
            "archive": manifest["archive"],
            "archive_sha256": manifest["archive_sha256"],
            "file_count": manifest["file_count"],
        }
    else:
        result = verify_archive(args.archive, args.manifest, args.extract_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
