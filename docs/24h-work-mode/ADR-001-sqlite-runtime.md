# ADR-001：OneManCompany 单机运行时采用 SQLite

- 状态：已接受
- 日期：2026-08-13
- 适用范围：OneManCompany standard v2 控制平面

## 决策

当前单机阶段统一使用：

```text
SQLite
+ LangGraph AsyncSqliteSaver
+ LangGraph AsyncSqliteStore
+ sqlite-vec（向量检索可选能力）
```

当前不引入 PostgreSQL、pgvector、FAISS 或 SQLite/ PostgreSQL 双写路径。

## 适用边界

- 单主机；
- 单个正式调度实例；
- 受控 RuntimeStorage 访问数据库文件；
- WAL、`synchronous=FULL`、`busy_timeout` 和 SQLite Online Backup 必须启用；
- standard v2 的 checkpoint、memory、provider queue、dispatch intent、tool ledger 和 outbox 必须纳入统一备份与恢复对账；
- 数据库不可用时进入 `holding`，不得无状态降级或静默从头执行。

## 不在本决策中的内容

SQLite 只是持久化底座，不等于长期记忆业务已经完成。以下能力仍必须单独实现和验收：

- `search_memory`、`propose_memory`；
- namespace ACL；
- Memory Outbox 和 worker；
- embedding、维度校验和 sqlite-vec index；
- 冲突、审批、过期和敏感信息过滤；
- checkpoint/TaskTree/provider queue reconciler；
- 在线备份、隔离恢复和 24 小时故障演练。

## PostgreSQL 迁移触发条件

出现下列情况时，必须重新提交迁移 ADR，而不是直接并行写入 PostgreSQL：

- 需要两个或以上后端实例同时调度；
- 需要跨主机执行、主备或高可用；
- SQLite 写锁等待、checkpoint 延迟或向量检索延迟超过压测门槛；
- 单机备份/恢复无法满足 RPO/RTO；
- 需要数据库级远程角色隔离或集中审计。

迁移前必须完成数据导出、schema/embedding version 兼容、双份恢复演练、切换回滚和历史审计保护。
