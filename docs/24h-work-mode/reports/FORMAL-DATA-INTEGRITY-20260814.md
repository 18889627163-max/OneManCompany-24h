# 正式运行数据隔离与完整性报告

- 日期：2026-08-14
- 工作区：`/Users/hanzhen/Downloads/OneManCompany-main`
- 当前结论：**测试隔离通过；并发真实服务写入单独标记**
- 适用范围：正式员工任务记录、正式 projects tree、Runtime SQLite、`iter_009`

## 1. 已修复的历史测试污染入口

| 测试入口 | 原风险 | 修复方式 |
|---|---|---|
| `tests/unit/agents/test_tree_tools_ceo.py` | standalone CEO 测试写入正式 `_adhoc_ceo` TaskTree 和 00001 历史记录 | 使用 `tmp_path` 并 patch `onemancompany.core.config.PROJECTS_DIR` |
| `tests/unit/test_product_triggers.py` | 产品触发测试通过 `project_archive.PROJECTS_DIR` 创建正式 fixture 项目 | 同时 patch `product.PRODUCTS_DIR` 和 `project_archive.PROJECTS_DIR` |
| `tests/unit/core/test_claude_session_coverage.py` | daemon trace fixture 追加到正式 project trace | 同时 patch `claude_session.PROJECTS_DIR` 和 `project_archive.PROJECTS_DIR` |
| integration dispatch 测试 | 创建正式 `employees/00100` | 改为临时员工运行目录 |

历史污染清理审计保存在：

```text
backups/implementation-snapshots/task-record-cleanup-20260814T025141Z/CLEANUP-AUDIT.json
backups/implementation-snapshots/project-test-leakage-cleanup-20260814T025749Z/CLEANUP-AUDIT.json
```

仅处理具有测试证据的 fixture 数据，不清理其他历史项目。

## 2. 早期静态窗口审计

在正式后端没有并发改变受保护文件的早期验证窗口中，曾对 979 个正式运行文件完成前后 hash 对账：

```json
{
  "before": 979,
  "after": 979,
  "added": [],
  "missing": [],
  "modified": []
}
```

该证据证明当时修复后的测试入口不再写入正式目录。

## 3. 本轮 Recovery Gate 的隔离方式

本轮新增恢复演练全部使用 pytest `tmp_path` 和显式独立：

```text
OMC_DATA_ROOT=<temporary>/isolated-omc-data
```

隔离 worker：

```text
tests/integration/recovery_worker.py
tests/integration/provider_recovery_worker.py
```

它们只创建临时：

```text
projects/recovery-project/iterations/iter_001/task_tree.yaml
runtime.sqlite3
side-effect/provider counter files
```

没有加载正式 projects tree，没有连接正式 Runtime SQLite，也没有创建正式员工或修改正式 task history。

## 4. 并发真实服务活动

本轮全量测试和 Recovery Gate 执行期间，发现已有真实后端持续运行：

```text
PID 32891
启动时间：2026-08-14 11:05:55 Asia/Shanghai
命令：/Users/hanzhen/Downloads/OneManCompany-main/.venv/bin/python3 -m onemancompany.main
```

观察到的正式业务写入包括：

```text
00003 conversations/messages.yaml
00003 progress.log
00003 task_history.json
.onemancompany/data/runtime.sqlite3-wal
.onemancompany/data/runtime.sqlite3-shm
```

这些文件的内容和时间与正在运行的 COO 业务活动一致，不能在无维护窗口时归因给测试，也不能使用旧快照覆盖。当前处理原则：

- 不停止、SIGSTOP 或重启 PID 32891；
- 不恢复或覆盖 00003 的业务记录；
- 不删除 Runtime SQLite、WAL 或 SHM；
- 将 WAL/SHM 的持续变化记录为 `concurrent live-service activity`；
- 不在并发写入期间声称正式数据目录“绝对静止”。

从 2026-08-14 12:29 左右的快照到 12:33 的复查中：

```json
{
  "file_count": 1057,
  "added": [],
  "missing": [],
  "modified": [
    ".onemancompany/data/runtime.sqlite3-shm",
    ".onemancompany/data/runtime.sqlite3-wal"
  ]
}
```

这与运行中 SQLite WAL 活动相符。

## 5. `iter_009` 只读保护

P0 Gate 在执行前后计算 `iter_009` 整棵目录内容哈希，结果一致：

```text
fd2b06f7e0525010f5c38ccd122df655436f8722cca285b2b6698d9673dae251
```

`iter_009` 未迁移、未恢复、未修改，也没有用于本轮 crash/resume 或 Provider 演练。

## 6. 最终自动化验证

全量测试：

```text
4662 passed, 5 skipped, 73 warnings in 137.13s
```

隔离 Recovery Gate：

```text
checkpoint crash/resume                         passed
side-effect replay protection                   passed
simulated Provider 429 holding/resume            passed
TaskTree/checkpoint reconciliation               passed
P0 Gate recovery group                          4 passed
```

## 7. 结论边界

当前可确认：

1. recovery tests 的文件路径和数据库均与正式 data root 隔离；
2. P0 Gate 和 recovery workers 没有使用 `iter_009`；
3. `iter_009` hash 不变；
4. 当前观察到的正式 WAL/SHM 和 00003 变化发生在持续运行的真实服务上下文中，未做危险回滚。

当前**不能**在不停真实服务的情况下证明某个长时间窗口内所有正式文件 hash 绝对不变。若上线验收要求严格的前后静态 hash，必须先申请安全维护窗口、让真实服务完成 checkpoint 并干净停止，再运行一次受控审计；不能擅自中断当前业务服务。
