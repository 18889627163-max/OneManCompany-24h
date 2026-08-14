# Runtime Warning Remediation Report — 2026-08-14

## Scope

This execution implemented the approved remediation for historical invalid ex-employees, missing default skill hook files, false system-automation project warnings, and non-authoritative automation/adhoc TaskTree paths.

## Completed code changes

- Fixed flat `ask_first` skill hooks so they are skipped before trigger-script resolution; no fake `create-pr.sh` was added.
- Added controlled default-skill reconciliation with dry-run, SHA-256 action reports, atomic file replacement, conflict preservation, protected admin API, CLI, RuntimeStorage audit, and audited startup reconciliation.
- Added `POST /api/admin/skills/reconcile` and `onemancompany-admin skills reconcile`.
- Classified `_sys_*` and `_auto_*` execution as system context and prevented named-project identity, product, workspace, history, and workflow lookup while retaining TaskTree/dependency context.
- Made scheduler `entry.tree_path` authoritative for formal tree-tool load/save/persist operations. Explicit missing trees fail closed instead of silently creating an empty tree.
- Expanded HR filesystem backup coverage to active employees, ex-employees, and quarantine-employees.
- Added per-file and archive SHA-256 manifests plus traversal-safe isolated verification/extraction.
- Added audited archived-employee quarantine service, protected API, and CLI. Execution requires a verified backup manifest and checks that an active employee profile remains unchanged.

## Runtime backup and independent restore evidence

A non-destructive HR backup was created and verified in an isolated temporary directory:

- Backup set: `20260814T153802Z-rw05`
- Manifest: `.onemancompany/backups/employees/employees_20260814T153802Z-rw05.manifest.json`
- Archive SHA-256: `7229b366620b5c803f1b3eb81b76e241cca1a8a522d290a41728c80e595e91b4`
- Files verified: `644`
- Active `00010` profile SHA-256 before/after: `2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6`
- `iter_009` sample file: `.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009.yaml`
- `iter_009` sample SHA-256 before/after: `4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626`

The backup contains the two invalid archived records before quarantine and the pre-existing, distinct `00100-legacy-20260813` quarantine record. The independent restore was completed under `/var/folders/dy/kgsk8ztj1nsdd8crjsh2gw3w0000gn/T/tmp.ohUqe0ZJOS`; no live runtime HR directory was overwritten by the restore test.

## Test evidence

Focused remediation suite:

```text
260 passed, 13 warnings
```

Full repository suite:

```text
4677 passed, 5 skipped, 73 warnings in 159.76s
```

The warnings are existing deprecation/mock-coroutine warnings; no test failed.

## Formal runtime remediation completed

The repository-local backend was not running on port `8000`. The detected `npm exec @1mancompany/onemancompany` process belonged to `/Users/hanzhen/Documents/网络安全渗透`, not this repository, so it was not stopped or modified.

The following controlled runtime actions were completed against this repository:

1. Ran audited dry-run and apply reconciliation for formal employees `00002`—`00005`.
2. Confirmed all four employees now contain `skills/self-improving-agent/hooks/session-logger.sh`.
3. Preserved one customized non-`SKILL.md` conflict per employee; no customization was overwritten.
4. Ran audited dry-run and execute quarantine for invalid archived employees `00010` and `00100` using the verified backup manifest.
5. Moved the archived records to:
   - `quarantine-employees/00010-invalid-20260814T154304Z`
   - `quarantine-employees/00100-invalid-20260814T154305Z`
6. Preserved the existing `quarantine-employees/00100-legacy-20260813` record.
7. Confirmed RuntimeStorage contains `default_skills_reconciled`, `archived_employee_quarantine_planned`, and `archived_employee_quarantined` audit events.
8. Confirmed the active `00010` profile and the protected `iter_009` sample remained unchanged.

Runtime evidence:

```text
active 00010 SHA-256 = 2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6
iter_009 SHA-256      = 4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
archived 00010 source = 80e09030ebfbd6bc39fde3f023311e8adc4a394b0caaf350678e1b4ad13c3a4d
archived 00100 source = 6531574c37c81598c6e0006068981a922636d867ab84e58b9bf9cdfb38f2a9c3
```

## Controlled real-service verification

A repository-local server was started at `2026-08-14 23:50` (Asia/Shanghai) with a current HR/data snapshot and the following safety fences:

```text
OMC_RESTORE_PERSISTED_TASKS=false
OMC_AUTOMATION_ENABLED=false
OMC_MEMORY_ENABLED=false
```

Observed results:

- all 12 formal profiles loaded and all 11 non-CEO employees were returned by `/api/employees`;
- startup reported `Loaded 12 skill hook(s) across all employees`;
- no historical `EmployeeConfig` validation warning appeared;
- no missing `session-logger.sh`/skill-hook warning appeared;
- no `_sys_automation_* project not found` warning appeared;
- no automation/adhoc TaskTree fallback warning appeared;
- `/api/health` returned healthy runtime/checkpoint/provider/automation registry state;
- `scripts/check-system-ready.sh` returned `PASS=35 FAIL=0 WARN=0` while the service was running;
- the server completed a clean shutdown.

The configured RuntimeStorage URL still resolved to the formal `.onemancompany/data/runtime.sqlite3` rather than the temporary data-root copy. Startup therefore appended `default_skills_reconciled` audit events for `00001`—`00012` (sequences 38—49), but persisted TaskTree recovery, the standard automation runner, and the memory worker remained disabled. No business task was resumed or dispatched. The active `00010` profile and protected `iter_009` hashes remained unchanged after shutdown.

The health snapshot also exposed pre-existing operational follow-up outside this warning-remediation scope:

```text
memory_worker_backlog=25
checkpoint_conflicts=7
```

These values must be reconciled before formal 24-hour launch; they do not invalidate the completed employee/hook/system-context/tree-path repair.

No Git commit or push was performed.
