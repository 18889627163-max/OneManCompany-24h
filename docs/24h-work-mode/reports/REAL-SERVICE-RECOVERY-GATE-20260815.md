# Standard v2 三阶段真实服务恢复与独立只读恢复 Gate 报告

**检查日期**：2026-08-15
**状态**：✅ 通过
**运行范围**：全新临时 data root + 三次独立服务进程退出/重启 + SQLite Online Backup + 独立只读恢复
**正式服务停止**：未授权、未执行
**正式上线许可**：`formal_24h_launch_allowed=false`

## 1. 安全维护窗口

本轮建立的是隔离恢复演练维护窗口：

- `scope=isolated_temp_data_root`；
- `formal_service_stop_authorized=false`；
- `formal_data_writes_authorized=false`；
- 演练数据和恢复副本均位于 `/private/tmp` 的新目录；
- 未停止当前前端/Node 进程；
- 未启动或修改正式 `.onemancompany` TaskTree；
- 未消费正式 Memory Outbox。

因此，本报告证明的是“真实 RuntimeStorage/LangGraph 服务进程生命周期的隔离恢复能力”，不是对正在使用的正式业务实例做破坏性演练。

## 2. 新建 standard v2 演练 iteration

演练创建：

```text
project_id=recovery-drill-20260815
iteration_id=iter_001
mode=standard
workflow_contract_version=2
execution_generation=1
```

父节点为显式 Review 节点，三个子节点分别对应：

1. dispatch 持久化边界；
2. executor started receipt 持久化边界；
3. business side-effect ledger 完成边界。

线程严格使用：

```text
omc:recovery-drill-20260815:iter_001:{node_id}:g1
```

## 3. 三处进程退出和恢复结果

每个场景均在目标 graph step 的 LangGraph checkpoint、TaskTree execution checkpoint 和业务 receipt 已落盘后调用 `os._exit(87)`，然后由全新 Python 服务进程重新打开同一个 Runtime SQLite 和 TaskTree，并以 `ainvoke(None, same_config)` 继续。

| 场景 | 故障进程 | 恢复进程 | checkpoint_before | HumanMessage | 外部副作用次数 | 最终 dispatch | ledger | 验收路径 |
|---|---:|---:|---|---:|---:|---|---|---|
| dispatch | 87 | 0 | true | 1 | 1 | started | completed | accept_child |
| executor_started | 87 | 0 | true | 1 | 1 | started | completed | accept_child |
| side_effect | 87 | 0 | true | 1 | 1 | started | completed | accept_child |

三个线程恢复前后均保持不变；三个 execution generation 均保持 `1`。父节点最终只有三个子节点，没有重复派发节点。

## 4. 正式状态对账

结果：

- `dispatch_intents=3`，三个 task key 唯一，最终状态均为 `started`；
- 每个 started receipt 均包含 `executor_started=true` 和 `executor_started_at`；
- `tool_invocation_ledger=3`，均为 `completed`；
- 三个外部 counter 均为 `1`；
- 每个 checkpoint thread 均有 6 条 checkpoint 记录；
- 每个线程只有 1 条原始 HumanMessage；
- 三个 TaskNode 最终均由正式 `accept_child()` 路径变为 `accepted`；
- 每条 `acceptance_audit.decided_via=accept_child`；
- 没有从记忆文本、模型结果或 Auto-accepted 推断验收。

## 5. Memory Outbox 和来源引用

演练终态生成 3 条隔离 episodic Memory Outbox 事件：

```text
status=pending
attempt=0
```

每条 payload 均包含：

```text
source_project_id
source_iteration_id
source_node_id
source_thread_id
```

恢复副本中的 Outbox 状态、attempt 和来源 thread 与源数据完全一致；演练没有启动 Memory worker 消费这些事件。

## 6. Online Backup 与独立恢复

使用 `RuntimeStorage.backup()` 和 SQLite Online Backup API 创建一致性镜像：

```text
backup_method=sqlite_online_backup_api
quick_check=ok
integrity_check=ok
database_size_bytes=237568
database_checksum=sha256:4a7d1cb55afa4484376c4cc9313874575cccc9801f3ed5284d54a136bca8b5a1
```

备份恢复到独立 data root 后，以 SQLite URI `mode=ro` 和 `PRAGMA query_only=ON` 打开。写入探针被拒绝，证明核对连接为只读。

只读对账一致的对象包括：

- TaskTree 及三个节点的 checkpoint thread；
- checkpoint 数量；
- dispatch intent 和 executor started receipt；
- side-effect ledger 及结果；
- acceptance audit；
- Memory Outbox 状态、attempt 和 source refs。

恢复副本只用于核对，没有被切换为正式权威数据源。

## 7. 正式数据保护结果

演练前后保持不变：

```text
runtime.sqlite3
2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de

legacy iterations/iter_009.yaml
4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626

directory iterations/iter_009/task_tree.yaml
b3b877e6b584feefe084a40f50a75b7161ae018b42910f9c2e54780e46d087ab

formal Memory Outbox
pending=26
attempted=0
```

报告、备份 manifest、源/恢复 TaskTree 和 SQLite 镜像通过凭证模式扫描，没有持久化 API key。

## 8. 回归验证

```text
专项恢复/RuntimeStorage/Provider 回归：21 passed in 11.45s
完整测试：4708 passed, 5 skipped, 72 warnings in 175.04s
git diff --check：passed
```

72 条 warning 为现有异步 mock/deprecation 警告，本轮没有新增测试失败。

## 9. Gate 结论

22 项检查全部通过：

```text
maintenance_window_isolated
new_standard_v2_iteration
three_process_crashes_observed
three_fresh_process_resumes_succeeded
same_checkpoint_threads_recovered
original_human_message_not_duplicated
dispatch_receipts_exactly_once
executor_started_receipts_exactly_once
side_effect_ledger_exactly_once
execution_generation_unchanged
all_checkpoint_threads_present
no_duplicate_children
explicit_acceptance_only
memory_outbox_preserved_pending
memory_source_refs_match_threads
online_backup_verified
restore_integrity_ok
restore_opened_read_only
restored_business_state_matches
formal_data_unchanged
formal_memory_outbox_not_consumed
credentials_not_persisted
```

下一阶段进入：

1. 24 小时墙钟连续运行；
2. 真实设备 smoke 和 FFmpeg/FFprobe 证据；
3. 全新四人 standard v2 最终正式复验。

在这些项目完成前，仍保持：

```text
formal_24h_launch_allowed=false
```
