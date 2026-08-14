#!/usr/bin/env bash
# OneManCompany SQLite runtime + filesystem backup.
# Online database copies must go through the protected Online Backup API.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"
DATA_ROOT="${OMC_DATA_ROOT:-.onemancompany}"
BACKUP_DIR="${OMC_BACKUP_DIR:-$DATA_ROOT/backups}"
DB_PATH="${OMC_MEMORY_DATABASE_PATH:-$DATA_ROOT/data/runtime.sqlite3}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_SET_ID="${OMC_BACKUP_SET_ID:-$TIMESTAMP}"
DATE="${TIMESTAMP:0:8}"
DB_BACKUP_DIR="$BACKUP_DIR/db"
mkdir -p "$BACKUP_DIR"/{db,employees,projects,config}

printf 'OneManCompany backup %s\n' "$TIMESTAMP"

# HR archive includes active, archived, and quarantined employees. The adjacent
# manifest contains a SHA-256 for the archive and every file.
HR_ARCHIVE="$BACKUP_DIR/employees/employees_${BACKUP_SET_ID}.tar.gz"
HR_MANIFEST="$BACKUP_DIR/employees/employees_${BACKUP_SET_ID}.manifest.json"
"${PYTHON:-.venv/bin/python}" scripts/hr_backup.py create \
  --data-root "$DATA_ROOT" --archive "$HR_ARCHIVE" --manifest "$HR_MANIFEST"
tar -C "$DATA_ROOT" -czf "$BACKUP_DIR/projects/projects_${BACKUP_SET_ID}.tar.gz" \
  company/business/projects/ 2>/dev/null || true
tar --exclude='.env' --exclude='.onemancompany/.env' -czf \
  "$BACKUP_DIR/config/config_${BACKUP_SET_ID}.tar.gz" docs/ scripts/ .env.example 2>/dev/null || true

# A responding HTTP service is considered running even when unhealthy. Never
# fall back to cp in that case: active SQLite WAL files must use the API.
HEALTH_URL="${OMC_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
BACKUP_URL="${OMC_BACKUP_URL:-http://127.0.0.1:8000/api/admin/runtime/backup}"
if curl --silent --show-error --connect-timeout 2 --max-time 5 \
    -o /dev/null "$HEALTH_URL"; then
  : "${OMC_ADMIN_TOKEN:?OMC_ADMIN_TOKEN must be set for online backup}"
  response="$(curl --silent --show-error --fail --connect-timeout 2 --max-time 120 \
    -X POST -H "X-OMC-Admin-Token: ${OMC_ADMIN_TOKEN}" \
    --get --data-urlencode "backup_id=${BACKUP_SET_ID}" "$BACKUP_URL")"
  db_file="$(printf '%s' "$response" | sed -n 's/.*"database_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  manifest_file="$(printf '%s' "$response" | sed -n 's/.*"manifest_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  test -n "$db_file" && test -n "$manifest_file" || {
    echo "Online backup response did not contain backup filenames" >&2
    exit 1
  }
  # The API writes to its configured backup directory. Copy only the receipt
  # artifacts into this run's directory when the paths differ is intentionally
  # not attempted. The default API path (backups/db relative to OMC_DATA_ROOT)
  # matches DB_BACKUP_DIR; custom paths must be configured consistently.
  expected_db_file="runtime-${BACKUP_SET_ID}.sqlite3"
  test "$db_file" = "$expected_db_file" || {
    echo "Online backup ID mismatch: expected $expected_db_file, received $db_file" >&2
    exit 1
  }
  echo "Online SQLite backup completed: ${db_file} (${manifest_file})"
else
  # The service is not reachable, so it is safe to create an offline image.
  # sqlite3.Connection.backup handles WAL state without copying a live file.
  if test -f "$DB_PATH"; then
    DB_PATH="$DB_PATH" BACKUP_TARGET_DIR="$DB_BACKUP_DIR" BACKUP_STAMP="$BACKUP_SET_ID" \
      "${PYTHON:-.venv/bin/python}" - <<'PY'
import hashlib, json, os, sqlite3
from pathlib import Path
source = Path(os.environ["DB_PATH"]).resolve()
target_dir = Path(os.environ["BACKUP_TARGET_DIR"]).resolve()
stamp = os.environ["BACKUP_STAMP"]
target_dir.mkdir(parents=True, exist_ok=True)
target = target_dir / f"runtime-{stamp}.sqlite3"
source_conn = sqlite3.connect(source)
dest_conn = sqlite3.connect(target)
try:
    source_conn.backup(dest_conn)
finally:
    dest_conn.close(); source_conn.close()
verify = sqlite3.connect(target)
try:
    integrity = verify.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    verify.close()
if integrity != "ok":
    raise SystemExit(f"offline backup integrity check failed: {integrity}")
sha = hashlib.sha256(target.read_bytes()).hexdigest()
manifest = {
    "backup_method": "sqlite_online_backup_api_offline",
    "created_at": stamp,
    "database_checksum": f"sha256:{sha}",
    "integrity_check_result": integrity,
}
manifest_path = target.with_suffix(target.suffix + ".manifest.json")
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(target)
PY
  else
    echo "runtime database not found; database backup skipped" >&2
  fi
fi

# Keep database and manifest pairs for seven days. Never remove secrets here.
find "$BACKUP_DIR" -type f \( -name '*.tar.gz' -o -name '*.sqlite3' -o -name '*.manifest.json' -o -name '*.txt' \) \
  -mtime +7 -delete
cat > "$BACKUP_DIR/backup_log_${DATE}.txt" <<EOF
OneManCompany backup
created_at_utc: $TIMESTAMP
backup_set_id: $BACKUP_SET_ID
root: $ROOT_DIR
runtime_database: $DB_PATH
secret_files: excluded
EOF

echo "Backup completed under $BACKUP_DIR"
