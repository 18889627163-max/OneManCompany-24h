# 24 小时工作模式实施状态报告

**检查日期**：2026-08-14  
**报告版本**：4.9
**整体状态**：🟡 实施中，尚未正式上线

> 本报告只记录已经由仓库内容、自动化测试、隔离 subprocess 故障注入或隔离真实服务验证的事实。P0 与隔离 Recovery Gate 通过，不等于真实云 Provider、正式业务服务重启、24 小时墙钟和最终 standard v2 iteration 已通过。

## 1. 当前结论

### 1.1 已完成

- ✅ SQLite 单机持久化方案已落地：`RuntimeStorage + AsyncSqliteSaver + AsyncSqliteStore`。
- ✅ RuntimeStorage 已启用 WAL、`synchronous=FULL`、`busy_timeout=5000`、schema setup、健康检查和 SQLite Online Backup。
- ✅ workflow contract v2、稳定 checkpoint thread ID/execution generation、ProviderGateway、dispatch intent/receipt、side-effect ledger、Memory Outbox 和 audit 基础已落地。
- ✅ 正式 v2 副作用工具接入 durable invocation ledger：completed 返回缓存结果，prepared/failed 禁止静默重放，fingerprint 冲突 fail closed。
- ✅ checkpoint reconciler 已接入启动生命周期，并实现 TaskTree-first 的 resumable/missing/conflict/orphan 矩阵。
- ✅ dispatch 幂等、executor started receipt、显式 `accept_child()`/`reject_child()` 和 Closure Gate 已有专项测试保护。
- ✅ 长期记忆 namespace/ACL、candidate/verified/disputed/superseded 状态、管理 API、脱敏边界和结构化降级路径已落地。
- ✅ 00001—00012 正式 profile 全部存在；00006—00010 已对齐 24 小时团队目标；00011、00012 已进入正式运行目录。
- ✅ 00002—00012 共 11 份工作原则已原子应用，source/runtime SHA-256 一致。
- ✅ 13 条 automation manifest 已校验并注册，注册具备 durable receipt 和重启幂等。
- ✅ P0 Gate 重跑通过：`standard_v2_p0=passed`，并已纳入隔离 recovery test group。
- ✅ 隔离 checkpoint crash/resume 通过：同一 thread 恢复，原始 HumanMessage 仅一份，副作用一次，未完成 step 恢复后完成。
- ✅ 隔离模拟 Provider 429 holding/resume 通过：attempt 和 `next_retry_at` 跨进程保留，恢复 callable 仅执行一次。
- ✅ checkpoint reconciler 跨 RuntimeStorage 生命周期读取持久 checkpoint，并对 missing/conflict/orphan 写幂等 recovery audit。
- ✅ 隔离 memory-enabled 真实服务验证通过：health、readiness、在线备份、直接 integrity check 和 clean shutdown 均通过。
- ✅ `backup-all.sh`/`restore.sh` 已统一遵守 `OMC_DATA_ROOT`；隔离离线备份和独立目标恢复演练均通过。
- ✅ `iter_009` 未迁移、未恢复、未修改；内容哈希保持不变。
- ✅ Talent Market MCP/SSE 已改为单一 owner task 生命周期；SSE/Session 的建立、工具调用、ping 和关闭不再跨 asyncio task。
- ✅ Runtime warning remediation 代码与正式文件操作已完成：`00002`—`00005` skill hook 已受控补齐，历史无效 `00010`/`00100` 已在验证备份后隔离，审计与回滚证据完整。
- ✅ system automation prompt context 与 automation/adhoc TaskTree 权威路径已修复。
- ✅ 本轮告警修复完成受控真实服务验证：12 个正式 profile 和 12 个 skill hooks 正常加载，员工 API 返回 11 名非 CEO 员工，目标告警未复现，readiness `PASS=35 FAIL=0 WARN=0`，服务干净关闭。
- ✅ 正式 RuntimeStorage 只读对账完成：7 条 finding 全部是旧 system automation 假 orphan，正式 actionable finding 为 0；26 条 outbox 均为未尝试 pending，没有删除、消费或重放。
- ✅ sqlite-vec `v0.1.9` 已在隔离 ARM64 Runtime SQLite 实际加载；混合检索、v1→v2 shadow reindex、失败保持旧 active、结构化降级和 outbox 不消费通过。
- ✅ embedding model/dimensions/text fields/Provider fingerprint 已纳入 index identity，同版本漂移 fail closed；管理 API/CLI 已执行真实原子 reindex，而非占位。
- ✅ 测试误触正式 Runtime SQLite 的隔离漏洞已修复；pytest 现在同时隔离 active/ex-employee 路径，解雇流程测试不再写入正式历史员工目录。
- ✅ 最新完整测试 `4693 passed, 5 skipped, 73 warnings in 135.83s`；正式 Runtime SQLite、active `00010`、当前 archived `00010` 和 `iter_009` 的 SHA-256、大小与 mtime 在测试前后完全一致。

### 1.1.1 附件日志复核与新增隔离发现

- 附件前半段的 `_sys_automation_* project not found`、无效 ex-employee profile、`task_tree.yaml not found` 和 Talent Market SSE 断线均发生在 remediation commit/重启验证之前；其中前三类在修复后运行段未再出现。
- Talent Market 的两次 `RemoteProtocolError` 均被 keepalive 自动重连恢复，属于远端 SSE 短断线，不是本地服务崩溃。
- `/health` 返回 404 是调用了不存在的旧路径；正式健康接口是 `/api/health`，附件中该接口返回 200。
- 只读审计发现 `.onemancompany/company/human_resource/ex-employees/00010/profile.yaml` 再次存在，内容哈希与此前已隔离记录一致。根因是 pytest 只隔离 active employee 路径而遗漏 ex-employee 路径；代码测试隔离已修复，但该正式历史记录本轮未删除、未移动，后续必须沿用“验证备份 → dry-run → quarantine → 哈希复核”的受控流程。
- 附件中标记为 `2026-08-15` 的日志时间晚于本报告当前日期 `2026-08-14`，因此只作为进程顺序证据，不作为当前日期或墙钟演练证据。

### 1.2 本轮恢复修复

- `RuntimeStorage` 新增 side-effect invocation 的 prepare/get/complete/fail 和 recovery audit 接口。
- 正式 v2 工具执行按 node、generation、tool 和 fingerprint 建立 durable ledger。
- reconciliation required 时正式 TaskNode 转为 holding，而不是静默重放或直接 failed。
- ProviderGateway 不再以 `INSERT OR REPLACE` 覆盖旧 request，跨重启保留 attempt、submitted_at 和 retry state。
- retry limit 为 0 时仍保存 `next_retry_at`；成功后状态改为 completed 并清空 queue 的 retry 时间。
- 启动时在 persisted schedule 恢复前运行 checkpoint reconciler；`OMC_RESTORE_PERSISTED_TASKS=false` 时不扫描正式 TaskTree。
- 新增真实 subprocess crash worker：分别使用 `os._exit(87)` 和 `os._exit(88)` 模拟无 finally/close 的进程死亡。
- P0 Gate 增加 `isolated_recovery_crash_resume_and_reconciliation` test group。
- Talent Market 新增 task-bound context 回归测试，覆盖跨 task 关闭原始异常、call/ping owner task 路由和取消启动清理。

### 1.3 仍未完成

- ⏳ 使用**全新专用 standard v2 演练 iteration**执行真实后端服务停止/重启；不得使用 `iter_009`。
- ⏳ 在真实 dispatch、executor started 和正式业务 side-effect 阶段分别注入故障并完成 receipt 对账。
- ⏳ 使用受控真实云 Provider 验证 HTTP 429、长 backoff、优先级竞争和恢复 UI；当前 Provider 演练为隔离模拟。
- ⏳ memory worker 与正式 Agent 的真实 Provider 并发槽位让位验证。
- ✅ 真实 Embedding Provider 隔离 Gate 已通过：本地 Ollama `0.32.12` + `embeddinggemma` 返回 768 维向量；真实写入/语义检索、ACL、candidate/status、去重、metadata 和 Prompt 预算全部通过。正式 worker pending/backoff 和 Provider 让位仍待验证。
- ⏳ 独立恢复后针对真实业务 checkpoint/TaskTree/dispatch receipt/acceptance audit 的只读对账。
- ⏳ 完整 24 小时墙钟运行、真机 smoke、FFmpeg/FFprobe 证据。
- ⏳ 创建全新 standard v2 iteration，完成四人正式复验和显式验收。

## 2. Gate 总表

| Gate | 状态 | 证据/说明 |
|---|---|---|
| P0 正式 Gate | ✅ 通过 | `P0-GATE-REPORT.json`，`standard_v2_p0=passed` |
| 隔离 Recovery Gate | ✅ 通过 | `RECOVERY-GATE-REPORT.json`；crash/resume、模拟 429、reconciler 通过 |
| 00006—00010 对齐 | ✅ 通过 | role/model 与目标矩阵一致 |
| 00011、00012 创建 | ✅ 通过 | 正式 profile、工作原则、任务索引和 automation 文件存在 |
| automation 注册 | ✅ 通过 | 13 条 manifest，health 显示 `automation_registered=13` |
| 工作原则原子应用 | ✅ 通过 | 11 份 source/runtime hash 一致 |
| 一致性在线备份 | ✅ 通过 | Online Backup + manifest + `PRAGMA integrity_check=ok` |
| 历史 HR archive 备份覆盖 | ✅ 完成 | active/ex-employees/quarantine-employees 已纳入同一 644-file backup set，并通过独立恢复校验 |
| 独立 SQLite 恢复演练 | ✅ 通过 | 合成 checkpoint/store/outbox/audit/dispatch/automation 对账通过 |
| 隔离真实服务 verify | ✅ 通过 | memory-enabled、readiness 35/35、clean shutdown |
| 本轮告警受控真实服务 verify | ✅ 通过 | profile/hook/API 正常，目标告警未复现，readiness 35/35，clean shutdown |
| checkpoint 隔离 subprocess 恢复 | ✅ 通过 | `os._exit(87)` 后同 thread resume，副作用不重放 |
| Provider 隔离 holding/resume | ✅ 通过 | 模拟 429，`os._exit(88)` 后 durable resume |
| TaskTree/checkpoint reconciler | ✅ 通过 | resumable/missing/conflict/orphan + 第二次运行幂等 |
| RuntimeStorage 只读对账 | ✅ 通过 | 7 条 legacy system orphan，正式 actionable=0；26 条 outbox 分类完成且数据库哈希不变 |
| MCP/SSE 生命周期 | ✅ 本地回归通过 | owner task 统一 enter/use/exit；真实远端断线演练待完成 |
| 真实云 Provider Gate | 🟡 待演练 | 尚未发起真实云 429/并发限制故障注入 |
| 真实业务服务重启 Gate | 🟡 待演练 | 尚未针对新专用正式演练 iteration 执行 |
| memory ACL/可信度 | ✅ 真实模型 Gate 通过 | employee/project ACL、candidate/status 和 Prompt budget 已用本地 Ollama 实测 |
| sqlite-vec/reindex | ✅ 隔离 Gate 通过 | `v0.1.9`、混合检索、shadow rebuild、原子切换和失败降级已验证 |
| 真实 Embedding Provider | ✅ 隔离 Gate 通过 | Ollama `0.32.12`、`embeddinggemma`、768 维、sqlite-vec `v0.1.9`；正式 worker 故障恢复待测 |
| dispatch/closure | ✅ 代码与专项测试通过 | 最终仍需全新正式 iteration 证据 |
| 24 小时墙钟演练 | ❌ 未执行 | 正式上线阻塞项 |
| 真机 smoke | ❌ 未执行 | 正式上线阻塞项 |
| `iter_009` 不变性 | ✅ 通过 | 当前受保护文件哈希 `4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626`；本次只读对账前后不变 |

## 3. 已验证证据

### 3.1 自动化测试

最终全量测试：

```text
4693 passed, 5 skipped, 73 warnings in 135.83s
```

本轮 MCP/SSE 相关模块回归：

```text
155 passed in 27.17s
```

完整单元测试：

```text
4628 passed, 2 skipped, 74 warnings in 152.84s
```

隔离恢复专项：

```text
4 passed in 2.08s
```

P0 Gate 内 recovery group：

```text
isolated_recovery_crash_resume_and_reconciliation
4 passed in 1.95s
```

P0 其他专项：

```text
runtime/checkpoint/provider    25 passed
dispatch idempotency           4 passed
explicit acceptance/closure   10 passed
memory/secret boundary        21 passed
automation registration        5 passed
```

现有 warnings 主要来自测试 mock 未 await、LangGraph `create_react_agent` 弃用提示和 Starlette/httpx 弃用提示；当前没有失败测试，但 warnings 仍应单独消减。

### 3.2 Recovery Gate 核心结果

```text
checkpoint thread = omc:recovery-project:iter_001:recovery-node:g1
HumanMessage       = 1
side effect count  = 1
finalize count     = 1
Provider hold      = holding, attempt=1, next_retry_at!=null
Provider resume    = completed, attempt=1, next_retry_at=null
reconciler         = resumable=1, missing=1, conflicts=1, orphans=1
formal launch      = false
```

详细报告：

- `reports/CHECKPOINT-CRASH-RESUME-20260814.md`
- `reports/PROVIDER-HOLDING-RESUME-20260814.md`
- `reports/CHECKPOINT-RECONCILIATION-20260814.md`
- `reports/RECOVERY-GATE-REPORT.json`
- `reports/MCP-SSE-LIFECYCLE-20260814.md`

### 3.3 P0 Gate

```text
standard_v2_p0 = passed
formal_24h_launch_allowed = false
```

`formal_24h_launch_allowed=false` 是预期结果：P0 和隔离 Recovery Gate 已完成，但真实云 Provider、真实业务服务重启、embedding/vector、24 小时墙钟、真机 smoke 和最终 iteration 验收尚未完成。

### 3.4 正式数据保护与并发服务说明

恢复测试和 recovery workers 全部使用 pytest `tmp_path` 与独立 `OMC_DATA_ROOT`，测试代码没有加载正式 projects tree 或正式 Runtime SQLite。

2026-08-14 20:07（Asia/Shanghai）本次复核时，未检测到 `python -m onemancompany.main` 后端进程。此前报告记录的 PID 32891 已不再运行；本轮未主动停止该进程。后续启动真实服务恢复演练前仍必须重新检查进程和正式数据活动，不能把一次“未运行”检查当作长期维护窗口。当前审计边界保持不变：

- 正式 projects tree 不由 recovery workers 创建测试项目；
- recovery worker 的 `recovery-project` 只存在于临时 data root；
- `iter_009` 哈希仍为固定值；
- 不使用旧快照覆盖正式 Runtime SQLite；
- 若重新出现 live-service WAL/SHM 写入，必须按 concurrent activity 单独记录，不冒充测试污染，也不擅自清理。

详细说明见 `reports/FORMAL-DATA-INTEGRITY-20260814.md`。

### 3.5 隔离 memory-enabled 服务

隔离服务验证结果：

```json
{
  "runtime_storage": "healthy",
  "checkpoint_store": "healthy",
  "memory_store": "healthy",
  "sqlite_vec": "unavailable",
  "embedding": "degraded",
  "provider_gateway": "healthy",
  "automation_registry": "healthy",
  "automation_registered": 13,
  "provider_running": 0,
  "provider_queued": 0,
  "memory_worker_backlog": 0,
  "checkpoint_conflicts": 0
}
```

readiness：

```text
PASS=35 FAIL=0 WARN=0
```

在线备份和 clean shutdown 已通过；这不等同于真实 embedding/vector 已启用。

## 4. 当前正式配置

```text
00003 COO                  claude-opus-5
00006 Senior Backend       claude-opus-5
00007 Full-Stack           claude-sonnet-5
00008 DevOps/SRE           gpt-5.6-sol
00009 QA Lead              claude-sonnet-5
00010 Tech Lead            claude-fable-5
00011 Mid-level Backend    gpt-5.6-sol
00012 Automation Test      gpt-5.6-sol
```

正式运行目录包含 00001—00012；测试员工 `00100` 不存在。历史隔离目录不重新纳入正式员工。

## 5. 下一步执行顺序

1. **已完成：** 只读对账正式 RuntimeStorage。7 条 finding 均为旧 `_sys_automation_*` adhoc thread 假 orphan，正式 actionable conflict 为 0；outbox 当前为 26 条 pending、attempt=0，其中普通任务 15 条、system automation 11 条。没有删除、消费、重放或修改 `iter_009`。详见 `reports/RUNTIME-STATE-RECONCILIATION-20260814.md`。
2. **隔离 Gate 已完成：** sqlite-vec `v0.1.9`、混合检索、versioned shadow reindex、原子切换、失败结构化降级和测试正式库隔离通过；详见 `reports/MEMORY-VECTOR-GATE-20260814.md`。
3. **已完成：** 真实 Embedding Provider 隔离 Gate 使用本地 Ollama `embeddinggemma` 通过；endpoint/model/768 维、真实向量写入、语义检索、ACL、状态过滤和 Prompt 预算均通过，正式 26 条 outbox 未消费。详见 `reports/REAL-EMBEDDING-GATE-20260814.md`。
4. **下一步：** 在全新临时 Runtime 中验证 Ollama 不可用时的 outbox holding、durable backoff、结构化降级和恢复补向量。
4. 使用受控真实云 Provider 执行低风险 429/并发限制演练，验证 TaskNode holding、backoff、优先级和恢复 UI。
6. 创建全新专用 standard v2 恢复演练 iteration；不使用 `iter_009`，不干扰当前真实服务任务。
7. 在 dispatch、executor started 和 side-effect 后分别停止/重启服务，验证同 thread、receipt、ledger 和 acceptance 对账。
8. 将在线备份恢复到全新 data root，执行 TaskTree/checkpoint/store/outbox/dispatch/acceptance 的只读对账。
9. 执行完整 24 小时墙钟演练和真机 smoke。
10. 所有 Gate 通过后创建全新 standard v2 iteration，完成四人正式复验。

## 6. 上线判定

当前判定：

```text
实施中，尚未正式上线
formal_24h_launch_allowed=false
```

不得仅依据 readiness、P0 Gate、隔离 recovery tests、历史记忆或隔离服务 health 宣称 24 小时模式已正式上线。最终上线必须同时具备真实云 Provider、真实业务服务恢复、embedding/vector、24 小时墙钟、真机证据和显式 acceptance audit。

## 2026-08-14 Runtime warning remediation execution

Code remediation, audited formal skill reconciliation, historical record quarantine, system automation context isolation, authoritative automation/adhoc TaskTree paths, complete HR backup/restore verification, and a controlled repository-local service startup are complete. Full suite: `4677 passed, 5 skipped`; live readiness: `PASS=35 FAIL=0 WARN=0`. The target warnings did not recur, and active employee `00010` plus protected `iter_009` hashes remained unchanged. The service health snapshot exposed `checkpoint_conflicts=7` and `memory_worker_backlog=25`; these are separate formal-launch follow-up items and must be reconciled without deleting or replaying authoritative state. See `reports/RUNTIME-WARNING-REMEDIATION-20260814.md`.


## 2026-08-14 RuntimeStorage read-only reconciliation

正式 Runtime SQLite 通过 URI `mode=ro` 完成只读对账，检查前后 SHA-256、大小和 mtime 完全一致。当前完整测试结果为 `4679 passed, 5 skipped, 72 warnings in 139.38s`。7 条 recovery finding 全部是旧 `_sys_automation_*` adhoc checkpoint 的假 orphan，正式 actionable finding 为 0；26 条 Memory Outbox 记录全部为 `pending/attempt=0/last_error=null`，并非 worker 失败。代码现已分别报告 actionable finding 与 legacy system orphan，并提供受 token/loopback 保护的只读管理 API/CLI。`iter_009.yaml` SHA-256 保持 `4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626`。

## 2026-08-14 Memory vector Gate 与测试隔离修复

隔离 ARM64 Runtime SQLite 已实际加载 `sqlite-vec v0.1.9`，完成结构化过滤优先的混合检索、v1→v2 versioned shadow reindex、单事务 active 切换、模型空间不兼容时结构化降级，以及失败时 outbox 保持 `pending/attempt=0`。完整测试为 `4693 passed, 5 skipped, 73 warnings in 135.83s`。

首次完整测试暴露出 lifespan 测试误用正式 `.onemancompany/data/runtime.sqlite3` 的隔离漏洞，并对正式库执行了 OMC schema v4 additive migration。没有回滚或覆盖正式库；只读审计确认 `integrity_check=ok`、index contract=0、archived vector=0、正式 outbox 26 条仍全部未尝试。代码现已让相对数据库路径跟随 `OMC_DATA_ROOT`，unit lifespan 使用临时数据库，并在 pytest 直接访问仓库正式库时 fail closed。修复后完整测试前后正式库 SHA-256 保持 `2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de`，大小保持 `120377344`。本地 Ollama `embeddinggemma` 真实 Embedding Gate 已通过；正式 worker 故障恢复、聊天 Provider 429、真实服务恢复和 24 小时墙钟仍未完成，因此 `formal_24h_launch_allowed=false`。详见 `reports/MEMORY-VECTOR-GATE-20260814.md`。
