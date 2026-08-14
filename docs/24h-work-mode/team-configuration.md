# 团队配置总览

> **运行时基础设施说明（2026-08-13）**：本团队文档中的技术栈描述是云测试项目本身的业务技术栈，不是 OneManCompany 控制平面的数据库选型。当前 OMC 单机运行时统一采用 SQLite + LangGraph `AsyncSqliteSaver`/`AsyncSqliteStore` + `sqlite-vec`；PostgreSQL + pgvector 仅在多实例、多主机或高并发触发迁移评估后引入。

> 24小时不间断工作模式 - 12人团队详细配置

---

## 📊 团队结构图

```
                    CEO (00001 - 你)
                          |
            +-------------+-------------+
            |                           |
    COO (00003)                  Tech Lead (00010)
    24/7 调度中枢                  按需技术支持
            |
    +-------+-------+-------+-------+
    |       |       |       |       |
   工程    工程    QA     支持   支持
   开发    开发    团队    团队   团队
```

---

## 👥 完整团队名册

### 指挥中枢层

#### 00003 - COO（Alex）
```yaml
姓名: Alex COO
昵称: 铁面侠
模型: claude-opus-5
价格: $450/月
等级: Founding
部门: Operations

职责:
  - 24/7自动任务调度
  - 团队协调管理
  - 进度监控报告
  - 阻塞问题处理

工作模式:
  时间: 24小时全天候
  策略:
    - 白天（9-21点）：激进策略，快速推进
    - 夜间（21-9点）：保守策略，安全任务
  
  自动调度:
    - 每2小时扫描任务队列
    - 自动为空闲员工分配任务
    - 识别阻塞并自动处理
    - 每日生成早晚报告

关键能力:
  - 任务分解
  - 优先级排序
  - 资源调配
  - 风险识别

配置文件: .onemancompany/company/human_resource/employees/00003/
工作原则: docs/employee-work-principles/00003-coo-work-principles.md
```

#### 00010 - Tech Lead
```yaml
姓名: Tech Lead
模型: claude-fable-5
价格: $270/月
等级: Senior
部门: Engineering

职责:
  - 系统架构设计
  - 难题攻关
  - 代码审查（关键模块）
  - 技术指导

工作模式:
  时间: 按需（主要白天9-21点）
  响应: CEO或COO上报时立即响应
  夜间: 记录问题，早上处理

核心交付:
  - Day 0-2: 架构设计和接口定义
  - Day 3+: 难题解决和代码审查
  
关键能力:
  - 深度思考（最长30分钟thinking）
  - 架构设计
  - 问题诊断
  - 技术决策

配置文件: .onemancompany/company/human_resource/employees/00010/
工作原则: docs/employee-work-principles/00010-tech-lead-work-principles.md
```

---

### 工程开发层

#### 00006 - 高级后端工程师（Alpha队长）
```yaml
姓名: Senior Backend Engineer
模型: claude-opus-5
价格: $450/月
等级: Senior
部门: Engineering
队伍: Alpha小队（后端开发）

职责:
  - 核心API开发（设备管理、任务调度）
  - 数据库设计和优化
  - 认证授权系统
  - 审查00011的代码

工作模式:
  时间: 24/7全天候
  节奏:
    - 00:00-06:00: 代码重构、测试、文档（保守）
    - 06:00-12:00: 核心API开发
    - 12:00-18:00: 复杂功能实现
    - 18:00-24:00: 新功能开发、代码审查

产出标准:
  - API开发速度: 2-3个端点/天
  - 测试覆盖率: > 70%
  - Bug率: < 5%
  - 代码审查响应: < 4小时

技术栈:
  - FastAPI
  - PostgreSQL + SQLAlchemy
  - Redis
  - JWT认证

配置文件: .onemancompany/company/human_resource/employees/00006/
工作原则: docs/employee-work-principles/00006-senior-backend-work-principles.md
```

#### 00011 - 中级后端工程师
```yaml
姓名: Mid-level Backend Engineer
模型: gpt-5.6-sol
价格: $180/月
等级: Mid-level
部门: Engineering
队伍: Alpha小队（后端开发）

职责:
  - 辅助API开发（中等复杂度）
  - 单元测试编写
  - 代码重构
  - Bug修复

工作模式:
  时间: 24/7全天候
  汇报: 所有代码需00006审查
  夜间: 只做安全任务（测试、重构）

学习目标:
  - Month 1: 熟悉项目，完成简单API
  - Month 2: 独立完成中等复杂度功能
  - Month 3: 能设计API方案

产出标准:
  - 任务完成速度: 2-3个API/天
  - 代码审查通过率: > 80%
  - Bug率: < 8%

配置文件: .onemancompany/company/human_resource/employees/00011/
工作原则: docs/employee-work-principles/00011-mid-backend-work-principles.md
```

#### 00007 - 全栈工程师
```yaml
姓名: Full-stack Engineer
模型: claude-sonnet-5
价格: $300/月
等级: Senior
部门: Engineering

职责:
  - 前端开发（React/Vue）
  - 前后端集成
  - 端到端测试
  - API Mock服务

工作模式:
  时间: 24/7全天候
  节奏:
    - 00:00-06:00: UI组件、样式（保守）
    - 06:00-18:00: 核心功能、API集成
    - 18:00-24:00: E2E测试、Bug修复

产出标准:
  - 页面开发速度: 1-2个页面/天
  - Bug率: < 8%
  - 用户体验评分: 高

技术栈:
  - React 18 / Vue 3
  - Ant Design / Element Plus
  - Redux / Pinia
  - Playwright (E2E测试)

配置文件: .onemancompany/company/human_resource/employees/00007/
工作原则: docs/employee-work-principles/00007-fullstack-work-principles.md
```

#### 00008 - DevOps/SRE
```yaml
姓名: DevOps Engineer
模型: gpt-5.6-sol
价格: $200/月
等级: Senior
部门: Operations

职责:
  - CI/CD管道
  - 基础设施管理
  - 监控和告警
  - 故障响应

工作模式:
  时间: 24/7全天候监控
  响应: 告警触发时立即响应
  巡检: 每4小时检查系统健康

关键任务:
  - 自动化部署
  - 数据库备份（每天2:00）
  - 日志清理（每小时）
  - SSL证书更新（每天4:00）

产出标准:
  - 系统可用性: > 99.9%
  - MTTR: < 30分钟
  - 部署频率: 每天至少1次
  - 部署成功率: > 95%

技术栈:
  - Docker / Docker Compose
  - GitHub Actions
  - Prometheus + Grafana
  - Nginx

配置文件: .onemancompany/company/human_resource/employees/00008/
工作原则: docs/employee-work-principles/00008-devops-work-principles.md
```

---

### 质量保障层

#### 00009 - QA Lead
```yaml
姓名: QA Lead
模型: claude-sonnet-5
价格: $300/月
等级: Lead
部门: Quality Assurance

职责:
  - 测试策略制定
  - 功能测试
  - 设备兼容性测试（8台设备）
  - Sprint验收

工作模式:
  时间: 24/7全天候
  节奏:
    - 00:00-06:00: 运行自动化测试
    - 06:00-18:00: 功能测试、Bug验证
    - 18:00-24:00: 回归测试、测试报告

验收基线:
  设备: 8台不同品牌Android设备
  标准:
    - 连接: 100%通过
    - 注册: 100%通过
    - APK安装: ≥ 95%成功率
    - 截图: 100%通过
    - 日志采集: ≥ 90%成功率

产出标准:
  - 测试覆盖率: ≥ 70%
  - Bug发现率: 高
  - Bug遗漏率: < 5%

配置文件: .onemancompany/company/human_resource/employees/00009/
工作原则: docs/employee-work-principles/00009-qa-lead-work-principles.md
```

#### 00012 - 自动化测试工程师
```yaml
姓名: Automation Test Engineer
模型: gpt-5.6-sol
价格: $180/月
等级: Mid-level
部门: Quality Assurance

职责:
  - 编写自动化测试脚本
  - 搭建CI/CD测试集成
  - 运行夜间测试
  - 生成测试报告

工作模式:
  时间: 24/7全天候
  主力: 夜间（22:00-08:00）
  夜间任务:
    - 00:00-02:00: 完整回归测试
    - 02:00-04:00: 性能和压力测试
    - 04:00-06:00: 设备兼容性测试
    - 06:00-08:00: 测试报告生成

产出标准:
  - 测试脚本稳定性: > 95%
  - 测试覆盖率: > 70%
  - 夜间测试完成率: 100%

技术栈:
  - Playwright (UI自动化)
  - Pytest (API测试)
  - Locust (性能测试)
  - GitHub Actions

配置文件: .onemancompany/company/human_resource/employees/00012/
工作原则: docs/employee-work-principles/00012-automation-test-work-principles.md
```

---

### 企业支持层

#### 00002 - HR
```yaml
姓名: HR Manager
模型: deepseek-v4-flash
价格: $90/月
等级: Manager
部门: Human Resources

职责:
  - 员工招募
  - 员工评审
  - 团队管理
  - 档案维护

工作模式:
  时间: 按需（非24小时）
  响应: 4小时内响应
  主要: 白天（9:00-18:00）

配置文件: .onemancompany/company/human_resource/employees/00002/
工作原则: docs/employee-work-principles/00002-hr-work-principles.md
```

#### 00004 - EA（执行助理）
```yaml
姓名: Executive Assistant
模型: gpt-5.6-sol
价格: $180/月
等级: Senior
部门: Executive

职责:
  - CEO支持
  - 信息整理
  - 文档管理
  - 质量门禁

工作模式:
  时间: 白天（跟随CEO，9:00-21:00）
  响应: 立即响应CEO需求

每日工作:
  早上（8:30）:
    - 整理夜间报告
    - 生成早间简报（1页）
    - 准备今日议程
  
  白天:
    - 实时支持CEO
    - 记录重要对话
    - 跟进任务分配
  
  晚上:
    - 生成日总结
    - 更新项目进度

配置文件: .onemancompany/company/human_resource/employees/00004/
工作原则: docs/employee-work-principles/00004-ea-work-principles.md
```

#### 00005 - CSO
```yaml
姓名: Chief Sales Officer
模型: claude-sonnet-5
价格: $300/月
等级: C-level
部门: Sales

职责:
  - 客户关系管理
  - 产品推广
  - 商务支持
  - 市场分析

工作模式:
  时间: 按需（有客户事务时）
  主要: 白天（9:00-18:00）
  响应: 客户咨询4小时内

配置文件: .onemancompany/company/human_resource/employees/00005/
工作原则: docs/employee-work-principles/00005-cso-work-principles.md
```

---

## 🔄 协作矩阵

### 核心协作关系

```
COO (00003) ← 汇报 ← 所有员工
COO (00003) → 分配任务 → 所有工程师

Tech Lead (00010) ← 难题上报 ← 工程师
Tech Lead (00010) → 技术指导 → 工程师

00006 (高级后端) ← 代码审查 ← 00011 (中级后端)
00006 (高级后端) ↔ API联调 ↔ 00007 (全栈)

00009 (QA Lead) ↔ 测试协作 ↔ 00012 (自动化测试)
00009 (QA Lead) ← Bug报告 → 所有工程师

00004 (EA) ← 处理报告 ← 00003 (COO)
00004 (EA) → 早间简报 → CEO
```

---

## 📋 模型分配策略

### 为什么这样分配？

#### Claude Opus 5（3人）- 最高能力
- **00003 COO**: 需要复杂决策和协调
- **00006 高级后端**: 核心API开发，架构关键
- **Tech Lead**: 难题攻关，深度思考

#### Claude Fable 5（1人）- 最新最强
- **00010 Tech Lead**: 技术领导者，需要最强推理

#### Claude Sonnet 5（3人）- 平衡性能
- **00007 全栈**: 前后端协调，需要理解力
- **00009 QA Lead**: 测试策略，需要批判性思维
- **00005 CSO**: 客户沟通，需要语言能力

#### GPT 5.6 Sol/Terra（3人）- 高性价比
- **00011 中级后端**: 辅助开发，不需顶级模型
- **00012 自动化测试**: 脚本编写，稳定可靠
- **00008 DevOps**: 运维任务，脚本为主
- **00004 EA**: 文档整理，组织能力

#### Deepseek v4 Flash（2人）- 经济实惠
- **00002 HR**: 招募流程，简单任务
- 备用支持岗位

---

## 💡 使用建议

### 调整模型配置

如果预算紧张，可以降级：
```yaml
降级方案（节省 ~$1000/月）:
  00003 COO: claude-opus-5 → claude-sonnet-5
  00006 高级后端: claude-opus-5 → claude-sonnet-5
  00010 Tech Lead: claude-fable-5 → claude-opus-5
  
  总成本: $3,440 → $2,440
```

如需临时升级 00011/00012，必须通过配置 revision、变更审批和独立回归验证；默认正式目标保持 `gpt-5.6-sol`。

---

## ✅ 配置检查清单

- [ ] 12个员工ID确认（00001-00012）
- [ ] 模型分配正确
- [ ] 工作原则文档就位
- [ ] profile.yaml 配置完整
- [ ] 角色和职责明确
- [ ] 协作关系清晰
- [ ] 成本预算批准

---

*最后更新：2026-08-12*
