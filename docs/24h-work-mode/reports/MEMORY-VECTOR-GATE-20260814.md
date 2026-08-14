# Memory / sqlite-vec / Versioned Reindex Gate 报告

- 日期：2026-08-14
- 范围：受控云 embedding 启动契约、sqlite-vec、混合检索、versioned shadow reindex、测试数据隔离
- 隔离 Gate：`passed`
- 真实云 embedding Gate：`blocked_by_configuration`
- 正式 24 小时上线：`formal_24h_launch_allowed=false`

## 1. 边界

本轮没有：

- 启动正式 Memory worker；
- 消费、重放或删除正式 Memory Outbox；
- 在正式 Runtime SQLite 注册 embedding index contract 或写入向量；
- 修改、迁移、恢复或重跑 `iter_009`；
- 使用长期记忆代替 TaskTree、dispatch/executor receipt 或 acceptance audit。

真实云 embedding 环境变量当前均未设置：

```text
OMC_MEMORY_ENABLED=unset
OMC_MEMORY_EMBEDDING_BASE_URL=unset
OMC_MEMORY_EMBEDDING_API_KEY=unset
OMC_MEMORY_EMBEDDING_MODEL=unset
OMC_MEMORY_EMBEDDING_DIMENSIONS=unset
OMC_MEMORY_INDEX_VERSION=unset
```

因此本报告只能批准隔离 SQLite/vector/reindex Gate，不能批准真实云 embedding 或正式 24 小时运行。

## 2. 已实现契约

### 2.1 sqlite-vec 与混合检索

- ARM64 环境实际加载 `sqlite-vec v0.1.9`；
- 结构化过滤先限制 namespace、状态和过期时间，再执行 vector limit；
- 默认普通检索只允许 active/verified 或获准的 employee episodic memory；
- embedding 不可用、vector space 不兼容或向量查询异常时，自动降级为结构化检索；
- 降级不改变正式 TaskTree 状态，也不把业务任务置为 failed。

### 2.2 embedding/index identity

`memory_index_config` 现在固定校验：

```text
index_version
embedding dimensions
text fields
embedding model identity
provider endpoint fingerprint
```

Provider fingerprint 仅保存 base URL 的 SHA-256，不保存 API key。同一 index version 出现模型、维度、字段或 Provider 身份漂移时 fail closed。

### 2.3 versioned shadow reindex

当前实现不再创建独立 `memory-vN.sqlite3` 或 `active-memory-index.json`。正式实现为：

```text
同一 Runtime SQLite
+ memory_vector_versions shadow rows
+ 单事务归档旧向量
+ 单事务切换 store_vectors 和 active index contract
```

失败时 active version 不切换，旧 vector 继续可用；如果新旧模型空间不兼容，reindex 前检索降级为结构化模式。Memory Outbox 不因 reindex 失败被消费或增加 attempt。

## 3. 隔离演练证据

隔离数据库：

```text
/tmp/omc-vector-gate.aPk8So/runtime.sqlite3
SHA-256=bc1126dc3a7a3aedc74ffdd85d2f33af6e3e0311e0a450f11e7953c2adaf4da2
integrity_check=ok
sqlite-vec=v0.1.9
```

只读复核结果：

```text
index contracts:
  v1 dims=4 model=same-model provider=isolated-provider active=0
  v2 dims=4 model=same-model provider=isolated-provider active=1
  v3 dims=4 model=different-model provider=isolated-provider active=0

archived vectors:
  v1=1
  v2=1

store rows=1
outbox=pending count=1 min_attempt=0 max_attempt=0
```

演练结果：

```json
{
  "sqlite_vec": "v0.1.9",
  "pre_switch": {
    "active": "v1",
    "target": "v2",
    "vector_enabled": true,
    "reindex_required": true,
    "score_present": true
  },
  "reindex": {
    "status": "completed",
    "mode": "atomic_shadow_rebuild",
    "from_version": "v1",
    "to_version": "v2",
    "memory_count": 1,
    "vector_count": 1
  },
  "post_switch": {
    "active": "v2",
    "vector_enabled": true,
    "reindex_required": false,
    "score_present": true,
    "archived_versions": [["v1", 1], ["v2", 1]]
  },
  "failure_degrade": {
    "target": "v3",
    "vector_enabled": false,
    "structured_score_is_none": true,
    "active_after": "v2"
  },
  "outbox_after_failure": ["pending", 0]
}
```

## 4. 正式数据库 schema-only migration 事件

在本轮完整测试首次执行时，`tests/unit/test_main.py` 的完整 FastAPI lifespan 测试进入 `_runtime_lifespan()`。默认 `OMC_MEMORY_DATABASE_PATH=.onemancompany/data/runtime.sqlite3` 直接按仓库 cwd 解析，没有真正跟随 `OMC_DATA_ROOT`，因此测试对正式数据库执行了 OMC owned additive schema migration。

已知变化：

```text
before SHA-256=6eb37214d6581b01c49781feb328417f0d5b4ddf83aa7f715c0d6e6219fdd75e
before size=120360960

after  SHA-256=2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de
after  size=120377344
```

只读审计确认正式库仅出现本轮已知 OMC schema v4 变化：

- `schema_migrations` 增加 version 4；
- `memory_index_config` 增加 `embedding_model`、`provider_fingerprint`、`activated_at`；
- 新增 `memory_vector_versions` 表及索引；
- `memory_index_config` 记录数为 0；
- `memory_vector_versions` 记录数为 0；
- 未发现本轮正式 reindex 或正式 vector 写入；
- `PRAGMA integrity_check=ok`。

没有用旧快照覆盖或擅自回滚该正式数据库；该事件按 schema-only migration 保留审计记录。

## 5. 测试隔离修复

已增加三层保护：

1. 相对 `OMC_MEMORY_DATABASE_PATH` 统一解析在 `OMC_DATA_ROOT` 内；legacy `.onemancompany/` 前缀会剥离后挂到当前 data root；相对路径逃逸会 fail closed；
2. unit test autouse fixture 把全局 settings 的 Runtime SQLite 重定向到每个测试的 `tmp_path`；
3. `RuntimeStorage` 在 pytest 中发现目标为仓库正式 `.onemancompany/data/runtime.sqlite3` 时，在创建目录或连接数据库前直接拒绝。

回归验证：

```text
路径/pytest guard                    3 passed
main.py lifespan                     36 passed
memory/vector/reconciliation targeted 126 passed
完整测试                             4692 passed, 5 skipped, 72 warnings in 140.01s
```

以上测试前后，正式数据库保持：

```text
SHA-256=2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de
size=120377344
```

## 6. 正式状态只读复核

```text
formal runtime integrity_check=ok
memory_index_config rows=0
memory_vector_versions rows=0
memory_outbox pending=26
memory_outbox min_attempt=0
memory_outbox max_attempt=0
iter_009.yaml SHA-256=4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626
```

26 条正式 outbox 未消费、未重放、未删除。`iter_009` 未修改。

## 7. Gate 判定与下一步

```text
isolated_sqlite_vec_gate=passed
isolated_versioned_reindex_gate=passed
structured_degradation_gate=passed
test_formal_database_isolation_gate=passed
real_cloud_embedding_gate=blocked_by_configuration
formal_24h_launch_allowed=false
```

下一步必须由操作者提供受控云 embedding 配置。先在全新临时 `OMC_DATA_ROOT` 做 endpoint/model/dimension 探针，不启动正式 Memory worker；探针、向量写入、检索和 reindex 全部通过并审批后，才允许考虑在正式 Runtime SQLite 注册新 index version。正式 26 条 outbox 不得未经审批直接消费。
