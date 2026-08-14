# RuntimeStorage Checkpoint/Memory Outbox 只读对账报告

- 执行日期：2026-08-14
- 数据库：`.onemancompany/data/runtime.sqlite3`
- 执行模式：SQLite URI `mode=ro`
- 业务数据写入：无
- checkpoint 删除/恢复/重放：无
- memory outbox 删除/消费/重放：无
- `iter_009` 修改：无
- 正式上线判定：`formal_24h_launch_allowed=false`

## 1. 数据保护证据

只读检查前后数据库属性完全一致：

```text
integrity_check = ok
size_bytes      = 120360960
sha256_before   = 6eb37214d6581b01c49781feb328417f0d5b4ddf83aa7f715c0d6e6219fdd75e
sha256_after    = 6eb37214d6581b01c49781feb328417f0d5b4ddf83aa7f715c0d6e6219fdd75e
mtime_ns_before = 1786724115511653502
mtime_ns_after  = 1786724115511653502
```

受保护文件保持不变：

```text
iter_009.yaml sha256 = 4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
```

本次没有启动 OneManCompany 后端，没有运行 Memory Outbox worker，也没有调用 checkpoint reconciler 的写入入口。

## 2. Checkpoint finding 对账

健康接口此前显示：

```text
checkpoint_conflicts=7
```

逐项检查后确认，7 条均不是正式项目 TaskTree 的可恢复冲突，而是旧版系统 automation thread 被通用 orphan 规则误分类：

| node_id | automation | checkpoints | 权威 TaskTree | node 状态 | 分类 |
|---|---|---:|---|---|---|
| `ab03123f6f2d` | `coo-auto-schedule` | 25 | 存在 | `finished` | legacy system orphan |
| `1d37bd84e29c` | `coo-blocking-check` | 37 | 存在 | `finished` | legacy system orphan |
| `c97456f9d145` | `coo-blocking-check` | 21 | 存在 | `finished` | legacy system orphan |
| `431844ecc89e` | `coo-auto-schedule` | 25 | 存在 | `finished` | legacy system orphan |
| `bff9ec94f0cc` | `coo-blocking-check` | 33 | 存在 | `finished` | legacy system orphan |
| `f0de981d1e06` | `coo-blocking-check` | 31 | 存在 | `finished` | legacy system orphan |
| `8847b3131962` | `coo-evening-report` | 29 | 存在 | `finished` | legacy system orphan |

共同特征：

```text
thread prefix = omc:_sys_automation_
iteration     = unknown-iteration
node_type     = adhoc
TaskTree mode = standard
node status   = finished
```

这些 TaskTree 位于正式员工的 adhoc task 目录，不位于正式项目 `projects/**/task_tree.yaml` 扫描范围。旧 reconciler 只排除了新格式 `omc:system:adhoc:*`，没有排除旧格式 `omc:_sys_automation_*`，因此产生了假 orphan。

### 2.1 处置结论

```text
checkpoint findings total     = 7
formal actionable findings    = 0
legacy system automation      = 7
```

处理规则：

1. 不恢复、不执行、不重放这 7 个 thread；
2. 不把 checkpoint 内容解释为任务尚未完成；
3. TaskTree 的 `finished` 状态继续作为业务权威；
4. 暂不删除 checkpoint/recovery 历史，保留审计证据；
5. future reconciler 排除新旧两类 system/adhoc thread；
6. 健康接口分别报告 actionable finding 和 legacy system orphan，避免把历史遗留误报成正式冲突。

## 3. Memory Outbox 对账

报告生成时 backlog 已从此前快照的 25 增加到 26。新增记录来自正常任务终态写入，不是重复恢复或数据库损坏。

```text
memory_outbox_backlog = 26
pending                = 26
processing             = 0
holding                = 0
attempt > 0            = 0
last_error != null     = 0
```

因此这 26 条不是 worker 失败，而是 `OMC_MEMORY_ENABLED=false` 时尚未消费的持久化事件。

分类：

| 分类 | 数量 | 说明 |
|---|---:|---|
| 正式/普通任务 employee episodic | 15 | 对应普通项目或产品任务 |
| system automation employee episodic | 11 | 对应 health-check、log-cleanup、COO automation 等 |
| evidence refs 存在 | 22 | 引用文件均存在 |
| evidence refs 为空 | 4 | CEO 早期产品任务；TaskTree 存在，但没有 verification.json |
| source thread id 存在 | 11 | 均为旧 system automation thread |
| source thread id 为空 | 15 | 早期普通任务，在稳定 checkpoint thread 接入前完成 |
| authoritative tree 存在 | 26 | 全部存在 |

### 3.1 处置结论

1. 当前不手工消费、不删除、不修改为 completed；
2. 当前不启动 Memory Outbox worker，直到真实 embedding/vector Gate 完成；
3. 4 条无 evidence 的早期记录在启用 worker 前进行人工可信度复核；
4. 15 条无 source thread 的记录保留来源 node/tree，但不得被解释为可恢复 checkpoint；
5. 11 条 system automation episodic 作为低优先级员工经验处理，不得成为 dispatch、acceptance 或服务健康的正式证据；
6. worker 启用后仍依靠 durable key 去重，不通过人工 SQL 重放。

## 4. 本轮代码修复

新增：

```text
src/onemancompany/core/runtime_reconciliation.py
GET /api/admin/runtime/reconciliation
onemancompany-admin runtime reconciliation
```

行为：

- 只读取 `recoveries` 和 active `memory_outbox`；
- 不返回 memory 正文、memory key 或敏感 payload；
- 区分 `checkpoint_actionable` 与 `checkpoint_legacy_system_orphans`；
- 对 outbox 按状态、项目类型、attempt、evidence 做聚合；
- health 中保留 `checkpoint_conflicts` 兼容字段，但其值改为正式 actionable finding 数量；
- 新增 `checkpoint_findings_total` 和 `checkpoint_legacy_system_orphans`；
- checkpoint reconciler 同时排除：
  - `omc:system:adhoc:*`
  - `omc:_sys_automation_*`

## 5. 测试证据

```text
针对性回归：9 passed in 1.45s
相关模块回归：36 passed in 3.95s
完整测试套件：4679 passed, 5 skipped, 72 warnings in 139.38s
```

未运行需要真实后端在线的 readiness Service Gate；本阶段是离线只读对账，避免为验证而启动正式服务并产生新的 automation/outbox 写入。

## 6. 验收结论

```text
Runtime SQLite integrity             PASS
read-only before/after hash          PASS
iter_009 unchanged                   PASS
7 checkpoint findings classified    PASS
formal actionable conflicts = 0      PASS
26 outbox rows classified            PASS
outbox replay/delete                 NOT PERFORMED
formal_24h_launch_allowed            false
```

下一阶段进入受控云 embedding、sqlite-vec、混合检索和 versioned reindex 演练。在真实 embedding 配置、模型维度探针和备份完成之前，不启用正式 Memory Outbox worker。
