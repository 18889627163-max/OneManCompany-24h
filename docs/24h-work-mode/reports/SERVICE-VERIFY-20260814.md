# SQLite Memory-enabled 隔离真实服务 Verify 报告

- 日期：2026-08-14
- 服务地址：`127.0.0.1:18082`
- data root：`/tmp/omc-isolated-memory-verify-20260814-103521`（macOS 实际解析为 `/private/tmp/...`）
- 数据库：隔离 data root 下的 `data/runtime.sqlite3`
- 模式：`OMC_MEMORY_ENABLED=true`、`OMC_AUTOMATION_ENABLED=false`、`OMC_RESTORE_PERSISTED_TASKS=false`
- 目的：验证真实 FastAPI 生命周期、SQLite RuntimeStorage、LangGraph checkpoint/store、memory 结构化降级、automation registry、管理备份、readiness 和干净关闭；不恢复或执行正式 TaskTree。

## 1. 结果摘要

| 检查项 | 结果 |
|---|---|
| `/api/health` | 通过 |
| RuntimeStorage | `healthy` |
| Checkpoint store | `healthy` |
| Memory store | `healthy` |
| sqlite-vec | `unavailable`，不阻塞结构化检索 |
| embedding | `degraded`，因未配置云 embedding |
| ProviderGateway | `healthy` |
| Automation registry | `healthy`，注册 13 条 |
| Provider running/queued | `0/0` |
| Memory worker backlog | `0` |
| Checkpoint conflicts | `0` |
| `check-system-ready.sh` | `PASS=35 FAIL=0 WARN=0` |
| SQLite Online Backup | 通过 |
| backup/restore 运维脚本隔离演练 | 通过 |
| API integrity check | `ok` |
| 直接 `PRAGMA integrity_check` | `ok` |
| SQLite page count | `48` |
| 正式目录 `00100` | 不存在 |
| `iter_009` | 哈希前后不变 |
| 服务关闭 | 通过，无 pending task/event loop closed 错误 |

## 2. Health 响应

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
  "oldest_provider_request_at": null,
  "memory_worker_backlog": 0,
  "oldest_memory_event_at": null,
  "checkpoint_conflicts": 0
}
```

该结果证明 embedding 不可用时，服务仍能以结构化 memory 模式启动；不证明 sqlite-vec 和真实云 embedding 已完成生产验证。

## 3. 在线备份

最终备份：

```text
backup_id=service-verify-20260814-final
database_file=runtime-service-verify-20260814-final.sqlite3
manifest_file=runtime-service-verify-20260814-final.sqlite3.manifest.json
sqlite_page_count=48
integrity_check=ok
```

验证中发现相对 `OMC_RUNTIME_BACKUP_DIR=backups/db` 原先按仓库 cwd 解析，会突破 `OMC_DATA_ROOT` 隔离边界。已修复为：

```text
相对 backup dir → OMC_DATA_ROOT / backup dir
绝对 backup dir → 保持显式绝对路径
```

修复后数据库和 manifest 均位于隔离 data root 内；另有单元回归测试验证该行为。`backup-all.sh` 和 `restore.sh` 的默认 backup/database 路径也已改为从 `OMC_DATA_ROOT` 派生。隔离离线演练确认员工/项目 archive 以 `company/...` 为根、数据库 integrity 为 `ok`，并能恢复到独立目标数据库；restore 同时保留旧 `.onemancompany/...` archive 布局兼容。

## 4. 正式数据保护

- `OMC_RESTORE_PERSISTED_TASKS=false`，没有扫描、恢复或推进正式 TaskTree。
- `OMC_AUTOMATION_ENABLED=false`，没有派发正式 automation 业务任务；registry 仍完成 13 条只读注册验证。
- 正式员工运行目录测试后仍不存在 `00100`。
- `iter_009` 内容哈希保持：

```text
fd2b06f7e0525010f5c38ccd122df655436f8722cca285b2b6698d9673dae251
```

## 5. 限制与结论

本次隔离验证通过，但它不是以下项目的替代品：

- 真实 standard v2 TaskTree crash/restart；
- 同一 checkpoint thread resume；
- Provider 429/并发限制 holding/resume；
- 真实云 embedding/sqlite-vec/reindex；
- 24 小时墙钟演练；
- 真机 smoke 与正式 iteration acceptance audit。

结论：**隔离真实服务 Gate 通过；正式 24 小时上线仍被上述真实业务演练阻塞。**
