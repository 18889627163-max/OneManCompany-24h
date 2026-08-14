"""Loopback admin CLI; all mutations go through the management HTTP API."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _request(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> int:
    base = os.environ.get("OMC_ADMIN_URL", "http://127.0.0.1:8000").rstrip("/")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-OMC-Admin-Token": os.environ.get("OMC_ADMIN_TOKEN", ""), "X-OMC-Admin-Identity": os.environ.get("OMC_ADMIN_IDENTITY", "cli")}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.load(response)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
    except urllib.error.HTTPError as exc:
        try: payload = json.load(exc)
        except Exception: payload = {"detail": exc.read().decode(errors="replace")}
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onemancompany-admin")
    parser.add_argument("--url", dest="url")
    parser.add_argument("--token", dest="token")
    sub = parser.add_subparsers(dest="resource", required=True)
    memory = sub.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    ls = memory_sub.add_parser("list"); ls.add_argument("--status", default=""); ls.add_argument("--scope", default="")
    detail = memory_sub.add_parser("detail"); detail.add_argument("memory_id")
    for action in ("approve", "reject", "supersede"):
        cmd = memory_sub.add_parser(action); cmd.add_argument("memory_id"); cmd.add_argument("--reason", "--notes", dest="notes", default=""); cmd.add_argument("--superseded-by", default="")
    reindex = memory_sub.add_parser("reindex"); reindex.add_argument("--from", dest="from_version", default=""); reindex.add_argument("--to", dest="to_version", default="")
    checkpoint = sub.add_parser("checkpoint")
    cp_sub = checkpoint.add_subparsers(dest="action", required=True)
    prune = cp_sub.add_parser("prune"); prune.add_argument("--older-than", type=int, default=30); prune.add_argument("--execute", action="store_true")
    hr = sub.add_parser("hr")
    hr_sub = hr.add_subparsers(dest="action", required=True)
    quarantine = hr_sub.add_parser("quarantine-archived")
    quarantine.add_argument("--employee", required=True)
    quarantine.add_argument("--reason", required=True)
    quarantine.add_argument("--backup-manifest", required=True)
    quarantine.add_argument("--execute", action="store_true")
    skills = sub.add_parser("skills")
    skills_sub = skills.add_subparsers(dest="action", required=True)
    reconcile = skills_sub.add_parser("reconcile")
    reconcile.add_argument("--employee", required=True)
    reconcile.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.url: os.environ["OMC_ADMIN_URL"] = args.url
    if args.token: os.environ["OMC_ADMIN_TOKEN"] = args.token
    if args.resource == "hr":
        return _request(
            "POST",
            "/api/admin/hr/quarantine-archived",
            body={
                "employee_id": args.employee,
                "reason": args.reason,
                "backup_manifest_path": args.backup_manifest,
                "dry_run": not args.execute,
            },
        )
    if args.resource == "skills":
        return _request(
            "POST",
            "/api/admin/skills/reconcile",
            body={"employee_id": args.employee, "dry_run": not args.execute},
        )
    if args.resource == "memory":
        if args.action == "list": return _request("GET", "/api/admin/memories", params={"status": args.status, "scope": args.scope})
        if args.action == "detail": return _request("GET", f"/api/admin/memories/{args.memory_id}")
        if args.action in {"approve", "reject", "supersede"}:
            body = {"notes": args.notes, "superseded_by": args.superseded_by}
            return _request("POST", f"/api/admin/memories/{args.memory_id}/{args.action}", body=body)
        return _request("POST", "/api/admin/memory/reindex", params={"from_version": args.from_version, "to_version": args.to_version})
    return _request("POST", "/api/admin/checkpoints/prune", params={"older_than_days": args.older_than, "dry_run": str(not args.execute).lower()})


if __name__ == "__main__":
    raise SystemExit(main())
