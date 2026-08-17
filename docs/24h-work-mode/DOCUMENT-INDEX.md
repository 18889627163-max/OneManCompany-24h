# OneManCompany 24小时工作模式 - 完整文档索引

> 所有配置文件、文档和脚本的完整列表

> **仓库状态说明（2026-08-14）**：员工工作原则、automation manifest、备份/监控/验证脚本、P0 Gate 和 Recovery Gate 已落地。`docs/fixes/`、`cost-analysis.md`、`troubleshooting.md`、`faq.md` 仍不存在，索引中这些条目只表示历史目标，不作为已完成证据。当前实施状态以 `IMPLEMENTATION-PLAN.md` 和 `STATUS-REPORT.md` 为准。

---

## 📚 文档结构

```
OneManCompany-main/
├── docs/
│   ├── 24h-work-mode/                     # 24小时模式核心文档
│   │   ├── README.md                      # 总览（从这里开始）
│   │   ├── team-configuration.md          # 12人团队详细配置
│   │   ├── startup-guide.md               # 启动指南（分步骤）
│   │   ├── verification-checklist.md      # 验证清单
│   │   ├── IMPLEMENTATION-PLAN.md          # 持续运行、恢复与长期记忆实施计划
│   │   ├── ADR-001-sqlite-runtime.md       # SQLite 单机运行时架构决策
│   │   ├── RUNTIME-WARNING-REMEDIATION-PLAN.md # 历史员工、Hook 与自动化告警专项计划
│   │   ├── STATUS-REPORT.md                # 当前实施状态与阻塞项
│   │   ├── reports/                        # P0、恢复、服务和数据审计报告
│   │   ├── cost-analysis.md               # 目标文件，当前不存在
│   │   ├── troubleshooting.md             # 目标文件，当前不存在
│   │   └── faq.md                         # 目标文件，当前不存在                         # 常见问题
│   │
│   ├── employee-work-principles/          # 员工工作原则（11个）
│   │   ├── 00002-hr-work-principles.md
│   │   ├── 00003-coo-work-principles.md
│   │   ├── 00004-ea-work-principles.md
│   │   ├── 00005-cso-work-principles.md
│   │   ├── 00006-senior-backend-work-principles.md
│   │   ├── 00007-fullstack-work-principles.md
│   │   ├── 00008-devops-work-principles.md
│   │   ├── 00009-qa-lead-work-principles.md
│   │   ├── 00010-tech-lead-work-principles.md
│   │   ├── 00011-mid-backend-work-principles.md
│   │   └── 00012-automation-test-work-principles.md
│   │
│   ├── automation/                        # 自动化配置
│   │   ├── cron-tasks.yaml               # 定时任务配置
│   │   └── backup-scripts/
│   │       ├── backup-all.sh
│   │       └── restore.sh
│   │
│   └── fixes/                            # 历史目标目录，当前不存在
│       ├── P0-P1-fix-plan.md            # 修复计划
│       ├── database-selection.md        # 数据库选型
│       └── memory-system-design.md      # 长期记忆设计
│
├── scripts/                              # 脚本工具
│   ├── check-system-ready.sh            # 系统就绪检查
│   ├── apply-work-principles.sh         # 原子应用工作原则
│   ├── monitor-24h-mode.sh              # 监控脚本
│   └── verify-24h-mode.sh               # 验证脚本
│
└── .onemancompany/                      # 运行时数据
    └── company/human_resource/employees/ # 员工配置目录
        ├── 00001/  # CEO
        ├── 00002/  # HR
        ├── 00003/  # COO
        ├── 00004/  # EA
        ├── 00005/  # CSO
        ├── 00006/  # 高级后端
        ├── 00007/  # 全栈
        ├── 00008/  # DevOps
        ├── 00009/  # QA Lead
        ├── 00010/  # Tech Lead
        ├── 00011/  # 中级后端
        └── 00012/  # 自动化测试
```

---

## 📖 核心文档详解

### 1. 入门文档（必读）

#### [README.md](./README.md)
- **用途**：总览文档，从这里开始
- **内容**：
  - 项目目标和架构
  - 团队配置概览
  - 成本分析
  - 实施前提条件
  - 快速启动指令
- **适合**：第一次了解24小时模式
- **阅读时间**：10分钟

#### [team-configuration.md](./team-configuration.md)
- **用途**：12人团队详细配置
- **内容**：
  - 每个员工的详细职责
  - 模型分配策略和原因
  - 协作关系矩阵
  - 配置调整建议
- **适合**：了解团队分工
- **阅读时间**：20分钟

#### [startup-guide.md](./startup-guide.md)
- **用途**：分步启动指南
- **内容**：
  - Phase 1-6 详细步骤
  - 命令行操作指令
  - CEO Console 启动指令
  - 验证和监控方法
- **适合**：实际启动时参考
- **阅读时间**：15分钟（执行需90分钟）

#### [verification-checklist.md](./verification-checklist.md)
- **用途**：完整验证清单
- **内容**：
  - 启动前验证（P0修复）
  - 第一小时验证
  - 第一天验证
  - 第一周验证
  - 成功和失败标准
- **适合**：启动后验证
- **阅读时间**：15分钟

---

#### [IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md)
- **用途**：统一 SQLite 持久化、LangGraph 恢复、长期记忆、组织配置和 24 小时运营验收
- **内容**：
  - SQLite/AsyncSqliteSaver/AsyncSqliteStore 技术决策
  - P0 到长期记忆和运营验收的分阶段计划
  - 正式启动门槛、历史审计边界和 PostgreSQL 迁移条件
- **适合**：系统开发和最终验收

#### [ADR-001-sqlite-runtime.md](./ADR-001-sqlite-runtime.md)
- **用途**：记录当前 SQLite 单机方案的正式决策、边界和 PostgreSQL 迁移触发条件
- **适合**：架构评审、上线前 Gate 和未来迁移准备

#### [reports/RUNTIME-STATE-RECONCILIATION-20260814.md](./reports/RUNTIME-STATE-RECONCILIATION-20260814.md)
- **用途**：正式 RuntimeStorage checkpoint finding 与 Memory Outbox backlog 的只读对账
- **内容**：7 条 legacy system automation orphan 分类、26 条 pending outbox 分类、数据不变证据和处置边界
- **适合**：embedding/vector Gate 前的运行状态审计

#### [RUNTIME-WARNING-REMEDIATION-PLAN.md](./RUNTIME-WARNING-REMEDIATION-PLAN.md)
- **用途**：处理历史 ex-employee、缺失 skill hook、system automation 假告警和 adhoc TaskTree 路径问题
- **内容**：红灯测试、受控 skill reconciliation、system project 隔离、备份/隔离/回滚和专项 Gate
- **适合**：真实云 Provider 与 24 小时墙钟演练前的 P1 运行卫生修复

### 2. 员工工作原则（11个文件）

每个员工都有详细的工作原则文档，包含：
- 角色定位和职责
- 工作模式（24/7或按需）
- 核心交付物和标准
- 与其他员工的协作方式
- 夜间工作策略
- 质量标准和KPI

#### 指挥层

**[00003-coo-work-principles.md](../employee-work-principles/00003-coo-work-principles.md)**
```yaml
角色: COO - 24/7调度中枢
模型: gpt-5.6-sol
长度: 约150行
重点内容:
  - 24/7自动调度策略
  - 白天激进 vs 夜间保守
  - 任务分解和优先级
  - 阻塞识别和处理
  - 早晚报告生成
```

**[00010-tech-lead-work-principles.md](../employee-work-principles/00010-tech-lead-work-principles.md)**
```yaml
角色: Tech Lead - 技术领导者
模型: gpt-5.6-sol
长度: 约120行
重点内容:
  - 架构设计方法
  - 难题攻关流程
  - 代码审查标准
  - 技术决策框架
```

#### 工程开发层

**[00006-senior-backend-work-principles.md](../employee-work-principles/00006-senior-backend-work-principles.md)**
```yaml
角色: 高级后端工程师（Alpha队长）
模型: gpt-5.6-sol
长度: 约180行
重点内容:
  - 核心API开发（设备、任务、认证）
  - 数据库设计模式
  - 代码审查责任
  - 24/7工作节奏
```

**[00011-mid-backend-work-principles.md](../employee-work-principles/00011-mid-backend-work-principles.md)**
```yaml
角色: 中级后端工程师
模型: gpt-5.6-sol
长度: 约150行
重点内容:
  - 辅助API开发
  - 单元测试编写
  - 向00006学习
  - 成长路径
```

**[00007-fullstack-work-principles.md](../employee-work-principles/00007-fullstack-work-principles.md)**
```yaml
角色: 全栈工程师
模型: gpt-5.6-sol
长度: 约160行
重点内容:
  - React/Vue组件开发
  - 前后端集成和联调
  - API Mock服务
  - 端到端测试
```

**[00008-devops-work-principles.md](../employee-work-principles/00008-devops-work-principles.md)**
```yaml
角色: DevOps/SRE
模型: gpt-5.6-sol
长度: 约200行
重点内容:
  - CI/CD管道配置
  - Docker/Compose基础设施
  - Prometheus监控和告警
  - 故障响应runbook
  - 自动化备份和清理
```

#### 质量保障层

**[00009-qa-lead-work-principles.md](../employee-work-principles/00009-qa-lead-work-principles.md)**
```yaml
角色: QA Lead
模型: gpt-5.6-sol
长度: 约170行
重点内容:
  - 测试策略和计划
  - 8设备兼容性测试矩阵
  - Sprint验收标准
  - Bug管理流程
  - 质量门禁
```

**[00012-automation-test-work-principles.md](../employee-work-principles/00012-automation-test-work-principles.md)**
```yaml
角色: 自动化测试工程师
模型: gpt-5.6-sol
长度: 约150行
重点内容:
  - Playwright UI自动化
  - Pytest API测试
  - 夜间测试编排（00:00-08:00）
  - CI/CD集成
  - 测试报告生成
```

#### 支持层

**[00002-hr-work-principles.md](../employee-work-principles/00002-hr-work-principles.md)**
```yaml
角色: HR
模型: deepseek-v4-flash
长度: 约50行
重点内容: 员工招募、评审、团队管理
```

**[00004-ea-work-principles.md](../employee-work-principles/00004-ea-work-principles.md)**
```yaml
角色: Executive Assistant
模型: gpt-5.6-sol
长度: 约60行
重点内容:
  - 早间简报生成（8:30）
  - CEO支持和文档管理
  - 质量门禁检查
```

**[00005-cso-work-principles.md](../employee-work-principles/00005-cso-work-principles.md)**
```yaml
角色: CSO
模型: gpt-5.6-sol
长度: 约50行
重点内容: 客户关系、产品推广、商务支持
```

---

### 3. 自动化配置

#### [automation/cron-tasks.yaml](../automation/cron-tasks.yaml)
```yaml
用途: 所有自动化定时任务配置
包含:
  - COO自动调度（每2小时）
  - COO阻塞检查（每1小时）
  - 早间报告生成（8:30）
  - 晚间报告生成（21:00）
  - 夜间回归测试（00:00-02:00）
  - 夜间性能测试（02:00-04:00）
  - 夜间兼容性测试（04:00-06:00）
  - 数据库备份（02:00）
  - 日志清理（每小时）
  - 健康检查（每4小时）
  - SSL证书更新（04:00）
  - 代码质量分析（03:00）

格式: YAML
长度: 约300行
```

---

### 4. 系统修复文档

#### [fixes/P0-P1-fix-plan.md](../fixes/P0-P1-fix-plan.md)
```yaml
用途: P0和P1问题的完整修复计划
包含:
  Phase 0: 环境准备
  Phase 1: ProviderGateway 并发闸门与 durable holding
  Phase 2: 显式验收（accept_child/reject_child）
  Phase 3: dispatch intent/receipt 幂等
  Phase 4: 失败恢复（retry_failed_node）
  Phase 5-7: P1问题修复

长度: 约500行
代码示例: Python实现
测试用例: Pytest
```

#### [fixes/database-selection.md](../fixes/database-selection.md)
```yaml
用途: 数据库选型分析
说明:
  - 本条为历史规划引用，实际文件当前不存在；
  - 当前数据库和长期记忆权威方案以 `IMPLEMENTATION-PLAN.md` 为准；
  - 当前单机方案为 SQLite + AsyncSqliteSaver + AsyncSqliteStore + sqlite-vec；
  - PostgreSQL + pgvector 仅作为后续迁移目标。

长度: 以 `IMPLEMENTATION-PLAN.md` 为准
```

#### [fixes/memory-system-design.md](../fixes/memory-system-design.md)
```yaml
用途: 长期记忆系统设计
包含:
  - LangGraph Checkpoint
  - 员工/项目/公司记忆分级
  - 敏感信息过滤
  - 向量混合检索
  - 记忆冲突解决

长度: 约600行
实施优先级: P0修复后
```

---

## 🔧 工具脚本

### 系统就绪检查
```bash
./scripts/check-system-ready.sh
```
- 检查P0修复完成情况
- 检查依赖安装
- 检查环境配置
- 输出：✅ / ❌

### 应用工作原则
```bash
./scripts/apply-work-principles.sh
```
- 批量复制工作原则到员工目录
- 备份旧配置
- 验证应用成功

### 监控脚本
```bash
./scripts/monitor-24h-mode.sh
```
- 显示员工状态
- 显示任务统计
- 显示系统健康
- 显示COO最近活动

### 验证脚本
```bash
./scripts/verify-24h-mode.sh
```
- 执行完整验证清单
- 生成验证报告
- 输出问题和建议

---

## 📂 运行时数据

### 员工配置目录
```
.onemancompany/company/human_resource/employees/{employee_id}/
├── profile.yaml           # 员工配置（模型、技能等）
├── work_principles.md     # 工作原则
├── progress.log           # 工作日志
├── task_index.yaml        # 任务索引
├── task_history.json      # 任务历史
└── conversations/         # 会话记录
```

### 项目数据目录
```
.onemancompany/company/business/projects/{project_id}/
├── project.yaml           # 项目配置
├── iterations/
│   └── iter_XXX/
│       ├── task_tree.yaml      # 任务树（权威来源）
│       ├── nodes/              # 节点执行日志
│       └── deliverables/       # 交付物
└── reports/               # 报告
```

### 日志目录
```
.onemancompany/logs/
├── activity.log          # 活动日志
├── api.log              # API日志
└── cron.log             # 定时任务日志
```

---

## 📋 快速查找

### 我想了解...

**"24小时模式是什么？"**
→ [README.md](./README.md)

**"12个员工都是谁？做什么？"**
→ [team-configuration.md](./team-configuration.md)

**"怎么启动？"**
→ [startup-guide.md](./startup-guide.md)

**"COO怎么工作？"**
→ [00003-coo-work-principles.md](../employee-work-principles/00003-coo-work-principles.md)

**"夜间测试怎么配置？"**
→ [00012-automation-test-work-principles.md](../employee-work-principles/00012-automation-test-work-principles.md)

**"自动化任务有哪些？"**
→ [automation/cron-tasks.yaml](../automation/cron-tasks.yaml)

**"P0问题是什么？怎么修？"**
→ [IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md) 和 [STATUS-REPORT.md](./STATUS-REPORT.md)

**"数据库用什么？"**
→ [ADR-001-sqlite-runtime.md](./ADR-001-sqlite-runtime.md)

**"验证是否正常运行？"**
→ [verification-checklist.md](./verification-checklist.md)

**"当前实施到哪一步？"**
→ [STATUS-REPORT.md](./STATUS-REPORT.md)

**"恢复演练结果是什么？"**
→ [reports/RECOVERY-GATE-REPORT.json](./reports/RECOVERY-GATE-REPORT.json)

---

## 📊 文档统计

```
总文档数量: 30+

核心文档: 7个
  - README, 团队配置, 启动指南, 验证清单
  - 成本分析, 故障排查, FAQ

员工工作原则: 11个
  - 指挥层 2个
  - 工程层 4个
  - QA层 2个
  - 支持层 3个

系统修复: 3个
  - P0-P1修复计划
  - 数据库选型
  - 长期记忆设计

自动化配置: 1个
  - 定时任务配置

工具脚本: 4个
  - 系统检查、应用配置、监控、验证

总代码行数: 约5000行
总文档字数: 约50000字
```

---

## 🚀 推荐阅读顺序

### 第一次了解（30分钟）
1. [README.md](./README.md) - 10分钟
2. [team-configuration.md](./team-configuration.md) - 20分钟

### 准备启动（60分钟）
1. [startup-guide.md](./startup-guide.md) - 15分钟
2. [verification-checklist.md](./verification-checklist.md) - 15分钟
3. [00003-coo-work-principles.md](../employee-work-principles/00003-coo-work-principles.md) - 15分钟
4. [automation/cron-tasks.yaml](../automation/cron-tasks.yaml) - 15分钟

### 深入了解（2小时）
1. 阅读所有11个员工工作原则
2. 理解自动化任务配置
3. 了解P0修复计划

### 系统开发
1. [IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md)
2. 先执行 Phase 0-3（P0、RuntimeStorage、Checkpoint 恢复）
3. 再执行 Phase 4-7（长期记忆、自动化和管理面）
4. 缺失的 `fixes/` 历史规划不作为当前实现依据，以 IMPLEMENTATION-PLAN.md 和 ADR-001-sqlite-runtime.md 为准

---

## 📞 获取帮助

如果找不到需要的文档：
1. 查看本索引的"快速查找"部分
2. 使用 `grep` 搜索关键词
3. 查看 [FAQ](./faq.md)
4. 联系社区支持

---

*最后更新：2026-08-14*  
*文档版本：1.1*


## 2026-08-14 实施验证报告

- `reports/P0-GATE-REPORT.json`：P0 正式 Gate 的机器可读结果，包含隔离 recovery test group。
- `reports/RECOVERY-GATE-REPORT.json`：standard v2 隔离 Recovery Gate 的机器可读结果。
- `reports/RECOVERY-IMPLEMENTATION-MANIFEST-20260814.json`：无 Git 元数据环境下的实施文件 SHA-256 清单。
- `reports/CHECKPOINT-CRASH-RESUME-20260814.md`：checkpoint crash/resume 与副作用防重放。
- `reports/PROVIDER-HOLDING-RESUME-20260814.md`：模拟 Provider 429 durable holding/resume。
- `reports/CHECKPOINT-RECONCILIATION-20260814.md`：TaskTree/checkpoint reconciler 状态矩阵。
- `reports/SERVICE-VERIFY-20260814.md`：memory-enabled 隔离真实服务、readiness、在线备份与 clean shutdown。
- `reports/RECOVERY-REPORT.md`：SQLite 独立备份恢复演练结果。
- `reports/FORMAL-DATA-INTEGRITY-20260814.md`：正式数据隔离、live-service 并发活动和 `iter_009` 保护。
- `reports/RUNTIME-STATE-RECONCILIATION-20260814.md`：正式 RuntimeStorage 只读对账、legacy system orphan 分类和 Memory Outbox 审计。
- `reports/MEMORY-VECTOR-GATE-20260814.md`：sqlite-vec、混合检索、versioned shadow reindex、真实云阻塞和测试正式库隔离修复。
- `STATUS-REPORT.md`：当前已完成项、隔离验证和正式上线阻塞项。

### Runtime warning remediation execution report

- `reports/RUNTIME-WARNING-REMEDIATION-20260814.md` — completed implementation, audited runtime maintenance, backup/restore evidence, full test result, and controlled live-service verification.
