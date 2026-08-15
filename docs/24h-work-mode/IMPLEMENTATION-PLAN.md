# OneManCompany 24 小时持续运行、可恢复执行与长期记忆实施计划

> 版本：3.8
> 日期：2026-08-14  
> 目标架构：SQLite + LangGraph AsyncSqliteSaver + AsyncSqliteStore/sqlite-vec + TaskTree standard v2  
> 当前状态：实施中；尚未满足正式启动 24 小时模式的条件

---

## 1. 本次重新规划的结论

结合长期记忆设计、补充风险分析、现有 24 小时模式文档以及仓库当前代码，本轮采用以下实施决策：

1. **当前单机版本不引入 PostgreSQL、pgvector 或独立 FAISS 服务。**
2. 使用项目已经接入的官方组件：
   - `AsyncSqliteSaver`：LangGraph 执行 checkpoint；
   - `AsyncSqliteStore`：长期记忆结构化存储；
   - `sqlite-vec`：由 `AsyncSqliteStore` 提供的可选向量检索；
   - `RuntimeStorage`：Provider 队列元数据、执行租约、派发意图、工具幂等账本、Memory Outbox 和审计。
3. **不额外维护一套 FAISS 索引。** 避免 SQLite、FAISS 和 LangGraph Store 三套数据之间出现双写、备份和恢复不一致。
4. 继续以 `task_tree.yaml` 为正式业务状态的唯一权威来源。Checkpoint 和长期记忆都不能替代派发、启动、验收或 Closure Gate 证据。
5. standard v2 是强持久化模式：RuntimeStorage 或 Checkpoint 不可用时不得无状态执行，也不得静默从头重跑。
6. Provider 限流、派发幂等、显式验收和恢复对账仍然优先于长期记忆。
7. `iter_009` 及其历史失败、取消和自动接受记录保持原样，不迁移、不修复、不伪造新证据。
8. **本轮正式采用 SQLite 单机方案。** 该决定已确认；在本轮上线 Gate 通过前，不切换 PostgreSQL，也不同时维护 PostgreSQL/SQLite 双写路径。
9. 等单机方案通过 24 小时演练后，再根据多进程、多主机和写入压力决定是否迁移 PostgreSQL。
10. Embedding Provider 正式采用本机 loopback Ollama `embeddinggemma`（768 维）；不可用时只允许结构化降级，不自动切换模型或向量空间。

### 1.1 为什么本轮选择 SQLite

当前仓库已经具备以下基础：

- `RuntimeStorage` 已启用 SQLite WAL、`synchronous=FULL`、`busy_timeout` 和在线备份；
- 已创建 `AsyncSqliteSaver` 和 `AsyncSqliteStore`；
- Agent Factory 已能把 checkpointer/store 注入 `create_react_agent()`；
- standard v2 已有稳定 thread ID、执行 generation、执行租约和 fencing token 的初步实现；
- ProviderGateway 已有按 Provider/凭证池限流和 durable retry metadata；
- 当前部署目标仍是单机、单个后端调度实例。

因此，继续完成 SQLite 路径的风险和工作量明显低于中途切换 PostgreSQL。PostgreSQL 方案保留为后续扩展目标，而不是当前上线前置条件。

### 1.2 何时重新评估 PostgreSQL

出现以下任一条件时启动迁移评估：

- 需要两个以上后端实例同时调度正式节点；
- 需要跨主机执行或高可用主备；
- SQLite 持续出现不可接受的写锁等待或 checkpoint 延迟；
- 单机备份和恢复时间不能满足恢复目标；
- 长期记忆规模或向量检索延迟超过压测门槛；
- 需要数据库级角色隔离、远程管理或集中审计。

在这些条件出现前，不为“未来可能需要”提前承担 PostgreSQL 运维复杂度。

### 1.3 本轮 SQLite 方案的边界（正式决策）

本轮 SQLite 方案的适用边界固定为：

- 单主机、单个正式调度实例；
- 运行时数据库文件只允许由受控 RuntimeStorage 访问；
- WAL、`synchronous=FULL`、`busy_timeout` 和 SQLite Online Backup 必须启用；
- checkpoint、memory、provider queue、dispatch intent、tool ledger 和 memory outbox 必须纳入同一 backup set；
- SQLite 故障时 standard v2 只能进入 `holding`，不得无状态降级、不得静默从头重跑；
- 向量检索使用 `AsyncSqliteStore` + `sqlite-vec`，不引入 FAISS 双写；
- 需要多实例、多主机或高可用时，先完成 ADR、迁移、双份恢复演练，再评估 PostgreSQL + pgvector。

当前代码仍以 `.onemancompany/data/runtime.sqlite3` 为兼容主文件。多文件拆分不是本轮上线前置条件；只有在定义跨文件 snapshot barrier、统一 backup set、整组恢复和回滚协议后才允许启用。

---

## 2. 文档依据、仓库事实与缺失项

### 2.1 已纳入的核心文档

本计划已读取并整合：

- `docs/24h-work-mode/README.md`
- `docs/24h-work-mode/team-configuration.md`
- `docs/24h-work-mode/startup-guide.md`
- `docs/24h-work-mode/verification-checklist.md`
- `docs/24h-work-mode/DOCUMENT-INDEX.md`
- 本轮提供的长期记忆设计和数据库选型补充文本

### 2.2 当前仓库已经存在的实现

| 能力 | 当前状态 | 本计划处理方式 |
|---|---|---|
| TaskTree workflow contract v2 | 已有字段和部分 Gate/测试 | 完成真实运行路径和闭环测试 |
| stable checkpoint thread ID | 已有 `omc:{project}:{iteration}:{node}:gN` 生成逻辑 | 补齐时间戳、状态和对账 |
| AsyncSqliteSaver/Store | 已在 RuntimeStorage 初始化 | 加强生命周期、索引配置和故障策略 |
| ProviderGateway | 已有并发控制和 retry metadata | 接通所有真实模型入口并补重启恢复 |
| execution lease/fencing | 已有表和基础实现 | 验证所有副作用写入都校验 fencing token |
| dispatch intent | 已有 durable 表和基础方法 | 完成 prepared 到 started 的 reconciler |
| Memory Outbox/Review/Conflict 表 | 已有基础 schema | 实现 worker、ACL、状态机和管理接口 |
| SQLite online backup | 已有基础实现和测试 | 加入保留策略、恢复演练和自动化 |
| `sqlite-vec` 运行依赖 | ARM64 隔离环境已实际加载 `v0.1.9` 并完成向量写入/检索 | 已用本地 Ollama 完成真实 endpoint/model/dimension、向量写入和检索 Gate |
| 长期记忆基础 Store | `AsyncSqliteStore`、Memory Record、ACL、混合检索和 versioned shadow reindex 已实现并隔离演练 | 真实 Ollama、故障恢复和 Provider 让位 Gate 已完成 |
| 长期记忆检索工具 | `search_memory`、`propose_memory` 已实现并按 runtime identity 授权 | 完成真实项目成员和 prompt budget 演练 |
| Memory worker | durable Memory Outbox worker 已接入 lifespan；pending/holding/backoff/恢复补向量和聊天 Agent Provider 让位均已通过隔离 Gate | 正式 26 条 outbox 继续保持未消费 |
| 管理 API/CLI/前端状态 | 管理 API 与 CLI 已实现；health 状态已接入 | 补齐并实测全部前端 attention/holding 展示 |

### 2.3 2026-08-14 实施进度快照

本计划中的 SQLite 基础、正式员工配置、工作原则、automation、P0 Gate、隔离真实服务和隔离 Recovery Gate 已经实际执行，不再是待创建的占位内容。

#### 2.3.1 已完成

- 00001—00012 正式 profile 已存在；00006—00010 已按目标角色/模型对齐，00011、00012 已进入正式运行目录；
- 00002—00012 共 11 份工作原则已原子应用，source/runtime SHA-256 一致；
- 13 条 automation manifest 已校验、持久化注册并通过重启幂等测试；
- SQLite RuntimeStorage、AsyncSqliteSaver、AsyncSqliteStore、ProviderGateway、dispatch intent、side-effect ledger、Memory Outbox 和 audit 已接入生命周期；
- memory namespace/ACL、可信度状态、检索/提案工具、outbox worker、管理审批、脱敏和结构化降级路径已实现并有专项测试；
- 正式 v2 side-effect ledger 已覆盖 completed cache、prepared/failed reconciliation、fingerprint conflict 和敏感结果过滤；
- Provider retry request 不再被 replace 覆盖，attempt、submitted_at、`next_retry_at` 和 retry state 可跨重启保留；
- checkpoint reconciler 已实现 processing/finished/missing/orphan 的 TaskTree-first 对账矩阵，并在 persisted schedule 恢复前运行；
- 隔离 subprocess checkpoint crash/resume 已通过：`os._exit(87)` 后同一 thread 恢复，HumanMessage=1，side effect=1，finalize=1；
- 隔离模拟 Provider 429 已通过：`os._exit(88)` 后 holding metadata 恢复，成功 callable 只执行一次；
- P0 Gate 重跑结果为 `standard_v2_p0=passed`，Recovery Gate 为 `standard_v2_recovery=passed`，两者均保持 `formal_24h_launch_allowed=false`；
- memory-enabled 隔离真实服务 health、在线备份、直接 integrity check 和 clean shutdown 已通过；
- sqlite-vec `v0.1.9` 已在隔离 Runtime SQLite 实际加载；v1→v2 shadow reindex、原子切换、失败保持旧 active、结构化降级和 outbox 不消费均通过；
- embedding model、dimensions、text fields 和 Provider endpoint fingerprint 已纳入 index identity，同版本漂移 fail closed；
- 测试误触正式 Runtime SQLite 的根因已修复：相对数据库路径跟随 `OMC_DATA_ROOT`、unit lifespan 使用 `tmp_path`、pytest 直接访问正式库 fail closed；
- 测试员工目录隔离已补齐：`config/store/memory_service` 的 active 与 ex-employee 路径均重定向到每测试 `tmp_path`，解雇流程测试不再写入正式历史员工目录；
- 最终全量测试达到 `4708 passed, 5 skipped, 72 warnings`；本轮 Gate 前后正式 Runtime SQLite、active `00010`、当前 archived `00010` 和当前磁盘 `iter_009` 的 SHA-256 均不变；
- recovery tests 全部使用临时 `OMC_DATA_ROOT`；当前正式服务并发写入单独记为 live-service activity，不做危险回滚；
- RuntimeStorage 只读对账已完成：7 条 finding 均为旧 `_sys_automation_*` adhoc thread 假 orphan，正式 actionable finding 为 0；Memory Outbox 当前 26 条均为 `pending/attempt=0`，未删除、未消费、未重放；
- health/API/CLI 已区分 actionable checkpoint finding 与 legacy system orphan，future reconciler 同时排除新旧 system/adhoc thread；
- legacy `iterations/iter_009.yaml` 哈希为 `4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626`，目录化 `iterations/iter_009/task_tree.yaml` 哈希为 `b3b877e6b584feefe084a40f50a75b7161ae018b42910f9c2e54780e46d087ab`；两个不同文件在本轮 Gate 前后均未变化。

#### 2.3.2 尚未完成的上线阻塞项

- 隔离维护窗口中的全新 standard v2 三阶段服务进程退出/恢复已通过；未停止正式业务实例；
- dispatch、executor started、业务 side-effect 三个阶段的 receipt/ledger 故障注入已通过；
- legacy `iter_009.yaml` 与目录化 `iter_009/task_tree.yaml` 已分别建立哈希基线，本轮前后同时保持不变；
- 本地 Ollama Embedding、worker pending/backoff/recovery 和 Provider 让位 Gate 均已通过；
- Online Backup 到独立 data root 的 TaskTree/checkpoint/receipt/ledger/outbox/acceptance 只读对账已通过；
- 仍缺少 24 小时墙钟、真机 smoke、FFmpeg/FFprobe 证据和最终四人 standard v2 正式复验；
- 全新 standard v2 iteration 的四人正式复验与显式验收。

**当前结论：** P0 和隔离 Recovery Gate 已通过，但 `formal_24h_launch_allowed=false`。不得把隔离 subprocess、隔离服务或单元测试结果写成正式业务上线。

## 3. 目标架构和权威边界

### 3.1 四层状态模型

```text
TaskTree YAML
  └─ 正式 node、父子关系、状态、派发、验收、Closure Gate

LangGraph Checkpoint
  └─ 消息、工具调用、graph step、可恢复执行位置

Execution Checkpoint / RuntimeStorage
  └─ generation、lease、fencing、dispatch intent、retry、side-effect ledger

Long-term Memory Store
  └─ employee/project/company 的经验、事实和程序性知识
```

权威性从高到低：

```text
TaskTree + receipt + acceptance audit
> Runtime execution records
> LangGraph checkpoint
> long-term memory
> progress.log / conversation history
```

### 3.2 不可破坏的系统不变量

长期记忆和 checkpoint 均不得直接证明：

- `dispatch_child()` 已成功；
- scheduler 已注册 child；
- executor 已持租约启动；
- COO/Review 已显式验收；
- Parent、Dispatch 或 Closure Gate 已通过；
- 正式节点已经 finished。

这些事实必须来自 TaskTree、dispatch receipt、started receipt、tool ledger 和 acceptance audit。

### 3.3 SQLite 文件布局

当前正式运行布局保持单库，避免 checkpoint、memory 和运行协调记录产生跨文件一致性问题：

```text
.onemancompany/data/
├── runtime.sqlite3              # OMC 表 + AsyncSqliteSaver + AsyncSqliteStore/sqlite-vec
├── runtime.sqlite3-wal          # SQLite WAL（运行时产生）
├── runtime.sqlite3-shm          # SQLite shared-memory 文件（运行时产生）
└── backups/                     # 受控在线备份和恢复演练产物
```

当前实现使用同一个数据库文件上的三个受控连接，分别提供 OMC runtime、checkpoint 和 memory Store 访问；这不是三套独立数据库。备份必须使用 SQLite backup API，不能在线状态下直接复制活动数据库文件。

未来如需拆分为 `runtime.sqlite3`、`checkpoints.sqlite3` 和 `memory-vN.sqlite3`，必须先提交 ADR，并实现：

- 全局 snapshot barrier 或 quiesce；
- 同一 `backup_set_id` / `snapshot_id`；
- 各文件 schema/version/hash 校验；
- 缺一不可的整组恢复；
- isolated dry-run、reconciler 和原子切换；
- 失败时整组回滚，禁止部分恢复。

在上述条件完成前，禁止启用多文件布局。

## 4. 统一运行规则

### 4.1 standard v2 与 legacy 模式

- standard v2：RuntimeStorage 和 checkpointer 是强依赖；不可用时进入 `holding`。
- simple/legacy v1：允许保持原有兼容路径，但 API/UI 必须显示 `memory_mode=legacy_degraded`。
- 不允许 standard v2 因存储故障自动降级为无 checkpoint 执行。

### 4.2 存储故障策略

RuntimeStorage、checkpoint 文件或必要目录不可用时：

- 停止调度新的 standard v2 执行；
- 节点进入 `holding`；
- `hold_reason=runtime_storage_unavailable` 或 `checkpoint_store_unavailable`；
- 已在 Provider 调用中的执行完成当前安全边界后停止推进；
- 周期性健康检查恢复后由 reconciler 唤醒；
- 不自动标记 failed，不静默创建新 generation。

当前代码中“RuntimeStorage 不可用即把 standard v2 标记 blocked”的路径需要改为上述 holding 语义；只有配置不可恢复、文件权限错误需人工修正或数据损坏确认后才进入 blocked。

### 4.3 Provider 故障策略

- 并发、429、临时网络和短时 5xx：`holding` + durable backoff；
- 认证错误、模型不存在、明确额度耗尽、非法配置：`blocked`；
- Provider 瞬态错误不消耗夜间“两次业务失败”额度；
- ProviderGateway 的持久化队列只保存请求元数据，进程重启后不能重放 Python callable；
- 重启恢复必须由 TaskTree + LangGraph checkpoint 重新驱动，同一 thread 和幂等工具防止重复副作用。

### 4.4 白天与夜间策略

时区固定为 `Asia/Shanghai`：

- 白天：09:00-21:00；允许中高风险开发、迁移和人工协作；
- 夜间：21:00-09:00；优先测试、文档、低风险重构、报告和独立任务；
- 高风险或破坏性改动夜间进入 holding，等待白天或人工批准；
- 不得通过跳过 Review、降低 Gate 或自动接受提高夜间吞吐。

调度和审计中记录：

```text
time_window
auto_dispatch_policy
risk_class
destructive_change
approval_required
```

---

## 5. Checkpoint 与恢复设计

### 5.1 TaskNode 持久化字段

standard v2 节点至少包含：

```text
workflow_contract_version
execution_generation
checkpoint_thread_id
checkpoint_status
last_checkpoint_at
execution_checkpoint
```

其中：

- `execution_generation`：明确重启一次新执行时递增；
- `checkpoint_thread_id`：正式节点固定 thread；
- `checkpoint_status`：`new | active | waiting | holding | terminal | missing | orphaned | conflict`；
- `last_checkpoint_at`：最近成功持久化时间；
- `execution_checkpoint`：业务阶段、已确认副作用、next retry 和待处理步骤摘要，不复制完整消息历史。

### 5.2 Thread ID 规则

正式节点：

```text
omc:{project_id}:{iteration_id}:{node_id}:g{execution_generation}
```

会话：

```text
conversation:{employee_id}:{conversation_id}
```

routine/adhoc/system 任务使用独立 thread，不与正式节点共享。

禁止使用 Employee ID、asyncio task ID、随机会话 ID 代替正式 node thread。

### 5.3 首次执行

1. 加载 TaskTree node；
2. 确认 standard v2 存储健康；
3. 生成或确认 generation/thread ID；
4. 获取执行租约和 fencing token；
5. 在第一次副作用工具前创建首个 checkpoint；
6. 写入原始 HumanMessage；
7. 执行有限 graph step；
8. 更新 checkpoint 和 TaskTree execution checkpoint。

### 5.4 恢复执行

1. TaskTree 先决定节点是否允许继续；
2. 查询同一 thread 的最新 checkpoint；
3. 恢复时不重复追加原始任务描述；
4. 不重复执行 checkpoint 已确认完成的 graph step；
5. 所有副作用工具仍通过业务幂等键和 tool ledger 校验；
6. 获取新 lease/fencing token 后才允许写正式状态；
7. 恢复审计记录旧 lease、新 lease、generation 和恢复原因。

明确“重新开始”时：

- `execution_generation += 1`；
- 创建新 thread；
- 旧 checkpoint 保留为审计记录；
- recovery audit 记录新旧 thread 关系；
- 不继承旧 thread 中未验证的“已完成”推断。

### 5.5 Checkpoint/TaskTree 对账矩阵

| TaskTree | Checkpoint | 处理 |
|---|---|---|
| processing | active | 获取 lease 后正常恢复 |
| holding | waiting/active | 等待事件或 `next_retry_at` |
| finished | active | 停止执行，标记状态冲突并审计 |
| processing | 不存在 | 转 holding，禁止从头重跑 |
| pending | 存在未开始 checkpoint | 对账后决定恢复或隔离 |
| node 不存在 | checkpoint 存在 | 标记 orphan，不执行 |
| generation 不一致 | 任意 | 隔离旧 generation，禁止写当前 node |

### 5.6 Checkpoint 清理

默认策略：

- active、holding、awaiting review：不清理；
- terminal generation：保留至少 30 天；
- 有恢复争议、验收争议或 memory evidence 引用：延长保留；
- 清理前生成 manifest 和 audit；
- 清理只删除 LangGraph checkpoint，不删除 TaskTree、receipt、acceptance audit 或 memory source reference；
- 清理命令支持 dry-run。

---

## 6. 派发、验收和副作用幂等

### 6.1 派发状态机

幂等键：

```text
parent_id + employee_id + task_key
```

请求内容生成稳定 fingerprint。状态按以下顺序推进：

```text
prepared
→ tree_written
→ index_written
→ scheduled
→ started
```

规则：

- 相同 key、相同 fingerprint：返回原 node/receipt；
- 相同 key、不同 fingerprint：返回冲突，不覆盖；
- 中间崩溃：reconciler 从最后一个 durable 状态补齐；
- `started` 只能由持有效 lease/fencing token 的 executor 写入；
- Agent checkpoint 恢复不能重新创建 child。

### 6.2 显式验收

standard v2 只能通过以下工具作出决定：

```text
accept_child()
reject_child()
```

并在一次 TaskTree 原子保存中写入：

```text
acceptance_audit.decided_via
acceptance_audit.decided_by
acceptance_audit.decided_at
acceptance_audit.evidence_refs
```

`Auto-accepted`、自然语言“通过”、长期记忆中的成功结论、progress.log 或模型总结均不能通过 Closure Gate。

### 6.3 工具副作用账本

所有外部副作用工具必须记录：

```text
node_id
execution_generation
tool_name
tool_call_id
business_idempotency_key
request_fingerprint
status
result_reference
fencing_token
```

重点覆盖：

- 创建/派发任务；
- 修改正式文件；
- 启动 executor 或设备作业；
- 部署、发消息、创建报告；
- accept/reject；
- 管理 memory 状态。

---

## 7. 长期记忆模型

### 7.1 命名空间

```python
("employee", employee_id, memory_type)
("project", project_id, memory_type)
("company", memory_type)
```

类型：

```text
semantic   稳定事实、项目约束和架构决策
episodic   过去任务、错误、处理过程和结果
procedural 工作方法、检查表、SOP 和恢复 runbook
```

### 7.2 访问控制

- 员工只能读取和写入自己的 employee memory；
- 仅 TaskTree/项目成员表确认的正式成员可读项目 memory；
- 普通员工只能提交 company candidate；
- COO/管理员可审批 company memory；
- 离职员工停止新增私有记忆，但历史和审计保留；
- 不同项目默认隔离；
- Agent 不能通过工具参数指定其他员工私有 namespace。

权限判断必须由服务端根据当前 runtime context 完成，不能信任模型输入。

### 7.3 Memory Record

每条记忆至少保存：

```json
{
  "memory_id": "uuid",
  "scope": "employee | project | company",
  "namespace_id": "employee_id | project_id | company",
  "memory_type": "semantic | episodic | procedural",
  "subject": "stable_subject_key",
  "text": "...",
  "structured_value": {},
  "status": "candidate | verified | disputed | superseded | rejected",
  "created_by": "employee_id | system",
  "source_project_id": "...",
  "source_iteration_id": "...",
  "source_node_id": "...",
  "source_thread_id": "...",
  "evidence_refs": [],
  "confidence": 1.0,
  "valid_from": "...",
  "expires_at": null,
  "supersedes": null,
  "embedding_status": "pending | indexed | failed | not_required",
  "embedding_model": "...",
  "embedding_dimensions": 0,
  "embedding_index_version": "v1",
  "content_hash": "...",
  "created_at": "...",
  "verified_at": null
}
```

### 7.4 分级写入

#### Employee episodic

正式节点进入终态后可自动生成：

- 做了什么；
- 遇到什么错误；
- 哪种方法有效或无效；
- 最终 TaskTree 状态；
- node、thread 和 evidence 引用。

自动写入只代表“发生过这段经历”，不代表业务验收通过。

#### Project semantic

仅当来源满足以下条件时可自动 verified：

- 项目 metadata；
- 正式工具回执；
- 真实文件、命令或设备结果；
- 来源 node、时间、evidence 完整；
- 与当前 verified fact 不冲突。

模型自由总结且无证据时只能是 candidate。

#### Company procedural

派发规范、真机 smoke SOP、统一路径和恢复 runbook 必须由 COO/管理员显式批准后才能 verified。

### 7.5 冲突和过期

不得覆盖旧记录。发现同 subject 的冲突时：

```text
旧 verified → disputed
新记录      → candidate
创建 memory_conflict
```

审批后：

```text
旧记录 → superseded
新记录 → verified
新记录.supersedes = 旧 memory_id
```

默认有效期：

- 设备在线状态：30 分钟；
- 当前进程、端口和 API 状态：5 分钟；
- 项目路径和架构决策：无自动过期，直到 superseded；
- 历史任务结果：永久保留来源，但不自动视为当前事实。

---

## 8. 结构化与向量混合检索

### 8.1 Embedding 配置

新增：

```text
OMC_MEMORY_ENABLED=true
OMC_MEMORY_DATABASE_PATH=.onemancompany/data/runtime.sqlite3
OMC_MEMORY_EMBEDDING_BASE_URL=...
OMC_MEMORY_EMBEDDING_API_KEY=...
OMC_MEMORY_EMBEDDING_MODEL=...
OMC_MEMORY_EMBEDDING_DIMENSIONS=...
OMC_MEMORY_INDEX_VERSION=v1
```

规则：

- embedding 配置不默认复用聊天模型名；
- 当前默认 Provider 为仅监听 `127.0.0.1:11434` 的本地 Ollama `embeddinggemma`；
- `OpenAIEmbeddings` 必须保持字符串输入兼容模式，禁止向 Ollama 发送 token-id 数组；
- Ollama 不可用时降级为结构化检索，禁止自动切换未知模型；
- API key 不写入 memory、checkpoint、audit 或错误正文；
- 启动探针验证模型、维度和 sqlite-vec 可加载；
- 维度与 active index 不一致时禁止向旧 index 写入；
- reindex 在同一 Runtime SQLite 的 `memory_vector_versions` 中创建目标版本 shadow vectors；
- 后台重建完成且验证通过后，在单事务中归档旧向量、切换 `store_vectors` 和 active index contract；
- 不在同一个向量空间混用模型或维度。

### 8.2 为什么不用独立 FAISS

`AsyncSqliteStore` 已提供结构化 Store 和可选向量搜索。独立 FAISS 会增加：

- Store 与索引双写一致性问题；
- 备份时点不一致；
- 删除、supersede 和 ACL 过滤的同步负担；
- 重启后索引重建和损坏恢复路径；
- 第四套持久化格式。

因此当前实现统一使用 `AsyncSqliteStore + sqlite-vec`。只有官方 Store 的性能压测不达标时才单独提出新的 ADR，不在本计划中预埋 FAISS 双写。

### 8.3 检索流程

每次正式 Agent 执行前：

1. 从 TaskTree/runtime context 获取 employee、project、iteration、node、phase；
2. 计算允许访问的 namespace；
3. 先按 scope、namespace、status、有效期和 index version 过滤；
4. 执行 subject/关键词结构化检索；
5. embedding 可用时执行 sqlite-vec 相似度检索；
6. 合并、去重和重排；
7. 注入 Prompt。

排序优先级：

```text
正式来源可信度
> 当前项目匹配
> 有效期
> 语义相关度
> 员工历史经验
```

默认注入限制：

- 最多 8 条；
- 总计不超过 6,000 字符；
- 不超过模型输入预算的 20%；
- 每条包含 `memory_id/scope/status/source_node_id/verified_at/expires_at`；
- candidate、disputed、superseded 默认不进入普通执行 Prompt。

### 8.4 Embedding 降级

embedding 服务不可用时：

- 业务任务继续；
- 结构化记忆先落盘；
- `embedding_status=pending`；
- 检索降级为 namespace + subject + 关键词 + 时间排序；
- worker 使用 durable backoff 后补向量；
- embedding 失败不改变正式 TaskTree 状态。

---

### 8.5 SQLite 长期记忆上线 Gate

SQLite 方案只有在以下检查全部通过后，才可作为 standard v2 的正式长期记忆后端：

1. `AsyncSqliteSaver`、`AsyncSqliteStore` 和 `sqlite_vec` import contract 通过；
2. Store/Saver `setup()` 多次执行幂等，生命周期关闭无连接泄漏；
3. 配置的 embedding 维度与 active index 一致；
4. embedding 不可用时结构化记忆仍可写入、可过滤检索，业务节点不失败；
5. SQLite lock、只读、连接断开和磁盘不足均进入可恢复 `holding`；
6. WAL、header、`quick_check`/`integrity_check`、page count 和 hash 可验证；
7. 在线备份可以在隔离目录恢复，并能对账 TaskTree、checkpoint、ledger、provider queue 和 memory outbox；
8. 单实例调度保护已启用，禁止两个运行时同时消费正式队列；
9. 24 小时墙钟演练中没有无界 WAL、连接、协程或 memory backlog 泄漏。

“SQLite 组件已初始化”不等于“长期记忆功能已完成”；`search_memory`、`propose_memory`、ACL、Memory Outbox worker、embedding worker 和恢复 reconciler 仍必须分别验收。

## 9. Memory Outbox 和 Worker

### 9.1 Outbox 唯一性

业务终态只同步写 outbox，不同步等待 embedding。唯一键：

```text
source_node_id + memory_type + content_hash
```

事件至少保存：

```text
event_id
source_node_id
event_type
status
attempt
next_retry_at
created_at
```

### 9.2 Worker 流程

```text
claim outbox event
→ 校验 source node 和正式状态
→ 去重
→ 提取候选记忆
→ 敏感信息过滤
→ ACL/作用域校验
→ 写结构化 memory
→ 尝试 embedding/index
→ 写 audit
→ 标记完成
```

Worker 必须使用 claim/lease，防止进程重启或重复 worker 双写。

### 9.3 敏感信息过滤

采用“结构化 allowlist + 精确敏感键 + 文本模式过滤”的组合：

必须拦截：

- API key、token、password、secret；
- Authorization/header/cookie；
- 数据库连接串中的凭证；
- 私钥、OAuth refresh token；
- `.env` 原文。

不得误删正常业务字段，例如 `task_key`、`memory_key`、`idempotency_key`。过滤结果记录字段名和 redaction 数量，不记录被过滤的原值。

### 9.4 Provider 优先级

```text
正式任务 / Review
> 恢复任务
> 记忆提取
> embedding
> 全量 reindex
```

正式聊天任务存在排队时，memory worker 主动让出并发槽位。

---

## 10. 员工工作原则与团队配置落地

### 10.1 文档与运行配置的关系

建立两个层次：

- `docs/employee-work-principles/*.md`：版本化、可审阅的模板；
- `.onemancompany/.../employees/{id}/work_principles.md`：当前运行副本。

应用脚本必须：

1. 校验员工存在；
2. 显示 diff；
3. 备份旧文件；
4. 写入模板版本和内容 hash；
5. 不自动改写历史员工任务记录；
6. 支持 dry-run 和单员工应用。

### 10.2 角色配置前置门槛

正式 12 人团队启动前必须解决：

- 00006 至 00010 当前角色与团队文档的冲突；
- 00011、00012 是否正式招募并具备 profile/manifest/runtime；
- 目标模型是否真实可用，而不只是 YAML 中存在字符串；
- COO、Tech Lead、Backend、Full-stack、DevOps、QA 的权限是否与职责一致；
- 工作原则是否包含夜间策略、Review、evidence 和长期记忆边界。

在角色未对齐前，不得把 12 人团队清单标记为通过。

### 10.3 正式员工配置变更方案

本项不是单纯修改几行 YAML，而是一次受控的组织配置迁移。正式配置以 `.onemancompany/company/human_resource/employees/{employee_id}/` 中经 onboarding/config service 写入并可由 API 回读的状态为准；`docs/employee-work-principles/` 只是模板来源。

#### 10.3.1 目标配置矩阵

| 员工 | 目标角色 | 目标部门 | 目标模型 | 汇报/协作关系 | 操作 |
|---|---|---|---|---|---|
| 00003 | COO | Operations | `claude-opus-5` | 向 CEO 汇报，调度全员 | 修改模型并复核权限 |
| 00006 | Senior Backend Engineer / Alpha Lead | Engineering | `claude-opus-5` | 审查 00011，和 00007 联调 | 修改角色、名称/职级、技能、权限、模型 |
| 00007 | Full-Stack Engineer | Engineering | `claude-sonnet-5` | 与 00006 联调，接收 00009 缺陷 | 修改模型并清理旧 API Tester 身份残留 |
| 00008 | DevOps/SRE | Operations | `gpt-5.6-sol` | 负责部署、监控、备份和恢复 | 对齐部门/角色显示并复核高风险工具权限 |
| 00009 | QA Lead | Quality Assurance | `claude-sonnet-5` | 管理 00012，负责正式质量 Gate | 修改角色、部门、技能、权限和模型 |
| 00010 | Tech Lead | Engineering | `claude-fable-5` | 技术升级入口，不承担 Project Manager 身份 | 修改角色、名称/职级、技能、权限、模型 |
| 00011 | Mid Backend Engineer | Engineering | `gpt-5.6-sol` | 向 00006 汇报 | 正式 onboarding 新建 |
| 00012 | Automation Test Engineer | Quality Assurance | `gpt-5.6-sol` | 向 00009 汇报 | 正式 onboarding 新建 |

如果 Provider 的实际模型目录中不存在目标模型，配置迁移必须停止并记录 `configuration_blocked:model_unavailable`，不得把一个无法调用的字符串写入正式 profile，也不得静默替换成其他模型。任何模型降级都必须先修改团队配置文档并经 CEO/管理员批准。

#### 10.3.2 变更前预检

1. 对以下内容生成只读快照和 SHA-256 manifest：
   - 00003、00006—00010 的 `profile.yaml`、`manifest.json`、`work_principles.md`、`automations.yaml`；
   - active/ex-employees/quarantine 中的 00011、00012 ID 占用情况；
   - 当前员工 API 返回、模型目录和权限目录；
   - 受影响员工的 processing/holding task 清单。
2. 暂停向受影响员工派发新任务；已有 processing 节点必须完成当前安全步骤后进入 holding，或明确结束当前 execution generation。
3. 验证目标模型可用、凭证池可用、ProviderGateway 可路由，但不得为了探针绕过并发闸门。
4. 验证目标角色需要的工具权限存在，采用最小权限白名单；DevOps、Tech Lead 和测试岗位不得因为角色名称变化自动获得数据库秘密或不受控 shell 权限。
5. 运行 `--dry-run`，输出字段级 diff；禁止修改 employee number、历史 task、progress log、task history、历史 acceptance audit 和 `iter_009`。

#### 10.3.3 00006—00010 与 00003 原子对齐

1. 通过受控 config service/CLI 写入，不以多次手工编辑作为正式流程；
2. 每名员工先写临时 profile，执行 schema、模型、权限和引用校验后再原子替换；
3. 同步更新 `name`、`role`、`department`、`level/title`、`skills`、`permissions`、`tool_permissions` 和 `llm_model` 中确实属于目标角色的字段；
4. 清理旧身份语义，例如 00006 的 SRE、00007/00009 的 API Tester、00010 的 Project Manager，但保留历史审计文本，不重写旧任务记录；
5. 写入 `configuration_revision`、`configuration_updated_at`、`configuration_updated_by` 和变更审计；若现有 profile schema 尚无这些字段，先增加兼容 schema 和读写测试；
6. `_refresh_agent()` 只刷新模型、工具和 prompt/原则版本，不重建 RuntimeStorage、Saver、Store 或连接；
7. 正在运行的旧 generation 不允许在中间步骤静默切换模型/工具。需要切换时先 holding，再创建有审计关联的新 generation；
8. 每名员工变更后通过文件、员工 API、Agent Factory 和一次无副作用模型探针四方回读一致性。

#### 10.3.4 正式创建 00011、00012

1. 通过正式 onboarding/hire 流程创建，不手工复制其他员工目录；
2. onboarding 前检查 active、ex-employees、quarantine、task index 和 conversation namespace 中不存在 ID 冲突；
3. 创建标准目录和必要文件，包括 profile、manifest、role guide、work principles、automation、task index/history、runtime metadata；
4. 00011 只获得中级后端所需权限，并建立 supervisor `00006`；00012 只获得测试执行、报告和受控设备访问权限，并建立 supervisor `00009`；
5. 两人创建后默认 `idle` 且 automation disabled，直到原则、权限、模型探针和 supervisor 引用全部通过；
6. onboarding event、配置 manifest 和 API 回读全部写审计；
7. 不导入或伪造历史任务、绩效、记忆和 acceptance 记录。

#### 10.3.5 员工配置验收与回滚

验收必须同时满足：

- 12 个正式 employee ID 恰好存在且无重复；
- profile schema、角色、部门、模型、supervisor 和权限矩阵通过机器校验；
- 目标模型经 ProviderGateway 探针成功；
- 00006—00010 的旧角色不再出现在当前 profile 和运行原则中；
- 00011/00012 可由 API 读取、可创建独立 conversation/thread，但尚未被自动派发高风险任务；
- 重启后配置保持一致，Agent Factory 未重建数据库资源；
- 回滚演练可恢复变更前 profile/原则/automation，且不触碰历史任务。

任何一项失败时，停止 automation 导入和 24 小时模式启动，恢复配置快照并记录 rollback audit。

### 10.4 工作原则原子应用

将 `scripts/apply-work-principles.sh` 升级为受控配置工具，而不是顺序 `cp`：

1. 支持 `--dry-run`、`--employee ID`、`--all`、`--verify-only` 和 `--rollback MANIFEST`；
2. 在任何写入前确认所有目标员工、模板、目标路径、权限和磁盘空间均有效；
3. 为每个模板计算版本和 SHA-256，将旧运行副本统一备份到同一 transaction manifest；
4. 先写临时文件并 fsync，再原子 rename；任一员工失败时整批回滚；
5. 在运行副本头部或 sidecar metadata 中记录 template version/hash/applied_at/applied_by；
6. 应用后逐文件 hash 回读，并让 Agent Factory 刷新 prompt template version；
7. 00011/00012 不存在时 `--all` 必须在预检阶段失败，不允许先修改 00002—00010；
8. 不修改历史 progress、task history、checkpoint 或 memory。

退出条件：故障注入在第 N 个员工写入失败时，所有员工均保持变更前版本；成功时 11 个运行副本与模板 hash 一致并有单一 audit transaction。

### 10.5 记忆职责分工

- COO：提出/审批公司 procedural memory，不能用记忆代替验收；
- Tech Lead：审核架构类 project semantic candidate；
- Backend：实现 memory service、ACL、outbox 和 API；
- DevOps：备份、恢复、容量、文件权限和健康检查；
- QA Lead：权限隔离、恢复、冲突和故障注入验收；
- Automation QA：夜间回归、reindex 和 backup restore 自动化；
- EA：汇总待审批、冲突和 backlog，不直接修改 verified memory。

---

## 11. 自动化、API、CLI 和前端

### 11.1 自动化权威来源

代码中的可审计 system task registry 是执行权威；`docs/automation/cron-tasks.yaml` 是可读配置和文档入口，不能绕过注册和幂等检查直接执行任意命令。

计划任务：

- COO 每 2 小时调度扫描；
- COO 每 1 小时阻塞检查；
- 08:30 夜间报告；
- 21:00 日间报告和夜间计划；
- 00:00-06:00 夜间测试窗口；
- 02:00 SQLite 在线备份；
- 每小时日志维护；
- 每 4 小时健康检查；
- 每 30 秒检查 storage/provider holding 是否可恢复；
- 低优先级 memory outbox 和 reindex worker。

所有自动化任务必须有稳定业务 key；重复触发返回相同结果或安全更新，不得重复派发。

### 11.2 健康接口

`GET /api/health` 和 `/api/runtime/health` 至少返回：

```json
{
  "runtime_storage": "healthy | unavailable | degraded",
  "checkpoint_store": "healthy | unavailable",
  "memory_store": "healthy | unavailable | disabled",
  "sqlite_vec": "healthy | unavailable | disabled",
  "embedding": "healthy | degraded | disabled",
  "provider_gateway": "healthy | degraded",
  "memory_worker_backlog": 0,
  "oldest_memory_event_at": null,
  "checkpoint_conflicts": 0
}
```

不得返回数据库内容、API key、token、原始记忆或完整错误堆栈。

### 11.3 管理 API

仅 loopback + 管理 token：

```text
GET  /api/admin/memories
GET  /api/admin/memories/{memory_id}
POST /api/admin/memories/{memory_id}/approve
POST /api/admin/memories/{memory_id}/reject
POST /api/admin/memories/{memory_id}/supersede
POST /api/admin/memory/reindex
POST /api/admin/checkpoints/prune
POST /api/admin/recovery/reconcile
POST /api/admin/runtime/backup
GET  /api/health
POST /api/admin/runtime/backup
POST /api/admin/restores/validate
```

审批和恢复操作全部写 append-only audit。

### 11.4 CLI

```bash
onemancompany-admin runtime status
onemancompany-admin memory status
onemancompany-admin memory list --status candidate
onemancompany-admin memory approve MEMORY_ID
onemancompany-admin memory reject MEMORY_ID --reason "..."
onemancompany-admin memory reindex --from v1 --to v2
onemancompany-admin checkpoint prune --older-than 30d --dry-run
onemancompany-admin recovery reconcile --dry-run
onemancompany-admin backup create
onemancompany-admin backup verify BACKUP_MANIFEST
```

CLI 只调用管理 API，不直接改 SQLite 表。

### 11.5 前端状态

活动面板新增：

```text
正在恢复上下文
等待运行存储
等待模型容量
记忆提取积压
存在待审批记忆
存在记忆冲突
存在 checkpoint 对账冲突
```

员工显示 idle 时仍可通过 attention badge 显示 holding、待验收、待人工恢复或待冲突处理。

---

## 12. 分阶段实施顺序

### 12.1 八项上线阻塞工作包

以下八项全部属于正式 24 小时上线阻塞项。它们可以在写集合互不冲突时并行开发，但必须按依赖顺序验收，不得因为文档或脚本文件已经存在而标记完成。

#### WP-01：P0 正式 Gate

实施：

1. 定义并实现 Parent Gate、Dispatch Gate、Closure Gate 的机器可判定规则；
2. ProviderGateway 覆盖所有真实模型入口，Provider 瞬态错误进入 durable holding；
3. RuntimeStorage/checkpointer 不可用时 standard v2 禁止无状态执行；
4. dispatch intent 从 `prepared → tree_written → index_written → scheduled → started` 全状态可恢复；
5. started receipt 必须绑定 execution lease/fencing token；
6. standard v2 只允许显式 `accept_child()`/`reject_child()`，Auto-accepted 不通过 Closure Gate；
7. checkpoint 恢复依赖 side-effect ledger，不重放已完成工具；
8. 新增 `scripts/verify-p0-gates.sh` 或等效管理命令，运行目标 pytest、持久化对账和 crash/restart 测试，输出 JSON gate report；
9. `check-system-ready.sh` 消费 gate report/测试结果，不再通过 grep 类名判断完成。

验收证据：三 Gate 测试报告、dispatch/started/acceptance durable records、Provider holding 恢复记录、checkpoint 不重放报告。任何一项失败均阻塞后续正式 iteration。

#### WP-02：修改正式员工运行配置并完成 00003、00006—00010 正式对齐

本工作包必须实际修改正式员工运行配置，而不是只修改计划、文档或模板。按照 10.3.2—10.3.3 执行快照、停止新派发、模型探针、字段级 dry-run、原子写入、Agent refresh、API 回读和回滚演练：

1. 通过受控 config service/CLI 修改正式 profile、manifest、权限配置和模型配置；
2. 对 00003、00006、00007、00008、00009、00010 的角色、部门、职级、技能、工具权限、`llm_model` 和协作关系逐字段对齐；
3. 原子替换成功后，从文件、员工 API、Agent Factory 和无副作用模型探针四个入口回读；
4. 写入 configuration revision、操作者、时间、前后 hash 和审计记录；
5. 不得只修改文档或工作原则而不修改正式 profile，也不得在旧 generation 中静默换模型；
6. 任一正式配置写入或回读失败，整批变更标记为 blocked 并按 manifest 回滚。

验收证据：员工配置 transaction manifest、变更前后 diff、目标模型探针、权限矩阵、API 回读和重启一致性报告。

#### WP-03：正式创建 00011、00012

按照 10.3.4 走正式 onboarding；建立 supervisor、最小权限、独立 thread/namespace 和 disabled automation。创建完成不等于可以立即执行夜间任务，必须先通过配置与原则验收。

验收证据：onboarding audit、无 ID 冲突报告、目录/schema 检查、员工 API 回读、Provider 探针和 supervisor 引用测试。

#### WP-04：工作原则原子应用

按照 10.4 实现全量预检、版本/hash、transaction manifest、临时文件、原子替换、失败回滚和 Agent prompt refresh。先完成 00011/00012，再允许 `--all` 正式应用。

验收证据：11 个模板与运行副本 hash 对账、故障注入回滚测试、单一 apply audit transaction。

#### WP-05：Automation 注册

实施：

1. 为 `docs/automation/cron-tasks.yaml` 建立 schema validator，校验 cron、任务 ID、employee ID、task key template、优先级和 allowlisted action；
2. 提供 `--dry-run` 导入器，生成每员工目标 diff，不直接执行 prompt 中的任意 shell；
3. 在单一受控事务中写入员工 `automations.yaml` 或等价持久化配置，并为每条任务保存 `manifest_hash`、`registration_id`、`registered_at`、`status`；
4. 通过 system task registry 注册 13 个幂等任务，每次触发生成稳定业务 key；
5. 正式任务/Review 优先于恢复、报告、记忆和 reindex；Provider 容量不足时 automation durable pending/holding；
6. 00011/00012 未就绪、权限不满足或 supervisor 缺失时相关任务保持 disabled，不允许丢弃或派给其他员工；
7. 重启调用 restore/reconcile，重复导入相同 manifest 不重复注册；内容变化必须产生新 revision 和审计；
8. 提供 list/status/disable/reconcile 管理 API/CLI，并在前端显示 backlog/disabled/conflict。

验收证据：13 条 registration receipt、员工 automation 回读、重启恢复、重复导入无重复派发、缺员 holding/disabled 和 Provider backoff 测试。

#### WP-06：一致性在线备份

实施：

1. 使用 loopback + 管理 token 的 `POST /api/admin/runtime/backup`，由后端调用 `RuntimeStorage.backup()`，只返回 JSON manifest，不把 HTTP body 伪装成 SQLite 文件；
2. manifest 至少包含 backup ID、绝对路径、时间、应用/schema 版本、文件大小、SHA-256、SQLite page count、TaskTree snapshot 引用和创建审计；
3. 在线备份使用 SQLite backup API；服务运行时 API 失败不得 fallback 为直接 `cp` 活动数据库；
4. 备份后校验 SQLite header、`PRAGMA quick_check/integrity_check` 和关键表抽样；
5. TaskTree、员工配置、项目配置和 runtime SQLite 使用同一 backup set ID，记录各自采集时点和一致性边界；
6. `.env`、API key、token 和数据库秘密不得进入归档；只保存 `.env.example` 或脱敏字段清单；
7. 保留策略覆盖 `*.sqlite3`，实现每日 7、每周 4、每月 6 份，并以 verified manifest 决定可删除项；
8. `backup-all.sh` 使用 `curl --fail --show-error` 调用管理 API并验证返回 JSON，不自行复制在线数据库。

验收证据：带 hash 的 verified manifest、持续写入期间的完整性测试、secret scan、保留策略测试和失败不产出“成功备份”的测试。

#### WP-07：独立恢复演练

实施：

1. 恢复前验证 manifest、hash、schema、SQLite header 和 integrity；
2. 永远先恢复到独立临时目录，不直接覆盖活动 `.onemancompany`；
3. 使用独立 data root 和测试端口启动恢复实例，禁止连接正式 scheduler/Provider 副作用入口；
4. 对账 TaskTree node 数量/状态、checkpoint thread/generation、dispatch/started receipt、acceptance audit、outbox、memory source refs 和员工配置 hash；
5. 运行 health/read-only smoke、checkpoint resume dry-run 和 automation registration dry-run；
6. 记录 RPO、RTO、缺失/冲突项；达到标准后才允许维护窗口内停止正式服务并原子切换；
7. 用户拒绝停止服务或停止确认失败时必须立即退出，禁止继续复制；
8. 切换失败自动恢复 pre-restore safety snapshot，并写 restore/rollback audit。

验收证据：独立恢复报告、对账清单、RPO/RTO、切换与回滚演练；至少一次从备份启动的测试实例通过全部只读验证。

#### WP-08：真实服务 Verify

实施：

1. 为 `verify-24h-mode.sh` 增加 `preflight`、`runtime`、`post-restore` 和 `--json-report` 模式；
2. 先做 API schema contract，确认 health/employees/state/automation/backup 字段，不使用猜测字段；
3. 在隔离测试服务上验证 12 人配置、模型/权限、P0 gate report、automation receipts、holding/backoff、checkpoint 恢复和 backup 状态；
4. 验证 macOS/Linux 日期、CPU、磁盘命令和缺少 `jq`/`bc` 时的明确行为；
5. blocking failure 返回非零；warning 必须有代码、解释和是否阻塞，不能把关键失败降为 warning；
6. 执行一次受控任务、一次 Provider holding/resume、一次服务重启恢复、一次 automation 触发和一次在线备份；
7. 报告保存到 `reports/verification/{timestamp}/`，包含版本、配置 revision、测试证据和失败链接；
8. 通过短时集成验证后仍需执行 Phase 8 的完整 24 小时墙钟演练。

验收证据：真实服务 JSON/Markdown verify report，所有 blocking checks 为通过，且报告可追溯到 durable state，而不是仅检查进程或日志字符串。

### 12.2 执行依赖

```text
Phase 0 基线/安全备份
  → WP-01 P0 正式 Gate
  → WP-02 员工对齐 + WP-03 新员工创建
  → WP-04 工作原则原子应用
  → WP-05 Automation 注册
  → WP-06 一致性在线备份
  → WP-07 独立恢复演练
  → WP-08 真实服务 Verify
  → Phase 8 完整 24 小时演练
  → Phase 9 全新 standard v2 iteration
```

Runtime/Checkpoint 与长期记忆的 Phase 2—5 可以在不修改同一写集合时并行开发，但 WP-01、WP-06 和 WP-07 的验收不得后移到最终复验之后。

### Phase 0：冻结历史与建立基线

目标：在修改持久化和恢复路径前建立可回滚基线。

任务：

1. 备份 `.onemancompany`、项目 TaskTree 和现有 runtime SQLite；
2. 记录当前测试结果、schema、依赖版本和运行配置；
3. 为 `iter_009` 建立只读历史保护测试；
4. 建立 import contract test：`AsyncSqliteSaver`、`AsyncSqliteStore`、`sqlite_vec`；
5. 明确单实例后端约束，禁止多 worker 启动正式调度器。

退出条件：可从备份恢复当前版本，历史 hash 未改变。

### Phase 1：P0 workflow contract、Provider 和真实闭环

目标：先保证任务不会重复派发、假启动或假验收。

任务：

1. 完成 Parent/Dispatch/Closure 三 Gate；
2. ProviderGateway 包裹全部真实聊天模型调用入口；
3. 瞬态故障统一 holding，配置故障 blocked；
4. 完成 dispatch intent reconciler；
5. 完成 started receipt + lease/fencing 校验；
6. standard v2 移除所有 Auto-accepted 路径；
7. 两次 Review omission 只创建一个人工升级节点。

退出条件：响应丢失、进程崩溃和重复请求均不产生重复 child 或自动接受。

### Phase 2：RuntimeStorage 生命周期和 SQLite 单库底座

目标：形成可靠的单机持久化底座；本阶段不启用多文件拆分。

任务：

1. Settings 增加 runtime/memory 路径、memory 开关、embedding 和 index version 配置；
2. FastAPI lifespan 只初始化一次 Saver/Store/连接；
3. 保持当前单一 `runtime.sqlite3` 兼容布局，明确三个受控连接的生命周期；
4. 增加单实例锁、磁盘空间、文件权限和完整性检查；
5. 修正 storage 不可用时 standard v2 的 `holding` 语义；
6. shutdown 顺序先停新任务和 worker，再 flush/close storage；
7. schema migration 幂等，重复启动不重复破坏性初始化；
8. 如未来需要拆分 SQLite 文件，先提交 ADR、snapshot barrier 和整组恢复测试，不能作为本阶段隐式迁移。

退出条件：重复启动、正常关闭、强制退出、SQLite 短暂不可用和磁盘短暂不可用均有确定行为；单实例与 WAL/备份 Gate 通过。

### Phase 3：Checkpoint 完整恢复

目标：Agent 从中断 graph step 继续，而不是从 prompt 重新开始。

任务：

1. 补齐 `last_checkpoint_at` 和 `execution_checkpoint`；
2. 首次副作用前确认首个 checkpoint；
3. 恢复时不重复 HumanMessage；
4. 实现完整对账矩阵和 orphan quarantine；
5. 所有正式 Agent 调用传稳定 thread ID；
6. conversation/routine/adhoc 使用独立 thread；
7. 实现 generation restart 和 recovery audit；
8. 实现 checkpoint prune dry-run。

退出条件：工具调用后 kill 进程，重启后从同一 thread 继续，已完成副作用不重放。

### Phase 4：结构化长期记忆和 ACL

目标：先实现不依赖 embedding 的可信记忆。

任务：

1. 实现 Memory Record、namespace 和状态机；
2. 实现 employee/project/company ACL；
3. 实现 `search_memory()` 和 `propose_memory()`；
4. 实现敏感信息过滤；
5. 实现 evidence 校验和自动 verified 规则；
6. 实现 conflict/review/supersede audit；
7. 实现过期过滤和 prompt 注入预算；
8. 历史 progress/task history 仅允许导入 `legacy/unverified`。

退出条件：跨员工、跨项目和越权 company 写入测试全部被拒绝。

### Phase 5：Memory Outbox、Embedding 和 sqlite-vec

目标：embedding 故障不阻塞业务，向量索引可重建。

任务：

1. 终态事件写 durable outbox；
2. worker claim、去重、backoff 和恢复；
3. 接入独立 OpenAI-compatible embedding 配置；
4. 启动探针验证 model/dimensions；
5. 配置 `AsyncSqliteStore` vector index；
6. 实现结构化 + 向量混合检索；
7. embedding 不可用时 pending + 结构化降级；
8. 实现 versioned reindex 和原子切换。

当前状态：1—8 的代码和隔离 Gate 已完成；本地 Ollama endpoint/model/dimension、真实向量检索、Memory worker pending/holding/backoff/恢复补向量以及聊天 Agent Provider 让位均已通过。正式 26 条 outbox 仍不得消费。当前实现使用同一 Runtime SQLite 内的 `memory_vector_versions` shadow rows 原子切换，不创建独立 `memory-vN.sqlite3`。

退出条件：关闭 embedding 服务后正式任务仍完成；恢复后 backlog 自动补齐且无重复 memory。

### Phase 6：组织配置和自动化落地

目标：让文档中的 12 人团队和 24 小时节奏成为真实、可审计配置。

已完成的材料准备：

- 00002—00012 共 11 份工作原则模板已创建；
- cron manifest 已改成 schema v1 的单一有效 YAML，包含 13 个任务；
- readiness 脚本的计数器、Python 选择和员工 ID 已修复；
- verify 脚本已创建；
- 备份/恢复脚本已从旧数据库名切换到 `runtime.sqlite3`；
- 所有相关 shell 文件通过静态语法检查。

剩余实施任务（按顺序）：

1. 把 readiness 的 grep 探针改为真实 import contract、配置校验和目标测试集合，避免把符号存在误当成功能完成；
2. 对齐 00006—00010 的正式 `profile.yaml`、角色、部门、权限和模型；
3. 通过正式 onboarding 创建 00011、00012 的运行目录、profile、manifest、权限和模型配置；
4. 将工作原则应用脚本改为 dry-run → 全量预检 → 临时文件 → 原子替换，并保存 template version/hash 和 audit；
5. 实现 cron manifest validator/importer，通过受控服务生成员工 `automations.yaml`，注册幂等 system tasks，并保存 durable registration receipt；
6. 为 manifest 中涉及 00011/00012 的任务增加员工存在性和权限 Gate，缺员时保持 disabled/holding，不得静默丢弃；
7. 实现真实、受管理 token 和 loopback 限制的 RuntimeStorage 在线备份入口；备份脚本使用 `curl --fail`、SQLite header、`PRAGMA integrity_check` 和 manifest 验证，禁止在线失败后直接复制活动数据库；
8. 修复恢复脚本的停止服务控制流，增加 schema/version/integrity 校验、独立目录演练和恢复后 TaskTree/checkpoint/runtime 对账；
9. 从配置备份中排除 `.env` 和其他秘密，只备份 `.env.example`/字段清单；补齐 `*.sqlite3` 保留策略；
10. 在真实测试服务上验证 `verify-24h-mode.sh` 与 `monitor-24h-mode.sh` 的 API schema、跨平台命令、报告命名和退出码；
11. 注册并验证早晚报告、夜间测试、备份、日志清理和健康检查，确保每个任务具备稳定 task key、并发优先级、holding/backoff 和审计；
12. 更新 README、startup guide、verification checklist 和补充状态文档，删除“所有脚本已就绪”等超前结论。

退出条件：团队、模型、权限、原则和 automation 均由机器校验；13 个 manifest 任务具有注册回执；在线备份和独立恢复演练通过；readiness/verify 的成功退出码由正式 Gate 和持久化证据支持，而不是文本 grep。

### Phase 7：管理面、可观察性和备份恢复

目标：运维人员能发现、审批、恢复和审计。

任务：

1. 健康、活动、scheduler 兼容接口；
2. memory/recovery/checkpoint 管理 API；
3. admin CLI；
4. 前端 holding/attention/backlog/conflict 状态；
5. 每日 7 份、每周 4 份备份和 manifest；
6. 独立目录恢复演练；
7. 备份后执行 integrity check 和抽样查询；
8. 文档化 RPO/RTO 和人工 runbook。

退出条件：从备份恢复后 TaskTree、checkpoint、runtime records 和 memory source refs 可对账。

### Phase 8：故障注入和 24 小时墙钟演练

目标：验证系统在真实时间跨度内不会因重启、Provider 或 embedding 故障丢失闭环。

故障场景：

- Agent 工具调用后 kill 后端；
- Provider 连续多次并发/限流错误；
- embedding 服务离线并恢复；
- SQLite 暂时只读、磁盘空间不足或连接失败；
- worker 在 memory 写入中间崩溃；
- dispatch 各状态之间崩溃；
- checkpoint 存在但 TaskTree node 缺失；
- TaskTree finished 但 checkpoint active；
- reindex 中断并恢复；
- 备份期间持续写入。

退出条件：完整 24 小时墙钟运行通过；加速测试不能替代此项。

### Phase 9：全新 standard v2 iteration 正式复验

1. 不修改 `iter_009`；
2. 创建全新 standard v2 iteration；
3. COO 动态派发正式团队任务；
4. 使用真实实施路径、真实命令/文件和真实设备 smoke 证据；
5. 所有 child 由显式 accept/reject 决策；
6. Parent、Dispatch、Closure Gate 全部从持久化事实验收；
7. 复验结束后生成恢复、记忆、Provider 和成本报告。

---

## 13. 测试矩阵

### 13.1 Storage 生命周期

- 重启后 checkpoint、runtime records 和 memory 不丢失；
- 多次 `setup()` 幂等；
- 非法路径、只读目录、磁盘不足行为明确；
- standard v2 在 storage 故障时 holding；
- legacy 显示 degraded；
- 关闭时 worker、Saver、Store 和连接正确结束。

### 13.2 Checkpoint

- 首个副作用前已有 checkpoint；
- 重启不重复 HumanMessage；
- 已完成工具不重放；
- generation 变化后使用新 thread；
- missing/orphan/conflict 都不会静默执行；
- prune 不删除正式业务证据。

### 13.3 Dispatch/Closure

- 相同 task key 返回原 node；
- 不同 fingerprint 返回冲突；
- prepared/tree/index/scheduled/started 各阶段均可恢复；
- Auto-accepted 无法通过 Closure Gate；
- memory 无法伪造 receipt 或 acceptance audit。

### 13.4 SQLite 与长期记忆基础设施

- 三个 LangGraph/OMC 连接使用同一 SQLite 文件时无 schema/lock 冲突；
- `setup()` 重复启动不重复破坏 schema；
- sqlite-vec 可加载，embedding 维度不匹配时拒绝写入旧 index；
- checkpoint、memory、outbox 和 runtime 记录在同一 backup set 中可恢复；
- SQLite 暂时只读、连接失败、磁盘不足时 standard v2 保持 `holding`；
- 恢复后 provider queue、TaskTree、checkpoint 和 memory outbox 可由 reconciler 重建；
- 不允许使用活动 SQLite 文件的直接 `cp` 作为在线备份成功路径；
- 单实例锁阻止第二个 scheduler 启动。

### 13.4 Memory ACL 和可信度

- 员工只能读自己的私有记忆；
- 正式项目成员可读 verified project memory；
- 非成员不能读取项目 memory；
- 普通员工不能创建 verified company memory；
- 离职员工不能继续写私有记忆；
- 无 evidence 的模型结论只能 candidate；
- disputed/superseded 不进入默认 Prompt；
- secret 不进入 Store/checkpoint/audit。

### 13.5 Vector/Embedding

- namespace/status 过滤在向量排序前生效；
- 语义相似任务可找回历史经验；
- embedding 离线时结构化检索可用；
- model/dimension 不匹配拒绝写旧 index；
- reindex 未完成前继续读旧版本；
- 切换后不混用 vector space。

### 13.6 24 小时运行

- Provider 瞬态故障保持 holding 并恢复；
- 重启保留 retry metadata 和 checkpoint；
- COO 恢复不重复派发；
- memory backlog 不抢占正式 Agent；
- prompt memory 始终满足 8 条/6,000 字符/20% 限制；
- 夜间高风险变更被 holding；
- 报告、备份和健康任务重复触发仍幂等。

---

## 14. 正式上线门槛

以下全部通过前，不得启动正式 24 小时模式，也不得创建最终复验 iteration：

- WP-01 P0 正式 Gate 通过，并产生机器可读 gate report；
- WP-02 00006—00010 和 00003 模型完成正式原子对齐；
- WP-03 00011、00012 通过正式 onboarding 创建并验收；
- WP-04 11 份工作原则完成原子应用、hash 对账和回滚测试；
- WP-05 13 个 automation 任务完成注册、回执、重启恢复和幂等测试；
- WP-06 一致性在线备份通过 integrity、secret scan 和 manifest 验证；
- WP-07 至少一次独立目录恢复、对账、切换和回滚演练通过；
- WP-08 在真实隔离服务上生成无 blocking failure 的 verify report；
- ProviderGateway 覆盖全部真实模型调用，并完成受控真实聊天 Provider 429/并发恢复演练；
- dispatch、started、accept/reject 均有 durable receipt/audit；
- RuntimeStorage、Saver、Store 已进入 FastAPI 生命周期；
- SQLite 单机边界、单实例锁和 WAL/磁盘策略已验证；
- `AsyncSqliteSaver`、`AsyncSqliteStore`、`sqlite_vec` import/维度 contract 通过；
- storage 故障不会无状态执行 standard v2，且统一进入 `holding`；
- SQLite online backup、隔离恢复和整组一致性校验通过；
- 隔离 subprocess checkpoint 崩溃恢复和副作用防重放已通过，且全新专用 iteration 的真实服务恢复通过；
- employee/project/company ACL 通过；
- embedding 降级、outbox 恢复和 reindex 通过；
- 备份完成一次独立恢复演练；
- 12 人团队实际配置与文档一致，或文档已按实际团队修订；
- 自动化任务已注册、可审计且幂等；
- 24 小时墙钟故障注入通过；
- 唯一实施路径 `/Users/hanzhen/Documents/云测试的项目` 通过核验；
- FFmpeg/FFprobe 和真机 smoke 产生真实证据；
- `iter_009` 未被修改。

---

## 15. 当前优先级和下一步

### 已完成的 P0/P1 基础

1. P0 workflow contract、dispatch intent/receipt、显式验收和 Closure Gate 的代码与专项测试；
2. 正式员工运行配置对齐：00003、00006—00010；
3. 正式创建并注册 00011、00012；
4. 11 份工作原则原子应用；
5. 13 条 automation manifest 注册与 durable receipt；
6. SQLite RuntimeStorage、LangGraph Saver/Store、Memory Outbox、ACL、管理 API 和 CLI；
7. SQLite Online Backup、隔离恢复、真实服务 health/readiness 和 clean shutdown；
8. 正式目录历史测试污染入口隔离和 `iter_009` 只读保护；
9. side-effect invocation ledger 和 Provider durable retry 修复；
10. checkpoint reconciler 启动接入及 TaskTree-first 状态矩阵；
11. 隔离 subprocess crash/resume、副作用防重放和模拟 Provider 429 holding/resume；
12. sqlite-vec `v0.1.9`、混合检索、versioned shadow reindex、失败结构化降级和 outbox 保持 pending 的隔离 Gate；
13. 测试 Runtime SQLite 隔离修复与回归保护；
14. 全量测试 `4708 passed, 5 skipped, 72 warnings`，P0、Recovery、Embedding、受控真实 HTTP Provider 429、standard v2 三阶段服务恢复和独立只读恢复 Gate 均通过。

### 已完成 P1：运行告警与历史数据治理

`RUNTIME-WARNING-REMEDIATION-PLAN.md` 专项 Gate 已于 2026-08-14 完成；实现、审计、备份恢复和受控真实服务证据见 `reports/RUNTIME-WARNING-REMEDIATION-20260814.md`：

1. 修复 `ask_first` skill hook 在跳过前仍解析 trigger 的顺序问题；
2. 受控补齐既有员工缺失的 `session-logger.sh`，保留员工定制并记录哈希审计；
3. 将 `_sys_automation_*` 与普通 named project context 隔离，但保留 TaskTree/checkpoint/receipt；
4. automation/adhoc 工具改用任务条目的权威 `tree_path`，standard v2 缺树 fail closed；
5. 先扩展一致性备份覆盖 ex/quarantine，再隔离非法历史 profile；
6. 正式 `employees/00010` 和 `iter_009` 在维护前后哈希必须保持不变。

### 已完成 P1：真实长期记忆与 Provider Gate

1. 已配置本机 loopback Ollama `0.32.12` + `embeddinggemma`，在全新临时 `OMC_DATA_ROOT` 完成 endpoint/model/768 维探针；LangChain 已固定字符串输入兼容模式；
2. 已使用真实本地模型返回验证 namespace/status 先过滤、向量检索、去重重排和 Prompt budget；
3. 已验证真实 Ollama 恢复路径下的 pending/holding/backoff 和同 memory 补向量，并验证 memory worker 在正式聊天任务运行时自动让出 Provider 槽位；
4. 经审批后才允许在正式 Runtime SQLite 注册目标 index version；正式 26 条 outbox 不得直接消费；
5. 已使用 loopback OpenAI-compatible endpoint 和真实 ChatOpenAI HTTP 客户端完成 429/并发恢复演练，验证 TaskNode holding、durable backoff、优先级、同 thread 恢复和恢复 UI；机器报告见 `reports/REAL-PROVIDER-429-GATE-REPORT.json`。

### 已完成 P1：真实服务恢复与独立对账

1. 建立隔离安全维护窗口，明确禁止停止正式服务和写入正式 `.onemancompany`；维护前后同时核对 legacy `iter_009.yaml` 与目录化 `iter_009/task_tree.yaml`；
2. 在全新临时 data root 创建专用 `recovery-drill-20260815/iter_001` standard v2 iteration，未使用 `iter_009`；
3. 在 dispatch、executor started 和业务 side-effect durable boundary 后分别以退出码 87 注入服务进程退出；
4. 三次均由全新进程沿同一 checkpoint thread 恢复，execution generation 保持 1，dispatch/started/ledger/外部副作用/HumanMessage 均未重复；
5. 使用 SQLite Online Backup API 将一致性镜像恢复到独立 data root，并以 `mode=ro` 完成 TaskTree/checkpoint/outbox/dispatch/ledger/acceptance/source refs 对账；
6. 正式 Runtime SQLite、两份受保护 `iter_009` 和 26 条正式 Memory Outbox 前后不变；机器报告和审计见 `reports/REAL-SERVICE-RECOVERY-GATE-REPORT.json` 与 `reports/REAL-SERVICE-RECOVERY-GATE-20260815.md`。

### P2：运营验收

1. 完整 24 小时墙钟故障注入；
2. 真机 smoke、FFmpeg/FFprobe 和设备证据；
3. 创建全新 standard v2 iteration，完成四人正式复验；
4. 确认所有子任务由真实 `accept_child()`/`reject_child()` 决定，Closure Gate 不接受 Auto-accepted 或记忆结论；
5. 生成最终恢复、Provider、automation、memory、成本和 Closure Gate 报告。

**当前状态：实施中，尚未正式上线；`formal_24h_launch_allowed=false`。**
