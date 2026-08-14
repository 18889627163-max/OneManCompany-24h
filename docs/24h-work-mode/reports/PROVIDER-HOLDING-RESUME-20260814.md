# Provider 429 Durable Holding/Resume 隔离演练报告

- 日期：2026-08-14
- Gate：`standard_v2_recovery`
- 结果：**通过（隔离模拟 429）**
- 数据目录：pytest `tmp_path` 下的独立 `OMC_DATA_ROOT`
- Provider：本地可控 callable；未调用真实付费或云端 Provider

## 1. 演练目标

验证 Provider 请求遇到：

```text
HTTP 429 Concurrency limit exceeded for user
```

时，队列和 retry metadata 在进程退出后仍可恢复，并且恢复调用不会丢失原有 attempt 或重复执行成功 callable。

固定 request：

```text
request_id=provider-recovery-1
priority phase 1=BUSINESS
priority phase 2=RECOVERY
```

## 2. Holding 阶段

第一阶段 Provider callable 抛出模拟 429。Gateway 将请求持久化为 holding 后，worker 通过：

```python
os._exit(88)
```

模拟进程异常终止。

重启前 durable 状态：

```json
{
  "status": "holding",
  "attempt": 1,
  "next_retry_at": "非空时间戳"
}
```

## 3. Resume 阶段

第二个全新 Python 进程重新打开相同隔离 Runtime SQLite，并使用同一个 `request_id` 进入 recovery priority。

恢复结果：

```json
{
  "result": "ok",
  "status": "completed",
  "attempt": 1,
  "next_retry_at": null,
  "retry_attempt": 1
}
```

成功 callable 的外部计数器为：

```text
provider_resume_count=1
```

结论：holding、attempt 和 retry state 跨进程保留；成功后队列进入 completed，`next_retry_at` 清空，恢复 callable 只执行一次。

## 4. 自动化证据

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_provider_holding_resume.py \
  --timeout=60
```

该测试也已纳入 P0 Gate 的隔离 recovery test group。

## 5. 边界

本次 429 来自可控测试 callable，不是云 Provider 的真实 HTTP 响应。因此仍需：

- 使用受控真实 Provider 凭证执行低风险 429/并发故障演练；
- 验证真实 TaskNode 的 `hold_reason`、`next_retry_at` 和恢复 UI；
- 验证 memory worker 在业务请求排队时主动让位；
- 验证长时间 backoff、服务重启和真实网络抖动。

因此：

```text
formal_24h_launch_allowed=false
```
