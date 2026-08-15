# Embedding Pending/Backoff/Recovery Gate 报告

**检查日期**：2026-08-14
**状态**：✅ 通过
**运行范围**：全新临时 `OMC_DATA_ROOT`，未打开或消费正式 Memory Outbox
**正式上线许可**：`formal_24h_launch_allowed=false`

## 1. 目的

本 Gate 验证长期记忆的结构化落盘与向量写入已经拆成两个可恢复阶段：

```text
业务终态事件进入 durable outbox
→ 结构化 memory 以 embedding_status=pending 落盘
→ Embedding 传输失败
→ outbox=holding，记录 attempt/next_retry_at
→ Provider 恢复
→ 重试同一 memory key
→ embedding_status=indexed
→ outbox=completed
```

长期记忆仍然只是辅助上下文。本 Gate 不修改 TaskTree、dispatch receipt、executor started receipt、side-effect ledger 或 acceptance audit。

## 2. 实现修复

本轮修复了旧状态机中“结构化记忆已经保存，但 outbox 被错误标记 completed，恢复后没有 durable 事件补向量”的缺口：

1. `MemoryService.propose()` 先使用 `index=False` 持久化结构化记录；
2. 向量写入通过独立 `ensure_indexed()`/`RuntimeStorage.index_memory()` 完成；
3. Embedding 失败时保留 `embedding_status=pending`，不删除结构化记录；
4. `MemoryOutboxWorker` 将事件置为 `holding`，采用 30 秒起步、最长 1800 秒的 durable backoff；
5. 重试使用同一 dedupe key，不创建第二条 memory；
6. 成功后更新为 `embedding_status=indexed`，并清空 `next_retry_at` 和 `last_error`；
7. 启动探针失败时仍保留安全的 index contract 和 embedder，使 worker 无需重启 RuntimeStorage 即可在 Provider 恢复后补向量；
8. `/api/health` 和管理状态接口从 RuntimeStorage 的动态状态反映恢复后的 `embedding=healthy`、`sqlite_vec=healthy`。

## 3. 真实 Provider 与故障注入

真实 Provider：

```text
Ollama：0.32.12
模型：embeddinggemma
维度：768
接口：http://127.0.0.1:11434/v1
```

演练先对真实 Ollama 执行 endpoint/model/dimension 探针，然后在同一 embedding adapter 上注入受控传输中断。恢复阶段解除中断，并由同一个 SQLite Store/Memory Outbox worker 调用真实 Ollama 完成向量写入。

没有停止当前正式后端，也没有停止用户正在使用的主 Ollama 服务。

## 4. Gate 结果

报告文件：

```text
docs/24h-work-mode/reports/EMBEDDING-RECOVERY-GATE-REPORT.json
```

关键结果：

```text
status=passed
failure status=holding
failure attempt=1
next_retry_at recorded=true
last_error=ConnectionError
structured memory embedding_status=pending
vector_count_before=0
recovery status=completed
recovery attempt=2
next_retry_at cleared=true
same memory reused=true
embedding_status=indexed
semantic score available=true
store_count=1
vector_count_after=1
sqlite_integrity=ok
formal_outbox_touched=false
formal_launch_allowed=false
```

## 5. 正式数据保护复核

Gate 前后以下 SHA-256 完全一致：

```text
Runtime SQLite:
2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de

active employee 00010:
2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6

archived employee 00010:
80e09030ebfbd6bc39fde3f023311e8adc4a394b0caaf350678e1b4ad13c3a4d

iter_009.yaml:
4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
```

正式 Memory Outbox 仍为：

```text
status=pending
attempt=0
count=26
```

因此本轮没有消费、删除、迁移或重放正式 26 条 outbox，也没有修改 `iter_009`。

## 6. 自动化验证

专项回归覆盖：

- 启动探针失败仍保留 retryable index contract；
- worker 先落结构化 memory；
- Provider 不可用时 holding/backoff；
- Provider 恢复后补向量；
- memory id/dedupe key 幂等；
- completed 状态清理 retry metadata；
- 动态 health 恢复；
- Gate 拒绝正式 `.onemancompany` data root；
- 报告不包含 API key。

完整回归结果：

```text
4699 passed, 5 skipped, 72 warnings in 167.58s
```

## 7. 剩余阻塞项

Embedding Provider Gate 已完成，但以下项目仍未完成：

1. memory worker 与聊天 Agent 对 Provider 并发槽位的真实让位验证；
2. 真实聊天 Provider HTTP 429、长 backoff、优先级和恢复 UI Gate；
3. 全新 standard v2 iteration 的三阶段真实服务退出恢复；
4. 独立恢复后的正式 receipt/ledger/acceptance 对账；
5. 24 小时墙钟、真机 smoke 和最终四人 standard v2 复验。

因此仍必须保持：

```text
formal_24h_launch_allowed=false
```
