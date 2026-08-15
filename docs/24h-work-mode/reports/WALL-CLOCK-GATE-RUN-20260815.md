# 24 小时墙钟 Gate 启动记录（2026-08-15）

## 当前判定

```text
status=running
formal_24h_launch_allowed=false
```

本记录只证明 24 小时墙钟 Gate 已完成一致性备份、隔离运行目录创建、真实服务预检和正式计时启动；在完整 86,400 秒结束、四类故障全部通过并生成 `final-report.json` 前，不得标记 Gate 通过。

## 隔离边界

- 正式数据根：仓库 `.onemancompany`，运行期间只读保护；
- 运行根：`backups/24h-runs/wall-clock-20260815T132353`（Git 忽略目录）；
- 隔离数据根：运行根下 `isolated-data`；
- 一致性基线：运行根下 `baseline/runtime.sqlite3`；
- 正式 26 条 Memory Outbox 不导入隔离 live Runtime SQLite；
- 新演练 iteration：`wall-clock-drill-20260815/iter_001`；
- 隔离后端：loopback `127.0.0.1:8015`；
- 自动化：关闭；持久任务恢复：开启；Memory/Checkpoint Store：开启。

## 正式数据基线

启动准备前后以下 SHA-256 一致：

```text
Runtime SQLite
2dfa8af78e3215d43d78b9bf4b2cd6c671c4e69612a6f7f52fd55717b9daa7de

legacy iter_009.yaml
4c8cdb0b84aa5f780ce1c589a504fca8bb2f545093a03e74895b7acfaaa58626

directory iter_009/task_tree.yaml
b3b877e6b584feefe084a40f50a75b7161ae018b42910f9c2e54780e46d087ab
```

正式 Runtime SQLite：`PRAGMA integrity_check=ok`；Memory Outbox 为 `pending=26`、`attempted=0`。

## 预检

1. 加速 public CLI Gate：`prepare → run → status → finalize` 通过；
2. 使用真实 OneManCompany FastAPI 后端完成 8 秒隔离预检；后端启动、重启、SQLite lock、health 恢复和 final checks 全部通过；
3. 真实 Provider 429 sidecar 预检通过；
4. 本地 Ollama `embeddinggemma` 真实探针及 embedding pending/backoff/recovery sidecar 预检通过；
5. 相关回归：`11 passed in 20.17s`。

## 墙钟窗口

- 开始：2026-08-15 13:24:57 Asia/Shanghai；
- 最早结束：2026-08-16 13:24:57 Asia/Shanghai；
- 采样间隔：60 秒；
- 当前 supervisor PID：`31841`；
- 启动后 health：RuntimeStorage、Checkpoint Store、Memory Store、sqlite-vec、Embedding、ProviderGateway、Automation Registry 全部 healthy；Memory Outbox backlog 为 0。
- 2026-08-15 13:27:04，首次启动 shell 退出后 supervisor 进程被宿主回收，但隔离 backend 保持运行；新的独立 session supervisor 从同一 `state.json` 接管 backend，保留原 `started_epoch` 和 2026-08-16 13:24:57 deadline。该事件及 2026-08-15 13:29:11 的代码升级接管均已写入 `wall_clock_supervisor_resumed`/`backend_adopted`；两次接管都保留原始 backend、`started_epoch` 和 deadline，墙钟没有重置。

## 故障计划

| 故障 | 计划偏移 | 预计时间（Asia/Shanghai） | 验收重点 |
|---|---:|---|---|
| Provider 429 | 10,800 秒 | 2026-08-15 16:24:57 | holding、durable backoff、优先级、Memory worker 让位、同 thread 恢复、UI |
| Embedding unavailable | 25,920 秒 | 2026-08-15 20:36:57 | pending/holding/backoff、恢复后同 memory 补向量、业务不 failed |
| Backend restart | 43,200 秒 | 2026-08-16 01:24:57 | 同 checkpoint thread、dispatch/side-effect 不重复、health 恢复 |
| SQLite lock | 64,800 秒 | 2026-08-16 07:24:57 | 锁竞争可观察、进程存活、释放后 integrity/health 恢复 |

## 运维命令

```bash
cd /Users/hanzhen/Downloads/OneManCompany-main
RUN_ROOT=$(cat backups/24h-runs/CURRENT)

.venv/bin/python scripts/run-24h-wall-clock-gate.py status \
  --run-root "$RUN_ROOT"

tail -f "$RUN_ROOT/evidence/events.jsonl"
tail -f "$RUN_ROOT/logs/backend.log"
tail -f "$RUN_ROOT/logs/supervisor.log"
```

如果 supervisor 异常退出但 Gate 尚未标记 failed，可使用同一命令恢复；`started_epoch`、deadline、fault schedule 和 checkpoint thread 均从 `state.json` 恢复。不得删除运行根后从头伪造连续墙钟。

```bash
nohup .venv/bin/python scripts/run-24h-wall-clock-gate.py run \
  --run-root "$RUN_ROOT" \
  --embedding-env-file "$PWD/.env.embedding.local" \
  --fault-duration-seconds 15 \
  --backend-ready-timeout-seconds 120 \
  --sidecar-timeout-seconds 600 \
  > "$RUN_ROOT/logs/supervisor.log" 2>&1 < /dev/null &
```

## 完成后顺序

1. 检查 `final-report.json` 的所有 checks；
2. 再次核对正式 Runtime SQLite、两份 `iter_009` 和 26 条 outbox；
3. 只有真实 86,400 秒 Gate 通过，才进入真机 smoke 和 FFmpeg/FFprobe 取证；
4. 真机证据通过后，创建全新四人 standard v2 iteration，执行显式验收和 Closure Gate。
