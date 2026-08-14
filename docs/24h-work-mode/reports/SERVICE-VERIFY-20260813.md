# SQLite 隔离服务 Verify 报告

- 日期：2026-08-13
- 服务地址：`127.0.0.1:18081`
- 数据库：临时路径（未触碰正式运行库）
- 模式：`OMC_MEMORY_ENABLED=false`、`OMC_AUTOMATION_ENABLED=false`
- 目的：验证 RuntimeStorage、checkpoint/store 生命周期、automation 注册、管理备份、readiness 和关闭生命周期，不执行正式业务任务。

## 结果

| 检查项 | 结果 |
|---|---|
| `/api/health` | 通过 |
| `/api/runtime/health` | 通过 |
| RuntimeStorage | `healthy` |
| Checkpoint store | `healthy` |
| Memory store | `disabled`（按隔离验证配置） |
| sqlite-vec / embedding | `disabled`（按隔离验证配置） |
| ProviderGateway | `healthy` |
| Automation registry | `healthy`，注册 13 条 |
| Provider running/queued | `0/0` |
| Memory worker backlog | `0` |
| Checkpoint conflicts | `0` |
| `check-system-ready.sh` | `PASS=35 FAIL=0 WARN=0` |
| 在线 SQLite backup | 通过，`integrity_check=ok` |
| 00100 污染告警 | 未再出现 |
| 12 名正式员工注册 | 通过，包含 00011、00012 |
| 关闭生命周期 | 通过；修复后未再出现 completion consumer pending task |

## 备份接口

使用 `X-OMC-Admin-Token` 调用 `POST /api/admin/runtime/backup`，返回 `status=completed` 和 `integrity_check=ok`。报告不记录 token、API key、密码或记忆原文。

## 关闭修复

初次验证发现 `EmployeeManager._completion_consumer_loop` 会在关闭时偶发遗留。根因是 system cron 等生产者晚于员工循环停止，且 EmployeeManager 缺少 shutdown fence。现已：

1. 先停止 system cron，再停止员工循环；
2. 在 EmployeeManager 增加 `_stopping` 生命周期栅栏；
3. 关闭时取消并等待 running/system tasks；
4. 禁止迟到的 schedule/completion callback 在 teardown 中重建任务；
5. 添加单元回归测试并完成真实服务退出复验。
