# 真实云 Embedding 隔离 Gate 报告

- **检查日期：** 2026-08-14
- **Gate：** `real_cloud_embedding_isolated`
- **当前结论：** ❌ 未通过，等待可用的独立 OpenAI-compatible embedding 服务
- **正式上线：** `formal_24h_launch_allowed=false`

## 1. 本轮完成内容

新增机器可执行 Gate：

```text
scripts/check-real-embedding-gate.py
```

该 Gate 强制执行以下隔离约束：

1. `OMC_DATA_ROOT` 必须是全新或空目录；
2. 拒绝正式 `.onemancompany` 目录及其子目录；
3. 不启动正式后端、automation、任务恢复或 Memory Outbox worker；
4. 不加载正式 Runtime SQLite；
5. 报告只记录 Provider URL SHA-256 fingerprint，不记录 URL、API key、请求头或响应正文；
6. 临时数据库中的 outbox 必须为 `0`；
7. 正式 26 条 outbox 不消费、不删除、不重放。

新增自动化保护测试：

```text
tests/integration/test_real_embedding_gate.py
```

测试覆盖：

- 本地 OpenAI-compatible embedding stub 下的完整向量写入和检索；
- sqlite-vec 实际索引；
- employee 私有记忆隔离；
- project member/outsider ACL；
- candidate 默认过滤；
- verified project memory 检索；
- 去重、必要 metadata 和 Prompt 字符预算；
- 正式 data root fail closed；
- API key 不进入报告。

测试结果：

```text
2 passed
```

## 2. 真实候选服务探针

现有聊天服务的 `/models` 目录可访问，但未公布 embedding 模型。为确认兼容性，使用以下候选契约在全新临时 data root 中执行探针：

```text
model=text-embedding-3-small
dimensions=1536
index_version=candidate-v1
provider_fingerprint=9c215f0b555c6c9374b3235018713683596fd8fe55037e92e4a97e62854b9bb7
```

真实 `/embeddings` 调用结果：

```text
InternalServerError
HTTP status=503
```

因此该现有聊天服务目前不能作为已批准的 embedding Provider。没有在正式环境注册 `candidate-v1`，也没有执行正式向量写入。

机器可读证据：

```text
docs/24h-work-mode/reports/REAL-EMBEDDING-GATE-REPORT.json
```

## 3. 当前阻塞条件

必须提供一套可用的独立 embedding 配置：

```text
OMC_MEMORY_EMBEDDING_BASE_URL
OMC_MEMORY_EMBEDDING_API_KEY
OMC_MEMORY_EMBEDDING_MODEL
OMC_MEMORY_EMBEDDING_DIMENSIONS
OMC_MEMORY_INDEX_VERSION
```

这些值只能写入被 Git 忽略的本地环境文件或进程环境，API key 不得提交到仓库。

## 4. 重新执行方法

在本地创建被 Git 忽略的 `.env.embedding.local`：

```bash
OMC_MEMORY_EMBEDDING_BASE_URL=https://YOUR-ENDPOINT/v1
OMC_MEMORY_EMBEDDING_API_KEY=YOUR-KEY
OMC_MEMORY_EMBEDDING_MODEL=YOUR-EMBEDDING-MODEL
OMC_MEMORY_EMBEDDING_DIMENSIONS=YOUR-DIMENSIONS
OMC_MEMORY_INDEX_VERSION=v1
```

执行：

```bash
cd /Users/hanzhen/Downloads/OneManCompany-main
set -a
source .env.embedding.local
set +a
.venv/bin/python scripts/check-real-embedding-gate.py \
  --report docs/24h-work-mode/reports/REAL-EMBEDDING-GATE-REPORT.json
```

只有 JSON 报告满足以下条件，第一步才算通过：

```text
status=passed
probe_healthy=true
vector_index_enabled=true
vectors_persisted=true
verified_project_retrieved=true
other_private_filtered=true
outsider_cannot_read_project=true
candidate_filtered=true
default_prompt_budget=true
tight_prompt_budget=true
formal_outbox_touched=false
outbox_count=0
```

## 5. Gate 判定

```text
real_cloud_embedding_isolated=failed
reason=existing_chat_provider_embedding_endpoint_http_503
formal_outbox_touched=false
formal_24h_launch_allowed=false
```

按照既定顺序，真实 Embedding Gate 通过前，不把第二步 Provider 429 Gate 标记为正式完成，也不启动正式 Memory worker。

## 6. 正式数据边界复核

探针结束后只读复核结果：

```text
Runtime SQLite SHA-256 = 2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de
active 00010 SHA-256   = 2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6
archived 00010 SHA-256 = 80e09030ebfbd6bc39fde3f023311e8adc4a394b0caaf350678e1b4ad13c3a4d
iter_009.yaml SHA-256 = 4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
Memory Outbox           = pending/attempt=0/count=26
```

这些值与 Gate 执行前记录一致。本轮没有启动正式 Memory worker，没有写正式向量索引，也没有修改 `iter_009`。
