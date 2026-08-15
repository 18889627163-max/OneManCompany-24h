# 真实 HTTP Provider 429 / 重启恢复 Gate 报告

**检查日期**：2026-08-14
**状态**：✅ 通过
**运行范围**：全新临时 `OMC_DATA_ROOT` + loopback OpenAI-compatible HTTP endpoint
**正式上线许可**：`formal_24h_launch_allowed=false`

## 1. Gate 定义

本 Gate 使用真实 `ChatOpenAI` HTTP 客户端和真实 HTTP 状态码，但 Provider endpoint 是为了可重复故障注入而启动的本机受控 OpenAI-compatible 服务，不是公共云模型服务。它验证协议、运行时、持久化和恢复链路，不声明公共云供应商已被主动压测。

执行序列：

```text
正式 TaskNode processing
→ durable dispatch intent + executor receipt
→ durable side-effect ledger + 外部计数器
→ LangGraph 保存 side-effect 后 checkpoint
→ ChatOpenAI 收到真实 HTTP 429
→ TaskNode=holding / hold_reason=provider_capacity
→ 保存 attempt=1 / next_retry_at / checkpoint_thread_id
→ 退出第一进程，模拟后端重启
→ 第二进程打开同一临时 Runtime SQLite 和 TaskTree
→ Provider 恢复为 HTTP 200
→ graph.ainvoke(None) 从 pending chat node 恢复
→ 同一 Provider request 和 checkpoint thread 完成
```

## 2. 实现内容

新增：

- `scripts/check-real-provider-429-gate.py`
- `tests/integration/provider_429_gate_worker.py`
- `tests/integration/test_real_provider_429_gate.py`
- `src/onemancompany/core/provider_task_state.py`

并完成以下运行时接线：

1. checkpointed chat turn 使用 `thread_id + messages` 生成稳定 Provider request ID；
2. Provider 429 时把 TaskNode 投影为 durable holding；
3. `attempt` 和 `next_retry_at` 跨 RuntimeStorage 生命周期保留；
4. Provider 成功后原子清除 queue/retry state 的活动 retry 元数据，同时保留历史 attempt；
5. Memory worker 在 claim 和 embedding 调用前检查高优先级 Provider 工作；
6. 业务/恢复任务优先级高于 memory/embedding；
7. UI API 投影 `hold_reason`、`checkpoint_status`、`next_retry_at`，前端显示“等待模型容量”。

## 3. Gate 结果

机器可读报告：

```text
docs/24h-work-mode/reports/REAL-PROVIDER-429-GATE-REPORT.json
```

16 项断言全部通过：

```text
real_chat_http_429_observed=true
tasknode_stayed_holding=true
attempt_and_retry_persisted_across_restart=true
formal_task_precedes_memory=true
memory_worker_yielded_provider_slot=true
same_checkpoint_thread_recovered=true
provider_request_reused_and_completed=true
dispatch_not_duplicated=true
side_effect_not_duplicated=true
original_human_message_not_duplicated=true
recovery_ui_fields_visible=true
recovery_ui_cleared=true
holding_and_recovery_audited=true
credentials_not_persisted=true
formal_data_unchanged=true
formal_memory_outbox_not_consumed=true
```

关键证据：

```text
HTTP chat attempts = 2（429 + 200）
checkpoint thread = omc:provider-gate:iter_001:provider-gate-node:g1
hold status = holding
hold reason = provider_capacity
checkpoint status = waiting_provider
attempt = 1
next_retry_at = 跨进程一致
final Provider status = completed
final active retry time = null
dispatch intent count = 1
tool ledger count = 1
external side-effect counter = 1
HumanMessage count = 1
provider holding/recovered audit count = 2
```

Provider 槽位让位顺序：

```text
priority:business:start
priority:business:end
priority:memory:start
```

因此 Memory 后台调用在正式业务 Provider 请求运行期间没有开始外部请求。

## 4. 测试结果

专项回归：

```text
87 passed in 12.47s
```

最终完整回归：

```text
4706 passed, 5 skipped, 72 warnings in 169.46s
```

warnings 仍主要是既有 coroutine mock 未 await、LangGraph `create_react_agent` 弃用和 Starlette/httpx 弃用提示；本 Gate 没有新增失败测试。

## 5. 正式数据保护

Gate 前后正式数据只读哈希一致：

```text
Runtime SQLite:
2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de

active employee 00010:
2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6

archived employee 00010:
80e09030ebfbd6bc39fde3f023311e8adc4a394b0caaf350678e1b4ad13c3a4d

legacy `iterations/iter_009.yaml`:
4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626

current `iterations/iter_009/task_tree.yaml`:
b3b877e6b584feefe084a40f50a75b7161ae018b42910f9c2e54780e46d087ab
```

正式 Memory Outbox 仍为：

```text
pending=26
attempt=0
```

只读核对已确认两个哈希来自两个不同的受保护文件：历史报告的 `4c8cdb...` 对应 legacy `iterations/iter_009.yaml`，`b3b877...` 对应目录化运行状态 `iterations/iter_009/task_tree.yaml`。两者在本 Gate 前后均未变化，不存在本轮修改或基线冲突。

## 6. 剩余上线阻塞项

Provider 429 Gate 已完成。下一阶段是：

1. 申请维护窗口并创建全新专用 standard v2 恢复演练 iteration；继续同时保护 legacy `iter_009.yaml` 和目录化 `iter_009/task_tree.yaml`；
2. 在 dispatch、executor started、业务 side-effect 三处执行真实服务退出/重启；
3. 将在线备份恢复到独立 data root，核对 TaskTree、checkpoint、receipt、ledger、acceptance 和 Memory Outbox；
4. 完成 24 小时墙钟、真机 smoke 和最终四人 standard v2 复验。

因此继续保持：

```text
formal_24h_launch_allowed=false
```
