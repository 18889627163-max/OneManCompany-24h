# Talent Market MCP/SSE 生命周期修复报告

- 日期：2026-08-14
- 范围：Talent Market MCP SSE 连接、工具调用、keepalive 和关闭
- 结果：**本地回归通过；真实远端连接验证待完成**
- 正式上线：`formal_24h_launch_allowed=false`

## 1. 原始故障

应用运行或关闭过程中出现：

```text
RuntimeError: generator didn't stop after athrow()
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

调用栈经过 `mcp.client.sse.sse_client`、`httpx_sse.aconnect_sse` 和 AnyIO task group/cancel scope。

## 2. 根因

旧实现通过 `AsyncExitStack` 在调用 `connect()` 的 asyncio task 中进入 SSE 和 `ClientSession` async context，但 reconnect、keepalive 或应用 shutdown 可能在另一 task 中调用 `stack.aclose()`。

AnyIO cancel scope 要求由进入它的同一 task 退出。跨 task 保存并关闭 `AsyncExitStack` 因而违反生命周期约束，导致上述 RuntimeError。

## 3. 修复方案

`TalentMarketClient` 改为单一 owner task 模式：

1. 专属 `omc-talent-market-sse-owner` task 创建 SSE 和 `ClientSession`；
2. owner task 完成 session initialize；
3. 工具调用和 keepalive ping 通过内部 command queue 交给 owner task 执行；
4. reconnect/shutdown 只取消并等待 owner task；
5. SSE 和 Session context 始终在 owner task 内退出；
6. connect 等待者被取消时同步取消 owner，避免孤儿连接和无限等待；
7. owner 异常日志只记录异常类型，不输出可能包含远端响应或 URL 细节的异常正文；
8. 保留旧注入 session/stack 的受限兼容路径，不再用于新连接。

`system_cron.talent_market_keepalive()` 同步改为调用公开的：

```python
await talent_market.ping()
```

不再跨 task 直接访问 `talent_market._session.send_ping()`。

## 4. 回归测试

新增 task-bound async context 回归测试，模拟 AnyIO 的同 task 约束。修复前该测试精确失败为：

```text
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

修复后验证：

- SSE context enter/exit 是同一 owner task；
- `ClientSession` context enter/exit 是同一 owner task；
- `call_tool()` 在 owner task 执行；
- `send_ping()` 在 owner task 执行；
- connect 等待者被取消时 owner 被取消，已进入的外层 context 仍在 owner task 中退出；
- keepalive 成功、失败重连和重连失败路径继续可用。

快速回归命令：

```bash
.venv/bin/pytest -q \
  tests/unit/agents/test_recruitment.py::TestTalentMarketClient::test_sse_context_is_entered_and_exited_by_same_owner_task \
  tests/unit/agents/test_recruitment.py::TestTalentMarketClient::test_cancelled_connect_stops_owner_and_closes_entered_context \
  tests/unit/core/test_system_cron.py::TestTalentMarketKeepalive
```

结果：

```text
6 passed in 0.97s
```

相关模块回归：

```text
155 passed in 27.17s
```

完整单元测试：

```text
4628 passed, 2 skipped, 74 warnings in 152.84s
```

仓库全量测试：

```text
4664 passed, 5 skipped, 71 warnings in 155.86s
```

## 5. 边界与后续

本报告证明本地可控 async context 下的生命周期约束和回归测试通过，但不代表以下项目已经完成：

- 真实 Talent Market MCP 远端 SSE 建连、断线和服务关闭验证；
- 网络半开、服务端主动关闭、代理超时等真实故障注入；
- 24 小时墙钟运行；
- 真实云 Provider 429/并发恢复；
- 全新 standard v2 iteration 正式复验。

因此正式 Gate 继续保持关闭：

```text
formal_24h_launch_allowed=false
```
