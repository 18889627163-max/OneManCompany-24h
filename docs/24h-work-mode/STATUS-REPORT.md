# 24 小时工作模式实施状态报告

**检查日期**：2026-08-14  
**报告版本**：4.0  
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

### 1.2 本轮恢复修复

- `RuntimeStorage` 新增 side-effect invocation 的 prepare/get/complete/fail 和 recovery audit 接口。
- 正式 v2 工具执行按 node、generation、tool 和 fingerprint 建立 durable ledger。
- reconciliation required 时正式 TaskNode 转为 holding，而不是静默重放或直接 failed。
- ProviderGateway 不再以 `INSERT OR REPLACE` 覆盖旧 request，跨重启保留 attempt、submitted_at 和 retry state。
- retry limit 为 0 时仍保存 `next_retry_at`；成功后状态改为 completed 并清空 queue 的 retry 时间。
- 启动时在 persisted schedule 恢复前运行 checkpoint reconciler；`OMC_RESTORE_PERSISTED_TASKS=false` 时不扫描正式 TaskTree。
- 新增真实 subprocess crash worker：分别使用 `os._exit(87)` 和 `os._exit(88)` 模拟无 finally/close 的进程死亡。
- P0 Gate 增加 `isolated_recovery_crash_resume_and_reconciliation` test group。

### 1.3 仍未完成

- ⏳ 使用**全新专用 standard v2 演练 iteration**执行真实后端服务停止/重启；不得使用 `iter_009`。
- ⏳ 在真实 dispatch、executor started 和正式业务 side-effect 阶段分别注入故障并完成 receipt 对账。
- ⏳ 使用受控真实云 Provider 验证 HTTP 429、长 backoff、优先级竞争和恢复 UI；当前 Provider 演练为隔离模拟。
- ⏳ memory worker 与正式 Agent 的真实 Provider 并发槽位让位验证。
- ⏳ sqlite-vec 与真实云 embedding 的维度探针、向量写入、混合检索和 versioned reindex 演练。
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
| 独立 SQLite 恢复演练 | ✅ 通过 | 合成 checkpoint/store/outbox/audit/dispatch/automation 对账通过 |
| 隔离真实服务 verify | ✅ 通过 | memory-enabled、readiness 35/35、clean shutdown |
| checkpoint 隔离 subprocess 恢复 | ✅ 通过 | `os._exit(87)` 后同 thread resume，副作用不重放 |
| Provider 隔离 holding/resume | ✅ 通过 | 模拟 429，`os._exit(88)` 后 durable resume |
| TaskTree/checkpoint reconciler | ✅ 通过 | resumable/missing/conflict/orphan + 第二次运行幂等 |
| 真实云 Provider Gate | 🟡 待演练 | 尚未发起真实云 429/并发限制故障注入 |
| 真实业务服务重启 Gate | 🟡 待演练 | 尚未针对新专用正式演练 iteration 执行 |
| memory ACL/可信度 | ✅ 代码与测试通过 | 真实向量/embedding 演练仍待完成 |
| sqlite-vec/embedding | 🟡 降级通过 | 当前为结构化路径；真实 vector index/reindex 待完成 |
| dispatch/closure | ✅ 代码与专项测试通过 | 最终仍需全新正式 iteration 证据 |
| 24 小时墙钟演练 | ❌ 未执行 | 正式上线阻塞项 |
| 真机 smoke | ❌ 未执行 | 正式上线阻塞项 |
| `iter_009` 不变性 | ✅ 通过 | 哈希 `fd2b06f7e0525010f5c38ccd122df655436f8722cca285b2b6698d9673dae251` |

## 3. 已验证证据

### 3.1 自动化测试

最终全量测试：

```text
4662 passed, 5 skipped, 73 warnings in 137.13s
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

### 3.3 P0 Gate

```text
standard_v2_p0 = passed
formal_24h_launch_allowed = false
```

`formal_24h_launch_allowed=false` 是预期结果：P0 和隔离 Recovery Gate 已完成，但真实云 Provider、真实业务服务重启、embedding/vector、24 小时墙钟、真机 smoke 和最终 iteration 验收尚未完成。

### 3.4 正式数据保护与并发服务说明

恢复测试和 recovery workers 全部使用 pytest `tmp_path` 与独立 `OMC_DATA_ROOT`，测试代码没有加载正式 projects tree 或正式 Runtime SQLite。

当前工作区同时存在一个从 2026-08-14 11:05:55（Asia/Shanghai）开始运行的真实后端进程：

```text
PID 32891
.venv/bin/python3 -m onemancompany.main
```

该服务持续写入正式 Runtime SQLite WAL 和 00003 的真实业务记录。因此，在不停服的情况下，不能把“全量测试前后正式目录 hash 完全静止”作为本轮恢复测试的证明，也不能回滚这些并发业务写入。当前审计确认：

- 正式 projects tree 未因 recovery workers 创建测试项目；
- recovery worker 的 `recovery-project` 只存在于临时 data root；
- `iter_009` 哈希仍为固定值；
- 未停止、覆盖、恢复或删除 PID 32891 使用的正式数据；
- Runtime SQLite 的 WAL/SHM 变化按 concurrent live-service activity 记录，不冒充测试污染，也不擅自清理。

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

1. 配置受控云 embedding，完成模型/维度探针、sqlite-vec、混合检索、reindex 和结构化降级切换演练。
2. 使用受控真实云 Provider 执行低风险 429/并发限制演练，验证 TaskNode holding、backoff、优先级和恢复 UI。
3. 申请安全维护窗口，创建全新专用 standard v2 恢复演练 iteration；不使用 `iter_009`，不干扰当前真实服务任务。
4. 在 dispatch、executor started 和 side-effect 后分别停止/重启服务，验证同 thread、receipt、ledger 和 acceptance 对账。
5. 将在线备份恢复到全新 data root，执行 TaskTree/checkpoint/store/outbox/dispatch/acceptance 的只读对账。
6. 执行完整 24 小时墙钟演练和真机 smoke。
7. 所有 Gate 通过后创建全新 standard v2 iteration，完成四人正式复验。

## 6. 上线判定

当前判定：

```text
实施中，尚未正式上线
formal_24h_launch_allowed=false
```

不得仅依据 readiness、P0 Gate、隔离 recovery tests、历史记忆或隔离服务 health 宣称 24 小时模式已正式上线。最终上线必须同时具备真实云 Provider、真实业务服务恢复、embedding/vector、24 小时墙钟、真机证据和显式 acceptance audit。
