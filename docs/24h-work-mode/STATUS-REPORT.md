# 24 小时工作模式实施状态报告

**检查日期**：2026-08-17
**报告版本**：5.9
**整体状态**：🟡 实施中，尚未正式上线

> 本报告只记录已经由仓库内容、自动化测试、隔离 subprocess 故障注入、隔离真实服务或完整墙钟运行验证的事实。P0、Embedding、Provider 429、standard v2 三阶段服务恢复、独立只读恢复、真实 24 小时墙钟 Gate 和 OneManCompany 真实服务 Smoke 已通过；2026-08-17 已批准把所有 Claude 系列正式员工模型统一迁移为 `gpt-5.6-sol`。当前服务未运行，旧模型基线 `iter_019` 待正式中止，新 iteration、COO/EA 显式验收和 Closure Gate 尚未完成。

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
- ✅ Embedding pending/backoff/recovery Gate 已通过：结构化记忆先以 `pending` 落盘，故障时 outbox `holding` 并持久化 attempt/`next_retry_at`，恢复后同一 memory 补向量并完成；正式 26 条 outbox 未消费。
- ✅ 受控真实 HTTP Provider 429 Gate 已通过：真实 `ChatOpenAI` HTTP 429→进程重启→HTTP 200，TaskNode durable holding、attempt/`next_retry_at`、同 checkpoint thread、dispatch/side-effect 防重放、Memory worker 让位和恢复 UI 全部通过。
- ✅ 测试误触正式 Runtime SQLite 的隔离漏洞已修复；pytest 现在同时隔离 active/ex-employee 路径，解雇流程测试不再写入正式历史员工目录。
- ✅ Checkpoint 修复后的最新完整测试为 `4734 passed, 5 skipped, 73 warnings in 153.06s`；定向 execution checkpoint 回归为 `37 passed`。本轮前后受保护的目录化 `iter_009/task_tree.yaml` SHA-256 仍为 `b3b877e6b584feefe084a40f50a75b7161ae018b42910f9c2e54780e46d087ab`。
- ✅ standard v2 三阶段服务恢复 Gate 已通过：在全新临时 data root 中分别于 dispatch、executor started receipt 和 side-effect ledger completed 边界退出并重启，均沿同一 checkpoint thread 恢复且未重复派发或副作用。
- ✅ SQLite Online Backup 已恢复到独立 data root，并以只读模式完成 TaskTree、checkpoint、receipt、ledger、acceptance、Memory Outbox 和 memory source refs 对账。
- ✅ 24 小时墙钟 Gate 的 `prepare/run/status/finalize`、可恢复 supervisor、资源/health/SQLite/TaskTree 监控和四类故障调度已实现；相关集成回归 `11 passed`，真实后端 8 秒预检及 Provider/Ollama sidecar 预检均通过。
- ✅ 真实 86,400 秒墙钟已于 2026-08-16 13:24:57 Asia/Shanghai 完成并通过：实际 86,400.263 秒、1,437 次监控采样、四类故障全部通过、11 项最终检查全部为 true，Gate 输出 `formal_24h_launch_allowed=true`。
- ✅ OneManCompany 当前版本真实服务 Smoke 已通过：后端/前端与核心 API 返回 HTTP 200，`00002`—`00012` 正式员工均可加载，13 条 automation 注册，RuntimeStorage/checkpoint/memory/sqlite-vec/Embedding/ProviderGateway 全部健康，readiness `PASS=35 FAIL=0 WARN=0`，SQLite 完整且服务干净关闭。
- ✅ Smoke 首轮发现测试重建的非法历史 `ex-employees/00010`；在完整员工归档和 Runtime SQLite 备份后，通过 dry-run、append-only audit 和正式 quarantine 流程处理，第二轮未再出现目标告警，在职 `00010`、两份 `iter_009` 和正式 26 条 Memory Outbox 均保持受保护。
- ❌ `iter_017` 正式复验未通过：`00006`—`00009` self-hosted executor 虽持有正式 `checkpoint_thread_id`，但 SQLite `checkpoints` 表没有对应执行 checkpoint 行；COO 已通过正式 `reject_child()` 拒绝 `00006`。该 iteration 的失败证据已保留，并已通过 `POST /api/task/iter_017/abort` 正式中止，禁止修补或重用。
- ✅ 已修复 self-hosted formal v2 executor 的 checkpoint 边界：在独立 `checkpoint_ns=omc_execution` 中、进入 executor body 前写入 `phase=before_executor_handoff`，写入失败时节点进入 `holding/checkpoint_backend_unavailable`，不允许无 checkpoint 执行；同 generation 恢复继续使用同一 thread，且不会冒充默认 LangGraph graph checkpoint。

### 1.1.1 附件日志复核与新增隔离发现

- 附件前半段的 `_sys_automation_* project not found`、无效 ex-employee profile、`task_tree.yaml not found` 和 Talent Market SSE 断线均发生在 remediation commit/重启验证之前；其中前三类在修复后运行段未再出现。
- Talent Market 的两次 `RemoteProtocolError` 均被 keepalive 自动重连恢复，属于远端 SSE 短断线，不是本地服务崩溃。
- `/health` 返回 404 是调用了不存在的旧路径；正式健康接口是 `/api/health`，附件中该接口返回 200。
- 只读审计发现 `.onemancompany/company/human_resource/ex-employees/00010/profile.yaml` 再次存在，内容哈希与此前已隔离记录一致。根因是 pytest 只隔离 active employee 路径而遗漏 ex-employee 路径；代码测试隔离已修复，但该正式历史记录本轮未删除、未移动，后续必须沿用“验证备份 → dry-run → quarantine → 哈希复核”的受控流程。
- 附件中标记为 `2026-08-15` 的旧日志与当前报告日期相同，但仍只作为当时进程顺序证据；本轮墙钟证据只认新的隔离 run root、durable state 和 JSONL 事件。

### 1.2 本轮恢复修复

- `RuntimeStorage` 新增 side-effect invocation 的 prepare/get/complete/fail 和 recovery audit 接口。
- 正式 v2 工具执行按 node、generation、tool 和 fingerprint 建立 durable ledger。
- reconciliation required 时正式 TaskNode 转为 holding，而不是静默重放或直接 failed。
- ProviderGateway 不再以 `INSERT OR REPLACE` 覆盖旧 request，跨重启保留 attempt、submitted_at 和 retry state。
- retry limit 为 0 时仍保存 `next_retry_at`；成功后以同一事务将 queue 和 retry state 改为 completed/无活动 retry 时间，同时保留 attempt 历史。
- 启动时在 persisted schedule 恢复前运行 checkpoint reconciler；`OMC_RESTORE_PERSISTED_TASKS=false` 时不扫描正式 TaskTree。
- 新增真实 subprocess crash worker：分别使用 `os._exit(87)` 和 `os._exit(88)` 模拟无 finally/close 的进程死亡。
- P0 Gate 增加 `isolated_recovery_crash_resume_and_reconciliation` test group。
- Talent Market 新增 task-bound context 回归测试，覆盖跨 task 关闭原始异常、call/ping owner task 路由和取消启动清理。
- `RuntimeStorage` 新增 `omc_execution` 正式执行 checkpoint namespace 及 get/put/config 接口；execution checkpoint 只保存脱敏后的结构化恢复状态和任务描述 SHA-256，不保存原始 prompt。
- `EmployeeManager._execute_task()` 在 formal v2 executor handoff 前强制持久化 execution checkpoint；checkpoint backend 不可用时 fail closed 到 holding。

### 1.3 仍未完成

- ✅ 2026-08-17 已按负责人明确批准，将 `00003`、`00005`、`00006`、`00007`、`00009`、`00010` 的 Claude 系列模型统一改为 `gpt-5.6-sol`；正式 profile、工作原则、目标文档和 Gate 基线同步更新。
- ⏳ `iter_019` 仍保留旧模型执行证据，当前服务未运行；下次启动必须禁用自动恢复，先正式中止 `iter_019`，再创建新 iteration。

- ✅ 完整 24 小时墙钟已通过：2026-08-15 13:24:57 至 2026-08-16 13:24:57，Provider 429、Embedding 不可用、后端重启和 SQLite lock 均按 durable schedule 注入并恢复。
- ❌ `iter_017` 已正式失败并中止：self-hosted executor 缺少真实 `omc_execution` checkpoint 行；原始证据已保留，不修补、不重用。
- ❌ `iter_018` 已正式失败并中止：COO 对 `00009` 先后使用相同 `task_key` 但不同 `depends_on` 重派发，durable tool invocation ledger 正确返回 `tool_idempotency_conflict` 并 fail closed；原始证据已保留，不修补、不重用。
- 🧾 `iter_019` 历史事实：前三个 child 已完成，COO 曾因旧配置 `claude-opus-5` 无可用 channel 保持 `holding/provider_capacity`。该 iteration 现在属于旧模型基线，等待服务恢复后正式中止。
- ⛔ 模型迁移已经获得明确批准，但不得在 `iter_019` 的旧 checkpoint thread 中混用新模型；不得手工改 TaskTree、不得伪造 receipt/acceptance。服务恢复后先正式 abort，再创建新 iteration。

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
| 受控真实 HTTP Provider 429 Gate | ✅ 通过 | `REAL-PROVIDER-429-GATE-REPORT.json`；ChatOpenAI HTTP 429→重启→200、durable holding、优先级/Memory 让位、同 thread 恢复和 UI 通过 |
| standard v2 三阶段服务恢复 Gate | ✅ 隔离维护窗口通过 | `REAL-SERVICE-RECOVERY-GATE-REPORT.json`；dispatch、executor started、side-effect 三边界恢复及独立只读恢复对账全部通过，正式服务未停止 |
| memory ACL/可信度 | ✅ 真实模型 Gate 通过 | employee/project ACL、candidate/status 和 Prompt budget 已用本地 Ollama 实测 |
| sqlite-vec/reindex | ✅ 隔离 Gate 通过 | `v0.1.9`、混合检索、shadow rebuild、原子切换和失败降级已验证 |
| 真实 Embedding Provider | ✅ 隔离恢复 Gate 通过 | Ollama `0.32.12`、`embeddinggemma`、768 维；pending/holding/backoff、同 memory 补向量和动态 health 已通过，正式 26 条 outbox 未消费 |
| `iter_017` 正式复验 | ❌ 已失败并正式中止 | 暴露 self-hosted executor 缺少真实 SQLite checkpoint 行；失败证据保留，不修补、不重用 |
| `iter_018` 正式复验 | ❌ 已失败并正式中止 | 相同 `task_key` 不同参数触发 `tool_idempotency_conflict`；fail closed，证据保留，不修补、不重用 |
| `iter_019` 正式复验 | 🧾 旧模型基线，待正式中止 | 旧配置下曾因 `claude-opus-5` 无 channel holding；统一迁移后禁止继续复用该 thread |
| execution checkpoint 修复 | ✅ 代码与测试通过，待真实服务复验 | 独立 `omc_execution` namespace；定向 `37 passed`，完整 `4734 passed, 5 skipped` |
| dispatch/closure | ✅ 代码与专项测试通过 | 最终仍需全新正式 iteration 证据 |
| 24 小时墙钟演练 | ✅ 通过 | `reports/WALL-CLOCK-GATE-FINAL-20260816.md`；真实 86,400 秒、四类故障、11 项 final checks 全部通过 |
| OneManCompany 真实服务 Smoke | ✅ 通过 | `reports/REAL-SERVICE-SMOKE-20260816.md`；核心页面/API HTTP 200、11 名非 CEO 员工、13 条 automation、health 全绿、readiness `35/0/0`、SQLite 完整、clean shutdown |
| `iter_009` 不变性 | ✅ 通过 | legacy `iterations/iter_009.yaml`=`4c8cdb...`；目录化 `iter_009/task_tree.yaml`=`b3b877...`；两个不同文件在本 Gate 前后均不变 |

## 3. 已验证证据

### 3.1 自动化测试

最终全量测试：

```text
4734 passed, 5 skipped, 73 warnings in 153.06s
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
wall_clock_gate = passed
formal_24h_launch_allowed = true
```

`formal_24h_launch_allowed=true` 表示真实 24 小时墙钟 Gate 自身已满足进入下一阶段的条件，不表示整个项目已经正式上线。全新四人 standard v2 iteration、COO 显式验收和最终 Closure Gate 仍是正式上线阻塞项。

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
00001 CEO                  gpt-5.6-sol
00002 HR                   deepseek-v4-flash
00003 COO                  gpt-5.6-sol
00004 EA                   gpt-5.6-sol
00005 CSO                  gpt-5.6-sol
00006 Senior Backend       gpt-5.6-sol
00007 Full-Stack           gpt-5.6-sol
00008 DevOps/SRE           gpt-5.6-sol
00009 QA Lead              gpt-5.6-sol
00010 Tech Lead            gpt-5.6-sol
00011 Mid-level Backend    gpt-5.6-sol
00012 Automation Test      gpt-5.6-sol
```

正式运行目录包含 00001—00012；测试员工 `00100` 不存在。历史隔离目录不重新纳入正式员工。

## 5. 下一步执行顺序

1. **已完成：** 只读对账正式 RuntimeStorage。7 条 finding 均为旧 `_sys_automation_*` adhoc thread 假 orphan，正式 actionable conflict 为 0；outbox 当前为 26 条 pending、attempt=0，其中普通任务 15 条、system automation 11 条。没有删除、消费、重放或修改 `iter_009`。详见 `reports/RUNTIME-STATE-RECONCILIATION-20260814.md`。
2. **隔离 Gate 已完成：** sqlite-vec `v0.1.9`、混合检索、versioned shadow reindex、原子切换、失败结构化降级和测试正式库隔离通过；详见 `reports/MEMORY-VECTOR-GATE-20260814.md`。
3. **已完成：** 真实 Embedding Provider 隔离 Gate 使用本地 Ollama `embeddinggemma` 通过；endpoint/model/768 维、真实向量写入、语义检索、ACL、状态过滤和 Prompt 预算均通过，正式 26 条 outbox 未消费。详见 `reports/REAL-EMBEDDING-GATE-20260814.md`。
4. **已完成：** Embedding pending/backoff/recovery Gate 通过；故障时结构化记忆保留、outbox holding，恢复后同一 memory 补向量并完成。详见 `reports/EMBEDDING-RECOVERY-GATE-20260814.md`。
5. **已完成：** 受控真实 HTTP Provider 429 Gate 通过；ChatOpenAI HTTP 429→重启→200、TaskNode holding、durable backoff、优先级、memory worker 让位和恢复 UI 均已验证。详见 `reports/REAL-PROVIDER-429-GATE-20260814.md`。
6. **已澄清：** 历史 `4c8cdb...` 是 legacy `iterations/iter_009.yaml`，当前目录化 TaskTree `b3b877...` 是另一个文件；二者本轮前后均不变。
7. **已完成：** 在隔离维护窗口和全新临时 data root 中创建 `recovery-drill-20260815/iter_001` standard v2 恢复演练 iteration，未使用或修改 `iter_009`。
8. **已完成：** 在 dispatch、executor started receipt 和 side-effect ledger completed 三个边界分别停止/重启服务，同 thread、receipt、ledger、外部副作用和 acceptance 对账全部通过。
9. **已完成：** 将 SQLite Online Backup 恢复到独立 data root，以只读模式完成 TaskTree/checkpoint/store/outbox/dispatch/ledger/acceptance/source refs 对账。
10. **已完成：** 真实 24 小时墙钟于 2026-08-16 13:24:57 通过；资源、Provider、Memory Outbox、checkpoint、TaskTree、正式基线哈希和四类故障结果均已对账。
11. **已完成：** OneManCompany 当前版本真实服务 Smoke 已通过，证据见 `reports/REAL-SERVICE-SMOKE-20260816.md`。
12. 已批准统一模型迁移：服务恢复后先通过正式 API 中止旧模型基线 `iter_019`，再创建全新 standard v2 iteration；不得在旧 checkpoint thread 中混用新模型。

## 6. 上线判定

当前判定：

```text
实施中，尚未正式上线
wall_clock_gate=passed
formal_24h_launch_allowed=true
project_formal_launch_allowed=false
```

不得仅依据 24 小时墙钟通过宣称项目已正式上线。OneManCompany 当前版本真实服务 Smoke 证据已经具备；最终上线仍必须具备全新四人 standard v2 iteration、显式 `accept_child()`/`reject_child()` acceptance audit 和通过的 Closure Gate；`Auto-accepted` 不能替代显式验收。Android/ADB、FFmpeg/FFprobe 和 cloud-test-platform 不属于本项目当前上线判定。

## 2026-08-14 Runtime warning remediation execution

Code remediation, audited formal skill reconciliation, historical record quarantine, system automation context isolation, authoritative automation/adhoc TaskTree paths, complete HR backup/restore verification, and a controlled repository-local service startup are complete. Full suite: `4677 passed, 5 skipped`; live readiness: `PASS=35 FAIL=0 WARN=0`. The target warnings did not recur, and active employee `00010` plus protected `iter_009` hashes remained unchanged. The service health snapshot exposed `checkpoint_conflicts=7` and `memory_worker_backlog=25`; these are separate formal-launch follow-up items and must be reconciled without deleting or replaying authoritative state. See `reports/RUNTIME-WARNING-REMEDIATION-20260814.md`.


## 2026-08-14 RuntimeStorage read-only reconciliation

正式 Runtime SQLite 通过 URI `mode=ro` 完成只读对账，检查前后 SHA-256、大小和 mtime 完全一致。当前完整测试结果为 `4679 passed, 5 skipped, 72 warnings in 139.38s`。7 条 recovery finding 全部是旧 `_sys_automation_*` adhoc checkpoint 的假 orphan，正式 actionable finding 为 0；26 条 Memory Outbox 记录全部为 `pending/attempt=0/last_error=null`，并非 worker 失败。代码现已分别报告 actionable finding 与 legacy system orphan，并提供受 token/loopback 保护的只读管理 API/CLI。`iter_009.yaml` SHA-256 保持 `4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626`。

## 2026-08-14 Memory vector Gate 与测试隔离修复

隔离 ARM64 Runtime SQLite 已实际加载 `sqlite-vec v0.1.9`，完成结构化过滤优先的混合检索、v1→v2 versioned shadow reindex、单事务 active 切换、模型空间不兼容时结构化降级，以及失败时 outbox 保持 `pending/attempt=0`。完整测试为 `4693 passed, 5 skipped, 73 warnings in 135.83s`。

首次完整测试暴露出 lifespan 测试误用正式 `.onemancompany/data/runtime.sqlite3` 的隔离漏洞，并对正式库执行了 OMC schema v4 additive migration。没有回滚或覆盖正式库；只读审计确认 `integrity_check=ok`、index contract=0、archived vector=0、正式 outbox 26 条仍全部未尝试。代码现已让相对数据库路径跟随 `OMC_DATA_ROOT`，unit lifespan 使用临时数据库，并在 pytest 直接访问仓库正式库时 fail closed。修复后完整测试前后正式库 SHA-256 保持 `2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de`，大小保持 `120377344`。本地 Ollama `embeddinggemma` 真实 Embedding Gate 已通过；在该阶段当时，聊天 Provider 429/worker 让位、服务恢复和 24 小时墙钟尚未完成。随后 Provider 429 与 standard v2 三阶段服务恢复 Gate 已于 2026-08-15 前完成；24 小时墙钟仍未完成，因此 `formal_24h_launch_allowed=false`。详见 `reports/MEMORY-VECTOR-GATE-20260814.md`。


## 2026-08-14 受控真实 HTTP Provider 429 Gate

真实 `ChatOpenAI` HTTP 客户端在全新临时 data root 中完成 HTTP 429→进程重启→HTTP 200 恢复。16 项 Gate 断言全部通过：TaskNode 保持 `holding/provider_capacity`，attempt 与 `next_retry_at` 跨重启一致，正式业务先于 Memory 后台请求，Memory worker 自动让位，同一 checkpoint thread 和 Provider request 恢复，dispatch/side-effect/HumanMessage 不重复，前端显示并清除“等待模型容量”。完整测试为 `4706 passed, 5 skipped, 72 warnings in 169.46s`。正式 Runtime SQLite 哈希保持 `2dfa8a...`，26 条 outbox 仍为 `pending/attempt=0`。只读核对确认 legacy `iterations/iter_009.yaml` 为 `4c8cdb...`，目录化 `iter_009/task_tree.yaml` 为 `b3b877...`；两个文件本 Gate 前后均未变化。详见 `reports/REAL-PROVIDER-429-GATE-20260814.md`。


## 2026-08-15 Standard v2 三阶段真实服务恢复与独立只读恢复 Gate

在全新临时 data root 和隔离维护窗口中创建 `recovery-drill-20260815/iter_001` standard v2 iteration。三个独立服务进程分别在 dispatch persisted、executor started receipt 和 side-effect ledger completed 边界保存 LangGraph checkpoint 后以退出码 87 强制结束，再由全新进程沿同一 checkpoint thread 恢复。22 项检查全部通过：原始 HumanMessage 未重复，三个 dispatch intent/started receipt/side-effect ledger/外部 counter 均严格一次，execution generation 不变，且三个子节点均通过正式 `accept_child()` 产生 acceptance audit。

随后使用 SQLite Online Backup API 创建一致性镜像并恢复到独立 data root；恢复库以 URI `mode=ro` 和 `PRAGMA query_only=ON` 打开，TaskTree、checkpoint、dispatch、executor receipt、ledger、acceptance audit、Memory Outbox 和 source refs 与源数据完全一致。正式 Runtime SQLite 和两份受保护 `iter_009` 文件哈希前后不变，正式 Outbox 保持 `pending=26/attempted=0`。详见 `reports/REAL-SERVICE-RECOVERY-GATE-20260815.md`。专项回归为 `21 passed`，完整测试为 `4708 passed, 5 skipped, 72 warnings in 175.04s`。本轮未停止正式服务，因此下一阶段仍是 24 小时墙钟、真实设备 smoke 和最终四人 standard v2 iteration；`formal_24h_launch_allowed=false`。
