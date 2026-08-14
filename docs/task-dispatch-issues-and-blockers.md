# OneManCompany 任务派发问题与阻塞汇总

> 审计日期：2026-08-12  
> 审计范围：项目 `18b1e9d4a1fc` 的正式任务派发、执行、验收、活动状态展示、运行进程、配置解析及一期业务复验  
> 当前结论：**一期 `FAIL / BLOCKED`**  
> 历史记录原则：`iter_009` 及其失败、自动接受记录原样保留，不做状态回填，不手工修改任务树

## 1. 文档目的与结论标签

本文档用于把当前已经确认的事实、系统缺陷、业务阻塞和待验证问题放入同一份可复核的审计记录，避免再次出现以下误判：

- 把模型输出的“已分配”当成系统已调用 `dispatch_child()`；
- 把手工写入 YAML 或活动日志当成正式派发；
- 把 `started: true` 单字段当成完整执行回执；
- 把系统自动接受当成 COO 已完成显式验收；
- 把子任务结束当成父任务闭环；
- 把验收脚本的局部 `ok=true` 当成一期业务已经通过；
- 把终端窗口退出当成后台服务进程已经退出。

本文使用以下标签：

| 标签 | 含义 |
|---|---|
| **已确认事实** | 有任务树、节点 YAML、执行日志、API 回读、源码或本机命令输出支持 |
| **已满足** | 当前持久化状态或测试结果已经满足该单项条件，但不代表一期整体通过 |
| **当前阻塞** | 已经阻止正式闭环或业务复验继续进行的问题 |
| **设计缺陷** | 当前实现缺少必要状态契约、恢复能力、幂等性或验收约束 |
| **业务环境阻塞** | 设备、工具、服务或项目运行环境尚不满足真实业务验证条件 |
| **待验证** | 已观察到现象或历史记录，但根因、当前复现性或影响范围尚未完整证明 |
| **建议方案** | 后续新 iteration 应实施的修复，不代表当前代码已经具备该能力 |

---

## 2. 执行摘要

### 2.1 当前整体结论

一期当前仍为 **`FAIL / BLOCKED`**，不能验收通过。

`iter_009` 已经完成四名员工的正式派发：四个业务子节点真实创建、落盘、写入员工任务索引、注册调度、由 executor 启动，并且均挂在同一个 COO 父节点下。正式实施路径也已正确绑定为：

```text
/Users/hanzhen/Documents/云测试的项目
```

但是，本轮仍未形成可接受的闭环：

1. COO 父节点 `ae42084c5f4c` 最终状态是 **`failed`**；
2. 失败原因为 `Concurrency limit exceeded for user, please retry later`；
3. 四个业务子节点均由系统自动接受，没有 COO 逐项调用 `accept_child()` 或 `reject_child()` 的审计证据；
4. 真机 smoke 没有实际执行；
5. FFmpeg/FFprobe 当前缺失；
6. `00008` 的“实施路径不存在”结论与同轮其他执行证据及当前实时文件系统状态矛盾；
7. 前端活动数量仍不能完整表达“执行中、待验收、holding、失败、已完成”等不同状态。

### 2.2 验收脚本当前真实能力

正式派发检查脚本 `scripts/check_formal_dispatch_18b1e9d4a1fc.py` 当前已经能够：

- 强制断言 `dispatch_verification.started_by == "executor"`；
- 检查 `started: true` 和非空 `started_at`；
- 检查任务树、员工 `task_index.yaml`、任务树 API 和员工 taskboard API；
- 检查四个节点是否共享同一直接父节点；
- 检查父节点是否为失败类状态；
- 检查实施路径是否精确匹配。

因此，“脚本完全没有校验 `started_by`”已不符合当前代码。真实 `iter_009` 检查结果为 `ok=false`，退出码为 `1`，脚本正确识别到 COO 父节点失败。

但脚本仍需增强：父节点检查不应依赖四份子节点 receipt 全部先通过；`started_at` 应校验时间格式；父状态应使用显式允许集合而非仅使用失败黑名单；派发成功和业务闭环应拆成独立的 **dispatch gate** 与 **closure gate**。

---

## 3. 关键对象与当前状态

### 3.1 项目与 iteration

| 对象 | 值 | 审计结论 |
|---|---|---|
| 项目 ID | `18b1e9d4a1fc` | 已确认 |
| 当前审计 iteration | `iter_009` | 已确认 |
| COO 协调父节点 | `ae42084c5f4c` | 已确认，状态为 `failed` |
| COO 父节点的上级 | `af041c5f4456` | 已确认 |
| Review 节点 | `a750a118c28d` | 已确认，状态为 `finished`，但未形成显式验收 |
| 唯一正式实施路径 | `/Users/hanzhen/Documents/云测试的项目` | 节点绑定正确；当前目录实际存在 |
| 一期结论 | `FAIL / BLOCKED` | 不得改写为 PASS |

主要持久化证据：

```text
.onemancompany/company/business/projects/18b1e9d4a1fc/
└── iterations/iter_009/
    ├── task_tree.yaml
    └── nodes/
        ├── ae42084c5f4c/execution.log
        └── a750a118c28d/execution.log
```

### 3.2 COO 父节点

| 字段 | 持久化值 |
|---|---|
| `id` | `ae42084c5f4c` |
| `employee_id` | `00003` |
| `parent_id` | `af041c5f4456` |
| `project_id` | `18b1e9d4a1fc/iter_009` |
| `status` | `failed` |
| `implementation_path` | `/Users/hanzhen/Documents/云测试的项目` |
| `started` | `true` |
| `started_by` | `executor` |
| `started_at` | `2026-08-12T15:11:48.248408` |
| `completed_at` | `2026-08-12T15:24:16.395620` |

父节点的 `children_ids` 中包括四个业务子节点和一个 review 节点：

```text
62967c7f3106
d162b8587059
a750a118c28d  # review
9fc76099019e
b85fb4d98be4
```

父节点失败原因记录在执行日志中，而不是通过手工修改任务树补写：

```text
Concurrency limit exceeded for user, please retry later
```

执行器已进行两次延迟重试：

| 时间 | 结果 | 下一次等待 |
|---|---|---:|
| `2026-08-12T15:21:54.860834` | Attempt 1 failed | 5 秒 |
| `2026-08-12T15:22:31.112307` | Attempt 2 failed | 15 秒 |
| `2026-08-12T15:24:16.396046` | 最终失败 | 不再重试 |

### 3.3 四个正式业务子节点

以下四个节点均是 `ae42084c5f4c` 的直接子节点：

| 员工 | node_id | 任务 | 状态 | `started_at` | 验收记录 |
|---|---|---|---|---|---|
| `00006` | `62967c7f3106` | 修复 orphans 脚本链路 | `finished` | `2026-08-12T15:14:29.845579` | 系统自动接受 |
| `00007` | `d162b8587059` | 梳理一期 E2E 场景 | `finished` | `2026-08-12T15:16:37.847235` | 系统自动接受 |
| `00008` | `9fc76099019e` | 准备迁移后真机 smoke | `finished` | `2026-08-12T15:19:33.726629` | 系统自动接受 |
| `00009` | `b85fb4d98be4` | 验证 Mate X5 设备准备 | `finished` | `2026-08-12T15:21:19.275450` | 系统自动接受 |

四个节点均满足以下正式派发持久化字段：

```yaml
dispatch_verification:
  dispatch_child_called: true
  task_tree_node_created: true
  task_tree_persisted: true
  task_index_written: true
  schedule_node_called: true
  schedule_registered: true
  verified: true
  started: true
  started_by: executor
  started_at: <非空时间>
```

四个节点的 `implementation_path` 均为：

```text
/Users/hanzhen/Documents/云测试的项目
```

截至 2026-08-12 本次核验时，以下 API 均可返回对应正式节点：

```text
GET /api/employee/00006/taskboard → 62967c7f3106
GET /api/employee/00007/taskboard → d162b8587059
GET /api/employee/00008/taskboard → 9fc76099019e
GET /api/employee/00009/taskboard → b85fb4d98be4
```

### 3.4 Review 节点和自动接受

Review 节点状态：

| 字段 | 值 |
|---|---|
| `id` | `a750a118c28d` |
| `employee_id` | `00003` |
| `parent_id` | `ae42084c5f4c` |
| `node_type` | `review` |
| `status` | `finished` |
| `completed_at` | `2026-08-12T15:25:17.085817` |

Review 执行日志中没有真实调用 `accept_child()` 或 `reject_child()`。四个子节点最终都被写入：

```yaml
acceptance_result:
  passed: true
  notes: "Auto-accepted: review completed without explicit accept/reject."
```

该行为来自 `src/onemancompany/core/vessel.py` 中“review 结束后自动接受 orphaned COMPLETED children”的逻辑。它只能说明系统进行了兜底状态推进，**不能证明 COO 阅读了交付物并完成逐项验收**。

因此，`iter_009` 的四条自动接受记录统一归类为：

```text
legacy/unverified
```

---

## 4. 问题与阻塞矩阵

| 优先级 | 分类 | 问题 | 当前状态 | 主要影响 |
|---|---|---|---|---|
| P0 | 当前阻塞 | COO 父节点并发失败 | 未解决 | 正式协调任务无法闭环 |
| P0 | 当前阻塞 / 设计缺陷 | 显式验收未完成，standard 模式自动接受 | 未解决 | 形成“看起来通过”的假闭环 |
| P0 | 设计缺陷 | 缺少受控失败节点恢复接口 | 未解决 | 只能新建节点或冒险手改状态 |
| P0 | 设计缺陷 | 派发重试缺少业务幂等键 | 未解决 | 父 Agent 整体重试可能重复派发 |
| P1 | 设计缺陷 | checker 的父节点、时间和 closure 检查不足 | 部分已修复 | 仍存在边界条件假绿风险 |
| P1 | 设计缺陷 | 前端活动数量和运行状态契约不完整 | 未解决 | 用户难以区分执行、待验收、阻塞和结束 |
| P1 | 业务环境阻塞 | FFmpeg/FFprobe 缺失 | 未解决 | 部分媒体链路与 smoke 无法完整执行 |
| P1 | 业务环境阻塞 | 迁移后真机 smoke 未实际执行 | 未解决 | 一期缺少真实端到端证据 |
| P1 | 待验证 | `00008` 路径不存在结论与其他证据冲突 | 未解决 | 同一 iteration 内证据不一致 |
| P1 | 设计缺陷 | `update_project_team()` 项目路径解析错误 | 未解决 | 团队元数据无法更新 |
| P1 | 历史数据问题 | 实施路径曾出现 `cloud-test-platform` 冲突 | 未清理，保留审计 | 容易在后续复验中进入错误工作树 |
| P2 | 运维设计缺陷 | 终端、包装进程和监听进程生命周期易误判 | 未解决 | 可能重复启动或误以为服务已退出 |
| P2 | 待验证 | OpenAI `ls` 工具 schema 非法 | 已观察，待复现 | Agent 调用在请求阶段直接失败 |
| P2 | 配置缺陷 | 员工 `00100` 配置字段缺失 | 未解决 | 配置加载时跳过该员工 |
| P2 | 配置兼容性 | 离职员工 `00010` 使用 Python tuple YAML tag | 未解决 | safe loader 无法解析，存在兼容风险 |

---

## 5. P0 问题详述

### 5.1 COO 父节点失败

**分类：已确认事实 / 当前阻塞**

父节点 `ae42084c5f4c` 在四个子任务已创建后，因为用户级并发限制失败。执行器先后进行了 5 秒、15 秒重试，最终仍进入 `failed`。

**影响：**

- 四个子节点的派发事实仍然有效，但父任务不能被认定为成功；
- 父节点无法继续承担可靠的协调、复核和向上汇报职责；
- 若直接从头重跑整个父 Agent，可能重新执行派发逻辑并创建重复子节点；
- 验收脚本必须继续返回失败，不能忽略父节点状态。

**禁止操作：**

- 不得把 `task_tree.yaml` 中的 `failed` 手工改成 `finished`、`accepted` 或其他成功状态；
- 不得仅凭四个子节点 `finished` 推导父节点成功；
- 不得仅凭 checker 的部分检查通过忽略父节点失败。

**建议方案：**

1. 在调度前申请或等待并发槽位，不要在 LLM 调用已经开始后才被动失败；
2. 对并发限流使用带抖动的指数退避，并把节点转入可恢复的 `holding`/`retry_wait` 状态；
3. 对同一 `node_id` 增加节点级单实例执行锁；
4. 超过自动重试上限后创建人工升级事件，不直接重跑整段父 Agent；
5. 为后续新节点提供受控节点级恢复接口。

**验收标准：**

- 新 COO 父节点不会因短暂并发限流直接进入不可恢复失败；
- 重试期间任务状态、下一次重试时间、累计次数和最后错误可通过 API 查询；
- 任一时刻同一节点至多有一个 executor 实例；
- 重试不会重复创建已经成功派发的业务子节点。

### 5.2 显式验收未完成

**分类：已确认事实 / 当前阻塞 / 设计缺陷**

`a750a118c28d` 没有逐项调用 `accept_child()` 或 `reject_child()`，但系统自动把四个 `COMPLETED` 子节点推进为 `ACCEPTED`/`FINISHED`。

**影响：**

- `acceptance_result.passed=true` 不再能单独证明主管验收；
- 业务失败或证据不足可能被自动推进为成功终态；
- 父节点和前端可能呈现“已完成”，但审计链没有验收人、验收动作和判断依据；
- `00008` 路径证据矛盾、`00009` 未执行 smoke 等问题没有被 review 正式拒绝或升级。

**建议方案：**

- 对后续新 iteration 的 standard 模式禁止自动接受业务子节点；
- review 必须为每个业务子节点写入显式决策记录，至少包含：

```yaml
acceptance_audit:
  decision: accepted | rejected
  decided_by: "00003"
  decided_via: accept_child | reject_child
  review_node_id: <review node id>
  decided_at: <ISO-8601 timestamp>
  criteria_results: [...]
  evidence_refs: [...]
  notes: <非空说明>
```

- Review 最多自动重试 2 次；仍没有显式决策时创建人工升级节点，不得自动接受；
- 历史自动接受记录保持原状，但在 closure gate 中视为 `legacy/unverified`。

**验收标准：**

- 每个业务子节点都有唯一、可追溯的 `accept_child()` 或 `reject_child()` 事件；
- 没有显式验收的节点不得通过 closure gate；
- Review 结束但未调用验收工具时，系统进入人工升级而不是自动成功。

### 5.3 缺少安全恢复机制

**分类：已确认现象 / 设计缺陷**

普通 1 对 1 会话调用正式节点工具时会返回 `No agent context`。现有工具依赖 `_current_vessel` 和 `_current_task_id`，因此不能在脱离正式 executor 上下文的聊天会话里安全恢复 `ae42084c5f4c`。

系统目前也没有经过权限控制、审计和状态机校验的 `retry_failed_node()` 正式接口。

**影响：**

- 运维人员无法通过受控方式恢复单一失败节点；
- 容易诱发手改 YAML、复用旧节点或重新启动整个父任务等高风险操作；
- 失败恢复过程缺乏操作者、原因、前置状态和结果审计。

**建议接口：**

```text
retry_failed_node(
  project_id,
  iteration_id,
  node_id,
  expected_status="failed",
  retry_mode="resume_from_checkpoint",
  reason,
  idempotency_key
)
```

接口至少应验证：目标节点属于指定 iteration；当前状态允许恢复；没有活动 executor；父子关系未变化；已有派发步骤可从 checkpoint 跳过；调用者有恢复权限；每次操作写入不可变审计事件。

**验收标准：**

- 无需编辑 YAML 即可恢复允许恢复的失败节点；
- 同一恢复请求重复提交不会启动多个 executor；
- 恢复前后状态、操作者、原因、checkpoint 和结果均可回读；
- 不允许把业务失败直接改写成成功。

### 5.4 派发重试缺少幂等性

**分类：设计缺陷 / 高风险**

当前并发错误发生在父 Agent 执行过程中。若恢复策略是重新运行父 Agent 的完整提示，模型可能再次执行 `dispatch_child()`，从而为同一员工和同一业务目标创建新节点。

**建议方案：**

为业务派发引入稳定幂等键：

```text
dispatch_key = parent_id + employee_id + task_key
```

其中 `task_key` 是调用方提供的稳定业务键，例如：

```text
phase1:orphans-fix
phase1:e2e-checklist
phase1:post-migration-smoke
phase1:mate-x5-readiness
```

调度存储层应对 `(parent_id, employee_id, task_key)` 建立唯一约束。重复请求应返回原节点及原 dispatch receipt，而不是创建新节点。

**验收标准：**

- 同一幂等键连续或并发调用多次，只产生一个正式 node_id；
- 第一次调用在“节点已落盘、响应未返回”时中断，重试仍能回读原节点；
- 不同 iteration 或不同正式父节点使用新的幂等域，不复用历史节点。

---

## 6. P1 问题详述

### 6.1 正式验收脚本仍需增强

**分类：部分已满足 / 设计缺陷**

当前 checker 已经不再是此前所说的“完全假绿”：它能强制检查 `started_by=executor` 并拒绝父节点 `failed`。真实 `iter_009` 返回失败即是证据。

仍存在以下边界风险：

1. **父节点检查顺序耦合。** 当前只有在四个子节点 receipt 全部有效后才继续检查共同父节点。若子节点先失败，报告可能遗漏同时存在的父节点错误。
2. **`started_at` 只检查非空。** 任意非空字符串都可能通过，尚未验证 ISO-8601 格式、时区策略和时间合理性。
3. **父状态采用失败黑名单。** 空状态、拼写错误或未来新增的未知状态可能绕过；应使用显式允许集合。
4. **派发与闭环混在一个结论中。** “正式派发成功”与“业务执行及验收成功”是两个不同 gate。
5. **缺少显式验收断言。** 当前 checker 主要验证派发链，不足以证明每个业务子节点由主管显式接受或拒绝。

**建议拆分：**

#### Dispatch gate

负责验证：

- 新正式 node_id 格式正确，且不属于拒绝复用集合；
- 四个节点位于目标 iteration；
- 四个节点属于同一正式父节点的直接 children；
- `dispatch_child_called`、任务树落盘、员工索引写入、调度注册均为真；
- `started=true`、`started_by=executor`；
- `started_at` 是可解析的 ISO-8601 时间；
- 项目树 API 和员工 taskboard API 均可回读；
- 父、子节点的实施路径精确一致；
- 父状态属于明确允许的任务状态枚举，未知/空状态直接失败。

#### Closure gate

负责验证：

- 父节点没有失败、阻塞、取消、未知或手工改写状态；
- 每个业务子节点都有显式验收审计；
- `decided_via` 必须是 `accept_child` 或 `reject_child`；
- 自动接受、空验收、legacy 验收不能作为通过依据；
- 所有必需交付物和证据可读取；
- 没有未解决 review、holding、failed 或人工升级节点；
- 最终父节点达到状态机允许的闭环状态。

### 6.2 前端活动数量和运行状态契约不完整

**分类：已确认事实 / 设计缺陷**

当前源码中的真实刷新链路是：

1. 前端初始加载调用 `GET /api/bootstrap`；
2. 后端 `sync_tick.py` 周期性通过 WebSocket 广播：

   ```json
   {"type": "state_changed", "changed": ["employees", "active_tasks"]}
   ```

3. 前端收到事件后按分类刷新 `/api/employees`、`/api/state` 等接口；
4. 动画逻辑主要依据员工运行时的 `current_task` 或 `status === "working"`；
5. `activity_log` 用于历史活动展示，不驱动员工的 working 动画。

因此，先前“网页每 3 秒通过 `GET /api/sync` 读取 profile”的描述不符合当前代码：当前没有该 GET 路由，3 秒周期在后端同步 tick 与 WebSocket 状态变更通知中体现。

截至本次核验：

```text
GET /api/state → version 0.7.110
active_tasks → 0
员工运行时状态 → 当前均为 idle
```

在当前契约下，任务完成或失败后不再计入 `active_tasks`，员工回到 `idle` 是可解释的；但 UI 无法因此表达“已经完成但待验收”“父节点失败”“业务仍阻塞”等项目活动。

**建议统一状态聚合：**

```json
{
  "activity_counts": {
    "running": 0,
    "holding": 0,
    "awaiting_review": 0,
    "blocked": 0,
    "failed": 0,
    "completed_unclosed": 0
  },
  "active_total": 0,
  "attention_total": 0
}
```

- `active_total` 只表示正在消耗执行资源的节点；
- `attention_total` 表示需要人工处理或尚未闭环的节点；
- 动画、任务面板和项目总览必须使用同一后端聚合定义；
- 前端不能自行使用不同状态集合计算“活动数量”。

**验收标准：**

- API、动画面板和任务总览对同一任务树给出一致计数；
- `failed` 父节点即使员工为 `idle`，仍显示为需要处理；
- 完成但待显式验收的节点显示为 `awaiting_review`，不计为正在执行，也不消失。

### 6.3 业务执行仍有真实阻塞

**分类：业务环境阻塞 / 当前阻塞**

#### FFmpeg/FFprobe 缺失

截至 2026-08-12 本次本机核验，以下命令均无可执行文件路径输出：

```bash
command -v ffmpeg
command -v ffprobe
```

在需要媒体探测、视频证据或相关 smoke 链路时，该缺失会直接阻止完整验证。

#### 真机 smoke 未执行

`iter_009_huawei_mate_x5_device_readiness_report.md` 明确限定为设备发现、只读属性查询和运行前检查，并明确写明“未执行 smoke 用例”。因此：

```text
Mate X5 设备准备 PASS ≠ 迁移后真机 smoke PASS
```

当前 ADB 实时检查能够看到 Mate X5 对应设备在线：

```text
192.168.101.112:5555
product: ALT-AL10E
model: ALT_AL10
device: HWALT-B
```

设备在线只能解除“设备不可发现”这一项前置阻塞，不能替代应用安装、服务启动、任务执行、证据采集和退出码验证。

#### `00008` 路径证据矛盾

`00008` 的阻塞报告声称：

```text
/Users/hanzhen/Documents/云测试的项目 不存在
```

但同轮 `00007`、`00006` 的交付记录显示已从该路径读取或验证项目内容，且本次实时 `test -d` 结果确认目录现在存在。

该矛盾不能通过选择其中一份报告直接消除。可能涉及执行时点差异、隔离环境、路径可见性或任务上下文差异，当前统一标记为 **待验证**，必须在新 iteration 中由 executor 记录同一时间窗口的：

```bash
pwd
test -d '/Users/hanzhen/Documents/云测试的项目'
stat '/Users/hanzhen/Documents/云测试的项目'
```

并在报告中记录时间、主机/执行环境和退出码。

#### 服务未就绪

截至本次核验，没有发现端口 `3001` 和 `5174` 的监听进程。后续 smoke 开始前应先按唯一实施路径确认正式启动命令、进程 cwd、端口、数据目录和健康检查，不能复用历史进程或相近目录的服务。

### 6.4 项目元数据与实施路径异常

**分类：已确认事实 / 设计缺陷 / 历史数据问题**

父节点调用 `update_project_team()` 时返回：

```text
project.yaml not found.
```

项目根目录实际存在：

```text
.onemancompany/company/business/projects/18b1e9d4a1fc/project.yaml
```

当前直接原因可以由节点数据和源码共同解释：

- 父节点 `task.project_dir` 指向 `.../iterations/iter_009`；
- `update_project_team()` 使用 `Path(task.project_dir) / "project.yaml"`；
- 因而工具查找的是 iteration 目录下不存在的 `project.yaml`，而不是项目根目录文件。

**建议方案：**

- 任务上下文明确区分 `project_root`、`iteration_dir` 和 `implementation_path`；
- `update_project_team()` 只能使用经过解析和校验的 `project_root`；
- 工具返回中包含最终解析路径，便于审计；
- 增加“节点位于 iteration 目录时仍能正确更新项目根 YAML”的集成测试。

历史交付物中还出现：

```text
/Users/hanzhen/Documents/cloud-test-platform
```

例如 `phase1_e2e_scenario_checklist.md` 和 `post_migration_device_smoke_preparation.md`。这些记录与本轮锁定的唯一实施路径冲突。历史文档保留审计，但后续新 iteration 的正式节点、命令 cwd、证据路径和验收脚本必须全部使用：

```text
/Users/hanzhen/Documents/云测试的项目
```

---

## 7. P2 运行与配置问题

### 7.1 终端退出不代表后端退出

**分类：已确认事实 / 运维设计缺陷**

截至 `2026-08-12 17:59 CST` 的瞬时核验：

| PID | 命令 | 是否监听 8000 |
|---:|---|---|
| `96607` | `.venv/bin/python3 .venv/bin/onemancompany` | 是 |
| `40317` | `npm exec @1mancompany/onemancompany` | 否 |

所以当前不是“两套后端同时监听 8000”，而是：

- 一个真实 Python 后端继续监听；
- 一个旧 npm 包装进程仍存在，但没有监听 8000；
- 终端 UI 或父 shell 退出，不一定会终止已经脱离前台、被包装器托管或仍由其他终端会话持有的子进程。

PID 是瞬时信息，后续不得把上述 PID 写死到停止脚本，也不得使用 `killall python`、`killall node` 等宽泛命令。

**建议方案：**

- 启动时写入 PID 文件和实例 UUID；
- 对端口和数据目录增加单实例锁；
- `/api/health` 返回 PID、实例 UUID、启动时间、版本、cwd、项目数据目录；
- `stop` 命令先核验 PID、实例 UUID 和进程命令行，再发送温和终止信号；
- npm 包装进程负责转发信号并等待子进程退出；
- 启动前检查已有健康实例，避免重复启动。

### 7.2 OpenAI `ls` 工具 schema 异常

**分类：已观察运行错误 / 待验证**

用户提供的运行日志多次出现：

```text
openai.BadRequestError: Invalid schema for function 'ls':
null is not of type "array"
```

该错误说明发送给模型 API 的 `ls` 工具 JSON Schema 在某个要求为 array 的位置包含了 `null`。当前仓库日志检索没有形成完整的生成链路证据，因此根因和当前版本是否仍稳定复现应标记为 **待验证**。

建议捕获最终发送给 provider 的工具 schema，使用 JSON Schema validator 做启动期校验，并添加 provider 兼容性测试；无效 schema 应在本地报出工具名和具体字段路径，而不是发送后才收到 400。

### 7.3 员工 `00100` 配置损坏

**分类：已确认事实 / 配置缺陷**

`.onemancompany/logs/manual-restart-2026-08-12.log` 记录 `00100` 的 `EmployeeConfig` 缺少必填字段：

```text
name
role
skills
```

加载器因此跳过该 profile。修复时应补齐合法字段或移除无效测试配置，并增加配置目录启动前校验，避免同一错误在日志中重复刷屏。

### 7.4 离职员工 `00010` YAML 兼容问题

**分类：已确认事实 / 配置兼容性**

以下文件包含 Python 专用 YAML tag：

```text
.onemancompany/company/human_resource/ex-employees/00010/profile.yaml
desk_position: !!python/tuple
```

安全 YAML loader 报错：

```text
could not determine a constructor for the tag 'tag:yaml.org,2002:python/tuple'
```

应把持久化格式迁移为普通数组，例如：

```yaml
desk_position: [3, 5]
```

迁移应通过独立脚本完成并保留备份，不能通过启用不安全的 Python 对象反序列化来规避错误。

---

## 8. 已满足项

以下单项已有持久化状态、API 或测试结果支持，但**不构成一期整体 PASS**：

1. `iter_009` 的四个业务子节点均为新生成的 12 位正式 node_id；
2. 四个业务子节点均属于同一 COO 父节点 `ae42084c5f4c`；
3. 四个节点均在父节点的 `children_ids` 中；
4. 四个节点均绑定精确实施路径 `/Users/hanzhen/Documents/云测试的项目`；
5. 四个节点均有完整派发、落盘、员工索引、调度注册和回读标记；
6. 四个节点均有 `started=true`、`started_by=executor` 和非空 `started_at`；
7. 四名员工的 taskboard API 均能返回对应正式节点；
8. `00006` 的 orphans 脚本专项报告记录了 `8/8` 断言通过和退出码 `0`；
9. Mate X5 对应 ADB 设备当前可发现并在线；
10. 当前正式 checker 对真实 `iter_009` 返回 `ok=false`、退出码 `1`，没有把失败父节点判绿；
11. checker 的现有 integration tests 基线为 `5 passed`。

---

## 9. 历史审计边界

以下边界对 `iter_009` 及后续修复均为强制要求：

1. `iter_009`、父节点 `ae42084c5f4c` 和自动接受记录永久保留原状；
2. 不把父节点 `failed` 手工改写为成功；
3. 不把四个自动接受记录改写成 COO 显式验收；
4. 不复用旧节点 `0515ed131b56`；
5. 不复用任何已取消的 `task_*` 节点；
6. 不复用旧 iteration 的业务节点；
7. 不把后台任务 ID、应用任务 ID、agent ID 或会话 ID冒充正式 node_id；
8. 不通过手工修改 `task_tree.yaml`、员工 `task_index.yaml` 或 `profile.yaml.current_task` 制造执行假象；
9. 历史自动接受统一标记为 `legacy/unverified`，新规则只对后续新 iteration 强制执行；
10. 新复验必须创建新的 iteration、新 COO 父节点和四个全新直接业务子节点。

---

## 10. 分阶段修复路线

### 第一阶段：强化 checker，拆分 gate

1. 把父节点存在性、状态、iteration 和路径检查移到独立流程，不依赖四个 receipt 全部成功；
2. 为 `started_at` 增加严格 ISO-8601 解析和合理性检查；
3. 对父、子节点状态使用显式允许集合，未知、空值和拼写错误全部失败；
4. 保留现有 `started_by == "executor"` 强制断言；
5. 新增 dispatch gate 和 closure gate 两份机器可读结果；
6. closure gate 强制检查显式验收审计，拒绝 `Auto-accepted` 和 `legacy/unverified`；
7. 为每一个失败分支增加 integration test。

### 第二阶段：修正 review 状态机

1. standard 模式禁止自动接受业务子节点；
2. Review 必须逐项调用 `accept_child()` 或 `reject_child()`；
3. Review 最多自动重试 2 次；
4. 两次后仍无显式决策，创建人工升级节点并保持父任务未闭环；
5. 显式验收事件写入不可变审计日志。

### 第三阶段：治理并发与执行恢复

1. 增加用户级并发槽位查询和预占；
2. 限流时采用指数退避与抖动；
3. 将可恢复限流放入 `holding`/`retry_wait`，记录 `next_retry_at`；
4. 增加节点级单实例锁；
5. executor 从 checkpoint 恢复，不重复已成功的工具调用。

### 第四阶段：增加受控恢复接口与派发幂等键

1. 实现受权限、状态机和审计保护的 `retry_failed_node()`；
2. 为 `dispatch_child()` 增加 `(parent_id, employee_id, task_key)` 幂等约束；
3. 重复派发返回原 node_id 和 receipt；
4. 增加“落盘成功但响应丢失”的故障注入测试。

### 第五阶段：创建全新的正式复验树

1. 创建新 iteration；
2. 创建新的 COO 正式父节点；
3. 分别为 `00006`、`00007`、`00008`、`00009` 创建四个全新直接子节点；
4. 四个节点统一绑定 `/Users/hanzhen/Documents/云测试的项目`；
5. 每次派发后通过节点详情、员工索引和 API 回读正式 receipt；
6. 不复用 `iter_009` 的任何 node_id。

### 第六阶段：解除业务环境阻塞并由 QA 复验

1. 安装并确认 FFmpeg/FFprobe 版本；
2. 核验唯一实施路径、正式启动命令、服务 cwd、端口和数据目录；
3. 锁定获授权的真机设备；
4. 执行真实迁移后 smoke；
5. 保存命令、设备序列号、开始/结束时间、日志、退出码、截图/视频及产物路径；
6. 由 QA 根据预先定义的验收标准显式接受或拒绝。

### 第七阶段：修复可观测性、进程和配置问题

1. 统一活动状态 API 与前端计数；
2. 增加 PID 文件、实例锁和增强健康接口；
3. 修复 `update_project_team()` 的项目根路径解析；
4. 修复 `00100` 配置；
5. 迁移 `00010` 的 Python tuple YAML；
6. 校验并修复 OpenAI 工具 schema 生成链。

---

## 11. 最终验收标准

只有以下条件全部满足，一期才可以从 `FAIL / BLOCKED` 进入可验收状态：

### 11.1 正式任务树

- [ ] 使用新 iteration，不修改 `iter_009`；
- [ ] 创建新的 COO 父节点，状态不是 `failed`、`blocked`、`cancelled`、空值或未知状态；
- [ ] 父节点状态来自 executor 和正式状态机，不是手工修改；
- [ ] `00006`、`00007`、`00008`、`00009` 各有一个全新正式节点；
- [ ] 四个节点是同一 COO 父节点的直接 children；
- [ ] 不复用 `0515ed131b56`、`task_*` 或旧 iteration 节点；
- [ ] 父、子节点均绑定精确路径 `/Users/hanzhen/Documents/云测试的项目`。

### 11.2 派发与启动回执

- [ ] 每个节点有 `dispatch_child_called=true`；
- [ ] 每个节点已写入 `task_tree.yaml` 和员工 `task_index.yaml`；
- [ ] 每个节点有 `schedule_node_called=true` 和调度注册证据；
- [ ] 每个节点可从任务树 API 和员工 taskboard API 回读；
- [ ] 每个节点有 `started=true`；
- [ ] 每个节点有 `started_by=executor`；
- [ ] 每个 `started_at` 都是合法、可解析且合理的 ISO-8601 时间；
- [ ] 并发错误重试不会重复创建节点。

### 11.3 显式验收闭环

- [ ] 每个业务子节点都有 COO/QA 的显式 `accept_child()` 或 `reject_child()` 审计事件；
- [ ] 自动接受不计入通过；
- [ ] 验收事件包含验收人、review node、时间、标准结果、证据和说明；
- [ ] Review 未决时进入人工升级，不自动完成父任务；
- [ ] dispatch gate 与 closure gate 均通过。

### 11.4 业务证据

- [ ] FFmpeg/FFprobe 前置依赖已满足，或有经批准且不影响验收的替代方案；
- [ ] 真机 smoke 使用获授权且唯一标识的设备；
- [ ] 真实记录执行命令、服务地址、设备序列号、开始/结束时间和退出码；
- [ ] 保存可重读的服务日志、ADB 输出、截图/视频和产物；
- [ ] Mate X5 “设备准备通过”与“smoke 通过”分开判定；
- [ ] `00008` 路径矛盾已在同一执行环境和时间窗口重新核验；
- [ ] QA 根据新证据显式给出通过或拒绝结论。

### 11.5 API 与前端

- [ ] 后端提供统一的活动状态分类和计数；
- [ ] 前端不自行使用不同状态集合计算数量；
- [ ] 员工 `idle` 时，失败父节点或待验收任务仍能显示为需要处理；
- [ ] WebSocket 刷新后，任务面板、动画面板和项目总览状态一致。

---

## 12. 证据索引与复验命令

### 12.1 主要证据文件

```text
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/task_tree.yaml
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/nodes/ae42084c5f4c/execution.log
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/nodes/a750a118c28d/execution.log
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/iter_009_post_migration_device_smoke_preparation_blocker.md
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/iter_009_huawei_mate_x5_device_readiness_report.md
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/iter_009_phase1_e2e_scenario_checklist.md
.onemancompany/company/business/projects/18b1e9d4a1fc/iterations/iter_009/orphans_script_chain_fix_report.md
.onemancompany/logs/manual-restart-2026-08-12.log
scripts/check_formal_dispatch_18b1e9d4a1fc.py
tests/integration/test_formal_dispatch_checker.py
src/onemancompany/core/vessel.py
src/onemancompany/core/sync_tick.py
src/onemancompany/agents/common_tools.py
src/onemancompany/agents/tree_tools.py
frontend/app.js
frontend/office.js
```

### 12.2 正式 checker

```bash
.venv/bin/python scripts/check_formal_dispatch_18b1e9d4a1fc.py \
  --iteration iter_009 \
  --implementation-path '/Users/hanzhen/Documents/云测试的项目'
```

当前预期结果：

```json
{
  "ok": false,
  "project_id": "18b1e9d4a1fc",
  "iteration_id": "iter_009",
  "errors": [
    "formal parent has terminal failure status 'failed': ae42084c5f4c"
  ]
}
```

预期退出码：

```text
1
```

### 12.3 Integration test 基线

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_formal_dispatch_checker.py
```

当前基线：

```text
5 passed
```

### 12.4 环境复核

```bash
test -d '/Users/hanzhen/Documents/云测试的项目'
command -v ffmpeg
command -v ffprobe
adb devices -l
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:3001 -sTCP:LISTEN
lsof -nP -iTCP:5174 -sTCP:LISTEN
```

---

## 13. 待验证问题

以下问题已有现象，但在关闭前仍需补充证据：

1. `00008` 执行时为何看不到当前实际存在的实施路径：需要核对执行容器、主机、cwd、权限、执行时间和挂载视图；
2. OpenAI `ls` schema 中哪个字段被生成成 `null`：需要保存最终请求 schema 并做最小复现；
3. 并发限流的真实配额维度：需要确认是用户、provider、模型、公司实例还是 executor 池限制；
4. 当前重试是否会在所有失败位置从头重跑工具调用：需要故障注入测试，而不是只依据日志推测；
5. 旧 npm 包装进程为何未随原终端退出：需要核对进程树、信号转发和启动脚本；
6. 历史 `cloud-test-platform` 与当前唯一实施路径之间的业务迁移关系：需要由项目负责人给出正式归档/废弃边界；
7. 后续 smoke 所需的正式服务端口、数据目录和启动授权：新 iteration 开始前必须锁定。

在这些问题完成验证前，相关结论不得写成“已修复”。

---

## 14. 最终审计结论

`iter_009` 证明了四人正式派发链已经能够真实创建节点、写入索引、调度启动并被 API 回读；但它同时保留了一个失败的 COO 父节点和四条未经主管显式决策的自动接受记录。因此：

```text
四人正式派发：已完成 4/4
同父节点与路径绑定：已满足
executor 启动回执：已满足
COO 父节点：failed
显式人工验收：未完成
迁移后真机 smoke：未执行
一期结论：FAIL / BLOCKED
二期：不得基于本轮结果启动
```

正确的下一步不是修补 `iter_009`，而是先强化 checker、review、并发恢复和派发幂等机制，再创建一个全新的正式 iteration 完成四人复验及 QA 显式验收。
