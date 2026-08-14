#!/usr/bin/env bash
# OneManCompany restore with an isolated SQLite validation step.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"
DATA_ROOT="${OMC_DATA_ROOT:-.onemancompany}"
BACKUP_DIR="${OMC_BACKUP_DIR:-$DATA_ROOT/backups}"
DB_PATH="${OMC_MEMORY_DATABASE_PATH:-$DATA_ROOT/data/runtime.sqlite3}"
RESTORE_FILESYSTEM="${OMC_RESTORE_FILESYSTEM:-true}"
TIMESTAMP="${1:-}"
if test -z "$TIMESTAMP"; then
  echo "Usage: $0 <UTC timestamp>" >&2
  echo "Example: $0 20260813T120000Z" >&2
  exit 2
fi

DB_BACKUP="$BACKUP_DIR/db/runtime-${TIMESTAMP}.sqlite3"
MANIFEST="$DB_BACKUP.manifest.json"
EMP_BACKUP="$BACKUP_DIR/employees/employees_${TIMESTAMP}.tar.gz"
EMP_MANIFEST="$BACKUP_DIR/employees/employees_${TIMESTAMP}.manifest.json"
PROJECT_BACKUP="$BACKUP_DIR/projects/projects_${TIMESTAMP}.tar.gz"
CONFIG_BACKUP="$BACKUP_DIR/config/config_${TIMESTAMP}.tar.gz"

test -f "$DB_BACKUP" || { echo "Missing database backup: $DB_BACKUP" >&2; exit 1; }
test -f "$MANIFEST" || { echo "Missing database manifest: $MANIFEST" >&2; exit 1; }
if test "$RESTORE_FILESYSTEM" = true && test -f "$EMP_BACKUP"; then
  test -f "$EMP_MANIFEST" || { echo "Missing HR archive manifest: $EMP_MANIFEST" >&2; exit 1; }
  "${PYTHON:-.venv/bin/python}" scripts/hr_backup.py verify \
    --archive "$EMP_BACKUP" --manifest "$EMP_MANIFEST"
fi

# Any HTTP response means the service is running; do not replace its database.
if curl --silent --show-error --connect-timeout 2 --max-time 5 \
    -o /dev/null "${OMC_HEALTH_URL:-http://127.0.0.1:8000/api/health}"; then
  echo "Service is reachable. Stop it before restore; no files were changed." >&2
  exit 1
fi

"${PYTHON:-.venv/bin/python}" - "$DB_BACKUP" "$MANIFEST" <<'PY'
import hashlib, json, sqlite3, sys
from pathlib import Path
backup = Path(sys.argv[1]); manifest = Path(sys.argv[2])
data = json.loads(manifest.read_text(encoding="utf-8"))
expected = str(data.get("database_checksum", ""))
actual = "sha256:" + hashlib.sha256(backup.read_bytes()).hexdigest()
if expected != actual:
    raise SystemExit(f"checksum mismatch: expected {expected}, got {actual}")
conn = sqlite3.connect(backup)
try:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit(f"integrity check failed: {integrity}")
    required = {"checkpoints", "store", "memory_outbox", "audit_events"}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = required - tables
    if missing:
        raise SystemExit("backup missing required tables: " + ",".join(sorted(missing)))
finally:
    conn.close()
PY

read -r -p "Restore $TIMESTAMP and overwrite $DB_PATH? Type yes: " confirm
if test "$confirm" != yes; then
  echo "Restore cancelled"
  exit 0
fi

SAFETY_DIR="$BACKUP_DIR/safety-before-restore-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$SAFETY_DIR"
if test -f "$DB_PATH"; then
  # The service has already been confirmed unreachable. Still use SQLite's
  # backup API instead of cp so a leftover WAL is folded into one consistent
  # safety image. Direct WAL/SHM copies are retained only as diagnostic data.
  DB_PATH="$DB_PATH" SAFETY_DIR="$SAFETY_DIR" \
    "${PYTHON:-.venv/bin/python}" - <<'PY'
import hashlib, json, os, sqlite3
from pathlib import Path
source = Path(os.environ["DB_PATH"]).resolve()
out_dir = Path(os.environ["SAFETY_DIR"]).resolve()
target = out_dir / "runtime.sqlite3"
source_conn = sqlite3.connect(source)
dest_conn = sqlite3.connect(target)
try:
    source_conn.backup(dest_conn)
finally:
    dest_conn.close()
    source_conn.close()
verify = sqlite3.connect(target)
try:
    quick = str(verify.execute("PRAGMA quick_check").fetchone()[0])
    integrity = str(verify.execute("PRAGMA integrity_check").fetchone()[0])
    page_count = int(verify.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(verify.execute("PRAGMA page_size").fetchone()[0])
finally:
    verify.close()
if quick != "ok" or integrity != "ok":
    raise SystemExit(f"safety backup integrity failed: quick_check={quick}, integrity_check={integrity}")
manifest = {
    "backup_method": "sqlite_online_backup_api_safety_snapshot",
    "database_path": str(target),
    "database_checksum": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
    "sqlite_page_count": page_count,
    "sqlite_page_size": page_size,
    "quick_check_result": quick,
    "integrity_check_result": integrity,
}
(target.with_suffix(target.suffix + ".manifest.json")).write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"Safety SQLite snapshot created: {target}")
PY
  test ! -f "$DB_PATH-wal" || cp "$DB_PATH-wal" "$SAFETY_DIR/runtime.sqlite3-wal"
  test ! -f "$DB_PATH-shm" || cp "$DB_PATH-shm" "$SAFETY_DIR/runtime.sqlite3-shm"
fi
mkdir -p "$(dirname "$DB_PATH")"
STAGED_DB="$(dirname "$DB_PATH")/.runtime-restore-${TIMESTAMP}.sqlite3.tmp"
cp "$DB_BACKUP" "$STAGED_DB"
"${PYTHON:-.venv/bin/python}" - "$STAGED_DB" <<'PY'
import sqlite3, sys
from pathlib import Path
path = Path(sys.argv[1])
conn = sqlite3.connect(path)
try:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    conn.close()
if result != "ok":
    raise SystemExit(f"staged restore integrity check failed: {result}")
PY
mv "$STAGED_DB" "$DB_PATH"

# Restore non-secret filesystem state only when the matching archive exists.
# New backup archives are rooted at company/ and restore under OMC_DATA_ROOT.
# Legacy archives rooted at .onemancompany/ remain restorable under ROOT_DIR.
restore_data_archive() {
  local archive="$1"
  local first_entry
  first_entry="$(tar -tzf "$archive" | sed -n '1p')"
  case "$first_entry" in
    .onemancompany/*) tar -C "$ROOT_DIR" -xzf "$archive" ;;
    company/*) mkdir -p "$DATA_ROOT"; tar -C "$DATA_ROOT" -xzf "$archive" ;;
    *) echo "Unsupported data archive layout: $archive ($first_entry)" >&2; return 1 ;;
  esac
}
if test "$RESTORE_FILESYSTEM" = true; then
  if test -f "$EMP_BACKUP"; then
    # Preserve the complete current HR state before any filesystem overwrite.
    "${PYTHON:-.venv/bin/python}" scripts/hr_backup.py create \
      --data-root "$DATA_ROOT" \
      --archive "$SAFETY_DIR/hr-before-restore.tar.gz" \
      --manifest "$SAFETY_DIR/hr-before-restore.manifest.json"
    "${PYTHON:-.venv/bin/python}" scripts/hr_backup.py verify \
      --archive "$EMP_BACKUP" --manifest "$EMP_MANIFEST" --extract-dir "$DATA_ROOT"
  fi
  test ! -f "$PROJECT_BACKUP" || restore_data_archive "$PROJECT_BACKUP"
  test ! -f "$CONFIG_BACKUP" || tar -C "$ROOT_DIR" -xzf "$CONFIG_BACKUP"
else
  echo "Filesystem archives were not restored (OMC_RESTORE_FILESYSTEM=$RESTORE_FILESYSTEM)."
fi

"${PYTHON:-.venv/bin/python}" - "$DB_PATH" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
try:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in ("checkpoints", "store", "memory_outbox", "audit_events")
    }
finally:
    conn.close()
if integrity != "ok":
    raise SystemExit(f"restored database integrity check failed: {integrity}")
print("Restored database verification:", counts)
PY

echo "Restore completed. Safety backup: $SAFETY_DIR"
echo "Secrets were not restored; review .env separately."
