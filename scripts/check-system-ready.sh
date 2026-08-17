#!/usr/bin/env bash
# OneManCompany standard-v2 readiness gate.
# This script is fail-closed: it checks the implementation that actually exists
# in this repository and never treats a missing service as healthy.
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_CMD="${PYTHON_CMD:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_CMD" ]] || PYTHON_CMD="$(command -v python3 || true)"
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { printf '✅ %s\n' "$1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { printf '❌ %s\n' "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn() { printf '⚠️  %s\n' "$1"; WARN_COUNT=$((WARN_COUNT + 1)); }
section() { printf '\n%s\n---\n' "$1"; }

section "OneManCompany standard-v2 readiness"
printf 'Workspace: %s\n' "$ROOT_DIR"
printf 'Date: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

section "P0 formal workflow Gate"
for pattern in "workflow_contract_version" "prepare_dispatch_intent" "accept_child" "reject_child" "ProviderGateway" "AsyncSqliteSaver" "AsyncSqliteStore"; do
  if grep -R -q "$pattern" src/onemancompany --include='*.py'; then pass "$pattern present"; else fail "$pattern missing"; fi
done
if grep -R -q "dispatch_verification\|receipt_type.*dispatch" src/onemancompany --include='*.py'; then pass "dispatch receipt verification present"; else fail "dispatch receipt verification missing"; fi
if grep -R -q "acceptance_audit" src/onemancompany --include='*.py'; then pass "acceptance audit present"; else fail "acceptance audit missing"; fi

section "Python/import and SQLite runtime contract"
if [[ -n "$PYTHON_CMD" ]] && "$PYTHON_CMD" - <<'PY'
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from onemancompany.core.runtime_storage import RuntimeStorage
assert AsyncSqliteSaver and AsyncSqliteStore and RuntimeStorage
PY
then pass "SQLite LangGraph import contract"; else fail "SQLite LangGraph import contract"; fi
if [[ -x "$PYTHON_CMD" ]] && "$PYTHON_CMD" - <<'PY'
import asyncio, tempfile
from pathlib import Path
from onemancompany.core.runtime_storage import RuntimeStorage
async def main():
    with tempfile.TemporaryDirectory() as d:
        s = RuntimeStorage(Path(d) / 'runtime.sqlite3')
        await s.initialize()
        rows = await s.fetchall("PRAGMA journal_mode")
        assert str(rows[0][0]).lower() == 'wal'
        assert (await s.fetchone("PRAGMA synchronous"))[0] == 2
        assert (await s.fetchone("PRAGMA busy_timeout"))[0] == 5000
        tables = await s.list_tables()
        for name in ('dispatch_intents','audit_events','memory_outbox','recoveries','automation_registry'):
            assert name in tables, name
        assert await s.integrity_check() == 'ok'
        await s.close()
asyncio.run(main())
PY
then pass "RuntimeStorage WAL/FULL/schema contract"; else fail "RuntimeStorage contract failed"; fi
if grep -R -q "sqlite.*backup\|backup(" src/onemancompany/core/runtime_storage.py scripts docs/automation/backup-scripts --include='*.py' --include='*.sh'; then pass "SQLite Online Backup API present"; else fail "SQLite Online Backup API missing"; fi

section "Formal employee configuration"
EMP_ROOT="$ROOT_DIR/.onemancompany/company/human_resource/employees"
count=0
for i in $(seq 1 12); do
  id=$(printf '%05d' "$i")
  if [[ -f "$EMP_ROOT/$id/profile.yaml" ]]; then count=$((count + 1)); else fail "$id profile.yaml missing"; fi
done
[[ "$count" -eq 12 ]] && pass "12 formal employee profiles" || fail "formal employee profiles: $count/12"
for id in 00003 00006 00007 00008 00009 00010 00011 00012; do
  [[ -f "$EMP_ROOT/$id/work_principles.md" ]] && pass "$id work principles applied" || fail "$id work principles missing"
done
if [[ -f "$EMP_ROOT/.work-principles-revision.yaml" ]]; then pass "work-principles revision record present"; else fail "work-principles revision record missing"; fi
if [[ -x scripts/apply-work-principles.sh ]]; then pass "work-principles application script present"; else fail "work-principles application script missing"; fi

section "24h team model/role alignment"
if [[ -n "$PYTHON_CMD" ]] && "$PYTHON_CMD" - <<'PY'
from pathlib import Path
import yaml
root=Path('.onemancompany/company/human_resource/employees')
expected={
 '00003':('COO','gpt-5.6-sol'), '00006':('Senior Backend Engineer','gpt-5.6-sol'),
 '00007':('Full-Stack Engineer','gpt-5.6-sol'), '00008':('DevOps/SRE Engineer','gpt-5.6-sol'),
 '00009':('QA Lead','gpt-5.6-sol'), '00010':('Tech Lead','gpt-5.6-sol'),
 '00011':('Mid-level Backend Engineer','gpt-5.6-sol'), '00012':('Automation Test Engineer','gpt-5.6-sol')}
for eid,(role,model) in expected.items():
    data=yaml.safe_load((root/eid/'profile.yaml').read_text()) or {}
    assert data.get('role')==role, (eid,data.get('role'),role)
    assert data.get('llm_model')==model, (eid,data.get('llm_model'),model)
PY
then pass "target roles and models aligned"; else fail "target roles/models are not aligned"; fi

section "Automation and documents"
if [[ -f docs/automation/cron-tasks.yaml ]] && [[ "$(grep -c '^  - id:' docs/automation/cron-tasks.yaml)" -eq 13 ]]; then pass "13 automation manifest entries"; else fail "automation manifest is missing or not 13 entries"; fi
if [[ -n "$PYTHON_CMD" ]] && "$PYTHON_CMD" - <<'PY'
from onemancompany.core.automation_manifest import load_manifest
assert len(load_manifest()) == 13
PY
then pass "automation manifest validates against formal employees"; else fail "automation manifest validation failed"; fi
for f in README.md team-configuration.md startup-guide.md verification-checklist.md DOCUMENT-INDEX.md IMPLEMENTATION-PLAN.md STATUS-REPORT.md; do
  [[ -f "docs/24h-work-mode/$f" ]] && pass "document exists: $f" || fail "document missing: $f"
done

section "Service Gate (must be independently verified)"
BASE_URL="${OMC_HEALTH_URL:-http://localhost:8000}"
if command -v curl >/dev/null 2>&1 && response="$(curl -fsS --max-time 5 "$BASE_URL/api/health" 2>/dev/null)"; then
  if [[ -n "$PYTHON_CMD" ]] && printf '%s' "$response" | "$PYTHON_CMD" -c 'import json,sys; d=json.load(sys.stdin); required=("runtime_storage","checkpoint_store","provider_gateway","automation_registry"); assert all(k in d for k in required); assert d["runtime_storage"]=="healthy" and d["checkpoint_store"]=="healthy"' >/dev/null 2>&1; then
    pass "service health schema and storage Gate"
  else
    fail "service health response is not healthy or schema is incomplete"
  fi
else
  fail "service Gate failed: $BASE_URL is not running"
fi

section "Protected historical data"
ITER=".onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009"
[[ -d "$ITER" ]] && pass "iter_009 exists and remains protected" || fail "iter_009 missing (do not recreate automatically)"

printf '\n==========================================\n'
printf 'PASS=%s FAIL=%s WARN=%s\n' "$PASS_COUNT" "$FAIL_COUNT" "$WARN_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf '✅ All readiness checks passed. Formal 24h mode may proceed to final independent verification.\n'
  exit 0
fi
printf '❌ Readiness Gate failed. Do not start formal 24h mode or create the final standard-v2 iteration.\n'
exit 1
