# TaskTree / Checkpoint Reconciliation 隔离演练报告

- 日期：2026-08-14
- Gate：`standard_v2_recovery`
- 结果：**通过**
- 数据目录：pytest 临时 projects tree 和独立 Runtime SQLite

## 1. 已实现对账矩阵

| TaskTree | Checkpoint | 处理结果 |
|---|---|---|
| `processing` | 存在且 active | 标记 resumable，保持 processing，不从头执行 |
| `processing` | 不存在 | 转为 `holding`，`hold_reason=checkpoint_missing_controlled_recovery`，`checkpoint_status=missing` |
| `finished` | 仍 active | TaskTree 保持 finished，`checkpoint_status=conflict`，记录 recovery audit |
| node 不存在 | checkpoint 存在 | 记录 orphan，不创建、不恢复 TaskNode |
| system adhoc | checkpoint 存在 | 排除正式 orphan 扫描 |

TaskTree 始终优先于模型 checkpoint。

## 2. 重启生命周期验证

集成测试先用第一个 `RuntimeStorage` 生命周期写入真实 LangGraph checkpoint 并关闭数据库，再用第二个全新 `RuntimeStorage` 生命周期运行 reconciler。

结果：

```text
resumable=1
missing=0
node.status=processing
node.checkpoint_status=active
```

证明 reconciler 可以读取前一服务生命周期留下的 checkpoint，而不是只识别当前进程内状态。

## 3. 冲突、缺失和 orphan 验证

组合场景第一次对账结果：

```text
resumable=1
missing=1
conflicts=1
orphans=1
```

recoveries audit 最终包含：

```text
blocked
conflict
orphan
```

第二次运行 reconciler：

```text
new missing=0
new conflicts=0
recovery audit total=3
```

证明对账自身具备幂等性，不会重复写相同 recovery audit。

## 4. 启动接入

reconciler 已接入 FastAPI lifespan，并在 persisted schedule 恢复之前执行。正式扫描受：

```text
OMC_RESTORE_PERSISTED_TASKS
```

控制；关闭时不扫描、不修改正式 TaskTree。

## 5. 自动化证据

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_checkpoint_reconciler.py \
  tests/unit/core/test_checkpoint_reconciler.py \
  --timeout=60
```

reconciler 测试与 crash/resume、Provider holding/resume 一起纳入 P0 Gate。

## 6. 边界

当前报告证明隔离数据上的状态矩阵和跨 storage lifecycle 对账通过；仍需在**全新专用 standard v2 演练 iteration** 上进行真实服务启动/停止、只读对账和人工审计。不得使用或修改 `iter_009`。
