# Standard v2 Checkpoint Crash/Resume 隔离演练报告

- 日期：2026-08-14
- Gate：`standard_v2_recovery`
- 结果：**通过（隔离 subprocess 故障注入）**
- 数据目录：pytest `tmp_path` 下的独立 `OMC_DATA_ROOT`
- 正式数据：未加载、未恢复、未修改

## 1. 演练目标

验证 standard v2 节点在副作用工具已经完成并写入 durable receipt、但后续 graph step 尚未执行时进程异常退出，重启后能够：

1. 使用同一个 `checkpoint_thread_id` 恢复；
2. 不重复追加原始 `HumanMessage`；
3. 不重放已完成的副作用工具；
4. 继续执行尚未完成的 graph step；
5. 保留 side-effect ledger 的完成状态。

固定 thread：

```text
omc:recovery-project:iter_001:recovery-node:g1
```

## 2. 故障注入

第一阶段创建隔离 standard v2 TaskTree、Runtime SQLite 和 LangGraph graph。graph 在 `side_effect` 节点之后设置 interrupt；副作用和 checkpoint durable 写入后，worker 通过：

```python
os._exit(87)
```

模拟没有 finally/close 的进程死亡。

崩溃前持久化结果：

```json
{
  "messages": 2,
  "checkpoint": true,
  "ledger_status": "completed"
}
```

外部计数器：

```text
side_effect_count=1
finalize_count=0
```

## 3. 重启恢复结果

第二个全新 Python 进程重新打开同一隔离 SQLite，使用相同 thread，并以：

```python
await graph.ainvoke(None, config=config)
```

从 checkpoint 继续，而不是重新提交原始任务。

恢复后验证：

```json
{
  "checkpoint_before": true,
  "human_messages": 1,
  "side_effect_messages": 1,
  "finalize_messages": 1,
  "thread_id": "omc:recovery-project:iter_001:recovery-node:g1"
}
```

最终外部计数器：

```text
side_effect_count=1
finalize_count=1
```

结论：原始任务消息只有一份，副作用只发生一次，未完成的 finalize step 在恢复后完成。

## 4. 自动化证据

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_checkpoint_crash_resume.py \
  --timeout=60
```

该测试也已纳入：

```text
scripts/check-p0-gate.py
→ isolated_recovery_crash_resume_and_reconciliation
```

## 5. 边界

本报告证明**隔离 standard v2 合成节点**的 durable crash/resume 和副作用防重放通过，但不代表以下项目已经完成：

- 对当前正式业务节点执行强制崩溃；
- 重启当前正在运行的真实后端服务；
- 真实云 Provider 调用恢复；
- dispatch、executor started、acceptance 三个真实业务阶段的全链路故障注入；
- 24 小时墙钟演练或正式四人复验。

因此：

```text
formal_24h_launch_allowed=false
```
