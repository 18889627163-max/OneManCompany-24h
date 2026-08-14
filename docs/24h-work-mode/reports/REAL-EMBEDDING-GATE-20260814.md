# 真实 Embedding Provider 隔离 Gate 报告

- **检查日期：** 2026-08-14
- **Gate：** `real_cloud_embedding_isolated`（脚本兼容名，实际 Provider 为本地 Ollama）
- **当前结论：** ✅ 本地 Ollama `embeddinggemma` 隔离 Gate 通过
- **正式上线：** `formal_24h_launch_allowed=false`

## 1. 最终 Provider 决策

本轮不再使用不支持 `/v1/embeddings` 的聊天 Provider，改用本机 loopback Ollama：

```text
provider=Ollama
ollama_version=0.32.12
base_url=http://127.0.0.1:11434/v1
model=embeddinggemma
dimensions=768
index_version=v1
provider_fingerprint=9b514b1a65f6ebfaa4da53a116ea003831b044dc32a989733eace74554ef5b0c
```

本地配置保存在被 Git 忽略且权限为 `0600` 的 `.env.embedding.local`。报告不保存 API key、Authorization header 或原始向量。

机器条件：

```text
architecture=arm64
memory=24 GiB
free_disk≈693 GiB
model_size=621 MB
ollama_cli=$HOME/.local/bin/ollama
listen=127.0.0.1:11434
```

## 2. 安装与兼容修复

Ollama 应用安装在：

```text
/Applications/Ollama.app
```

创建用户级 CLI 链接：

```text
$HOME/.local/bin/ollama
```

`embeddinggemma` 原生 `/api/embed` 和 OpenAI-compatible `/v1/embeddings` 探针均返回 768 维向量。

首次运行项目 Gate 时，LangChain 默认把文本转换成 token-id 整数数组，Ollama 返回：

```text
HTTP 400
invalid input type
```

代码已为 `OpenAIEmbeddings` 设置：

```python
check_embedding_ctx_length=False
```

这样请求保留为字符串。官方 OpenAI endpoint 同样接受字符串输入；OneManCompany 的单条记忆还受 6,000 字符和 Prompt 预算限制，不依赖 LangChain 在客户端进行 token-id 分块。

修改位置：

```text
src/onemancompany/main.py
scripts/check-real-embedding-gate.py
```

对应测试确认运行时构造参数固定为字符串模式。

## 3. 隔离约束

Gate 强制：

1. 使用自动生成的全新临时 `OMC_DATA_ROOT`；
2. 拒绝正式 `.onemancompany` 及其子目录；
3. 不启动正式 automation、TaskTree 恢复或正式 Memory worker；
4. 不读取或写入正式 Runtime SQLite；
5. 临时数据库 outbox 必须为 `0`；
6. 正式 26 条 outbox 不消费、不删除、不重放；
7. API key、请求头、响应正文和原始向量不进入报告。

## 4. Gate 结果

机器可读证据：

```text
docs/24h-work-mode/reports/REAL-EMBEDDING-GATE-REPORT.json
```

结果：

```text
status=passed
embedding_status=healthy
vector_status=healthy
vector_count=4
outbox_count=0
sqlite_integrity=ok
sqlite_vec_version=v0.1.9
formal_outbox_touched=false
formal_launch_allowed=false
```

检查矩阵：

```text
probe_healthy=true
vector_index_enabled=true
vectors_persisted=true
verified_project_retrieved=true
own_private_retrieved=true
other_private_filtered=true
teammate_can_read_project=true
teammate_cannot_read_private=true
outsider_cannot_read_project=true
candidate_filtered=true
deduplicated=true
metadata_complete=true
default_prompt_budget=true
tight_prompt_budget=true
semantic_scores_present=true
formal_outbox_not_imported=true
```

因此以下能力已在真实本地模型返回下完成验证：

- endpoint/model/dimension 探针；
- sqlite-vec 真实向量写入；
- 语义相似度检索；
- employee 私有记忆隔离；
- project member/outsider ACL；
- candidate 默认过滤；
- verified project memory 检索；
- 去重和必要 metadata；
- 8 条、6,000 字符和 20% Prompt 预算；
- 正式 Runtime 隔离。

## 5. 专项测试

```text
tests/unit/test_memory_index_startup.py
tests/integration/test_real_embedding_gate.py
```

结果：

```text
6 passed
```

## 6. 正式数据边界复核

Gate 执行后只读复核：

```text
Runtime SQLite SHA-256 = 2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de
active 00010 SHA-256   = 2acc362d7e62228c86b25ba55a19660ee27ead33ba706c94218325ec43a510b6
archived 00010 SHA-256 = 80e09030ebfbd6bc39fde3f023311e8adc4a394b0caaf350678e1b4ad13c3a4d
iter_009.yaml SHA-256 = 4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
Memory Outbox           = pending/attempt=0/count=26
```

与 Gate 前基线完全一致。

## 7. 历史失败记录

采用 Ollama 前，曾使用现有聊天 Provider 候选：

```text
model=text-embedding-3-small
dimensions=1536
GET /models=HTTP 200
target_model_listed=false
embedding_endpoint_models=0
POST /embeddings=HTTP 503
error_code=model_not_found
```

该失败证明原聊天 Provider 不能作为 Embedding Provider，不是维度填写错误。旧 Provider API key 已从 `.env.embedding.local` 移除；已暴露的旧 key 仍必须在服务商后台撤销。

## 8. 尚未完成

本 Gate 通过不等于正式 24 小时上线。下一步仍需：

1. 在全新临时 Runtime 中验证 Ollama 不可用时的结构化保存、outbox `holding`、`next_retry_at` 和恢复补向量；
2. 验证 memory worker 不抢占正式聊天 Provider；
3. 执行真实聊天 Provider 429 Gate；
4. 执行全新 standard v2 真实服务三阶段故障恢复；
5. 独立恢复对账；
6. 24 小时墙钟、真机 smoke 和最终四人正式复验。

当前判定：

```text
real_embedding_provider_isolated=passed
formal_outbox_touched=false
formal_24h_launch_allowed=false
```
