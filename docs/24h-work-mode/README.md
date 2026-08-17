# OneManCompany 24小时不间断工作模式配置文档

> 版本: 1.0  
> 更新日期: 2026-08-12  
> 状态: 待系统修复后实施

> **实施依据更新（2026-08-13）**：本文描述目标运营模式。当前真实实施顺序、SQLite/Checkpoint/长期记忆技术决策和上线门槛以 [IMPLEMENTATION-PLAN.md](./IMPLEMENTATION-PLAN.md) 为准。文中链接到尚不存在文件的条目均视为待办，不视为已完成。

---

## 📋 概述

本文档集合包含完整的24小时不间断工作模式配置，包括：
- 12人团队配置
- 每个员工的详细工作原则
- 自动化任务配置
- 启动和验证流程
- 成本分析

---

## 🎯 核心目标

```yaml
目标:
  - 项目周期: 31天
  - 工作时长: 24小时/天
  - 团队规模: 12人
  - 总工时: 8,928 人·小时
  - 产出: 相当于3个月的工作量

项目:
  名称: 云测试平台
  ID: 18b1e9d4a1fc
  实施路径: /Users/hanzhen/Documents/云测试的项目
  设备数量: 8台并发测试
```

---

## 👥 团队架构

### 指挥层（2人）
- **00003 - COO**（Alex）
  - 模型: gpt-5.6-sol
  - 职责: 24/7自动调度、任务分配、进度管理
  - 工作模式: 全天候自动化
  - [详细工作原则](../employee-work-principles/00003-coo-work-principles.md)

- **00010 - Tech Lead**
  - 模型: gpt-5.6-sol
  - 职责: 架构设计、难题攻关、代码审查
  - 工作模式: 按需工作（主要白天）
  - [详细工作原则](../employee-work-principles/00010-tech-lead-work-principles.md)

### 工程开发层（4人）
- **00006 - 高级后端工程师**（Alpha队长）
  - 模型: gpt-5.6-sol
  - 职责: 核心API开发、数据库设计、认证授权
  - [详细工作原则](../employee-work-principles/00006-senior-backend-work-principles.md)

- **00011 - 中级后端工程师**
  - 模型: gpt-5.6-sol
  - 职责: 辅助API开发、单元测试、代码重构
  - [详细工作原则](../employee-work-principles/00011-mid-backend-work-principles.md)

- **00007 - 全栈工程师**
  - 模型: gpt-5.6-sol
  - 职责: 前端开发、前后端集成、端到端测试
  - [详细工作原则](../employee-work-principles/00007-fullstack-work-principles.md)

- **00008 - DevOps/SRE**
  - 模型: gpt-5.6-sol
  - 职责: CI/CD、基础设施、监控告警
  - [详细工作原则](../employee-work-principles/00008-devops-work-principles.md)

### 质量保障层（2人）
- **00009 - QA Lead**
  - 模型: gpt-5.6-sol
  - 职责: 测试策略、功能测试、质量把关
  - [详细工作原则](../employee-work-principles/00009-qa-lead-work-principles.md)

- **00012 - 自动化测试工程师**
  - 模型: gpt-5.6-sol
  - 职责: 自动化测试脚本、夜间测试、CI/CD集成
  - [详细工作原则](../employee-work-principles/00012-automation-test-work-principles.md)

### 企业支持层（4人）
- **00002 - HR**
  - 模型: deepseek-v4-flash
  - 职责: 员工招募、评审、团队管理
  - [详细工作原则](../employee-work-principles/00002-hr-work-principles.md)

- **00004 - EA**（执行助理）
  - 模型: gpt-5.6-sol
  - 职责: CEO支持、文档管理、质量门禁
  - [详细工作原则](../employee-work-principles/00004-ea-work-principles.md)

- **00005 - CSO**
  - 模型: gpt-5.6-sol
  - 职责: 客户关系、产品推广、商务支持
  - [详细工作原则](../employee-work-principles/00005-cso-work-principles.md)

- **00001 - CEO**
  - 你自己

---

## 💰 成本分析

### 月度成本预估

| 层级 | 人数 | 模型 | 单价/月 | 小计 |
|------|------|------|---------|------|
| 指挥层 | 1 | gpt-5.6-sol | $450 | $450 |
| 指挥层 | 1 | gpt-5.6-sol | $270 | $270 |
| 工程层 | 2 | gpt-5.6-sol | $450 | $900 |
| 工程层 | 1 | gpt-5.6-sol | $180 | $180 |
| 工程层 | 1 | gpt-5.6-sol | $300 | $300 |
| 工程层 | 1 | gpt-5.6-sol | $200 | $200 |
| 质量层 | 1 | gpt-5.6-sol | $300 | $300 |
| 质量层 | 1 | gpt-5.6-sol | $180 | $180 |
| 支持层 | 2 | deepseek-v4-flash | $90 | $180 |
| 支持层 | 1 | gpt-5.6-sol | $180 | $180 |
| 支持层 | 1 | gpt-5.6-sol | $300 | $300 |
| **总计** | **12人** | | | **$3,440/月** |

详细分析见：[cost-analysis.md](./cost-analysis.md)

---

## 📦 实施前提条件

### ⚠️ 必须先完成系统修复

在启动24小时模式前，必须完成以下P0问题修复：

1. **并发控制** ✅
   - 实现全局任务调度器
   - 避免 `Concurrency limit exceeded`
   - 任务排队而不是失败

2. **显式验收** ✅
   - 禁用自动接受（standard模式）
   - 强制 accept_child/reject_child 调用
   - 完整审计记录

3. **派发幂等** ✅
   - 基于 (parent_id, employee_id, task_key) 的唯一约束
   - 网络抖动不会重复创建任务

4. **失败恢复** ✅
   - 声明式恢复接口
   - 节点可从 holding 状态恢复

详见：[P0-P1修复计划](../fixes/P0-P1-fix-plan.md)

---

## 🚀 启动流程

完整启动指南见：[startup-guide.md](./startup-guide.md)

### 快速启动

```bash
# 1. 确认系统修复完成
./scripts/check-system-ready.sh

# 2. 招募新员工
# 在 CEO Console 执行招募指令

# 3. 应用员工配置
./scripts/apply-employee-configs.sh

# 4. 启动24小时模式
# 在 CEO Console 执行启动指令

# 5. 验证运行状态
./scripts/verify-24h-mode.sh
```

---

## ✅ 验证清单

完整验证清单见：[verification-checklist.md](./verification-checklist.md)

### 关键验证点

- [ ] 12个员工全部就位
- [ ] 模型配置正确
- [ ] COO自动调度运行
- [ ] 自动化任务生效
- [ ] 并发控制正常
- [ ] 夜间安全策略生效
- [ ] 早晚报告自动生成
- [ ] 质量门禁执行

---

## 📊 监控仪表盘

### 每日检查点

**早上（8:30-9:00）**
- 阅读EA整理的早间简报
- 查看夜间完成的任务
- 处理需要决策的问题
- 确认今日优先级

**晚上（21:00）**
- 阅读日间进度报告
- 确认夜间任务计划
- 处理睡前需要决策的事项

**随时**
- 响应紧急告警
- 回复员工的问题
- 调整项目优先级

---

## 📚 相关文档

### 员工工作原则
- [所有员工工作原则目录](../employee-work-principles/)

### 自动化配置
- [自动化任务配置](../automation/cron-tasks.yaml)
- [备份脚本](../automation/backup-scripts/)

### 系统修复与实施
- [统一实施计划](./IMPLEMENTATION-PLAN.md)（当前权威）
- `../fixes/P0-P1-fix-plan.md`（待补齐）
- `../fixes/database-selection.md`（待补齐；当前决策已纳入统一实施计划）
- `../fixes/memory-system-design.md`（待补齐；当前设计已纳入统一实施计划）

---

## ⚠️ 重要提醒

### ✅ 可以做的
- 随时调整任务优先级
- 随时查看进度报告
- 处理员工上报的问题
- 调整团队配置

### ❌ 不要做的
- ❌ 不要手动分配每个任务（让COO自动处理）
- ❌ 不要频繁打断工作流程
- ❌ 不要降低质量标准
- ❌ 不要在夜间做重大决策（等早上）

### 🎯 成功关键
- **信任自动化**：让COO自动调度
- **定期检查**：每天早晚看报告
- **及时决策**：处理需要你决策的问题
- **保持耐心**：31天是一个马拉松

---

## 📞 支持

如有问题，参考：
1. [常见问题](./faq.md)
2. [故障排查指南](./troubleshooting.md)
3. [联系社区](https://github.com/yourusername/OneManCompany/issues)

---

*最后更新：2026-08-12*  
*版本：1.0*
