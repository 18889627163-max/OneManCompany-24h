# 24小时工作模式启动指南

> 完整的启动、配置和验证流程

---

## ⚠️ 重要前提

**在启动24小时模式之前，必须确认系统已完成P0问题修复！**

```bash
# 运行系统就绪检查
./scripts/check-system-ready.sh
```

必须通过的检查项：
- ✅ 全局任务调度器已实现
- ✅ 并发控制机制运行正常
- ✅ 派发幂等性已验证
- ✅ 显式验收流程已强制执行
- ✅ workflow_contract_version = 2 已启用

如果检查失败，参考：[P0-P1修复计划](../fixes/P0-P1-fix-plan.md)

---

## 📋 启动流程总览

```
Phase 1: 环境准备（30分钟）
  ↓
Phase 2: 招募新员工（15分钟）
  ↓
Phase 3: 配置员工模型（20分钟）
  ↓
Phase 4: 应用工作原则（15分钟）
  ↓
Phase 5: 启动24小时模式（10分钟）
  ↓
Phase 6: 验证和监控（持续）
```

总耗时：约90分钟

---

## Phase 1: 环境准备

### 1.1 备份现有数据

```bash
# 进入项目目录
cd /Users/hanzhen/Downloads/OneManCompany-main

# 备份员工配置
tar -czf backup_employees_$(date +%Y%m%d_%H%M%S).tar.gz \
  .onemancompany/company/human_resource/employees/

# 备份项目数据
tar -czf backup_projects_$(date +%Y%m%d_%H%M%S).tar.gz \
  .onemancompany/company/business/projects/

echo "✅ 备份完成"
```

### 1.2 确认环境

```bash
# 检查 Python 版本
python --version  # 应该是 3.10+

# 检查依赖
source .venv/bin/activate
pip list | grep onemancompany

# 检查服务状态
curl http://localhost:8000/api/health

# 确认实施路径存在
test -d '/Users/hanzhen/Documents/云测试的项目' && echo "✅ 路径存在" || echo "❌ 路径不存在"
```

### 1.3 安装缺失工具

```bash
# 安装 FFmpeg（用于媒体测试）
brew install ffmpeg

# 验证安装
ffmpeg -version
ffprobe -version

echo "✅ 工具安装完成"
```

---

## Phase 2: 招募新员工

### 2.1 当前员工状态

检查现有员工：

```bash
# 列出所有员工
ls -la .onemancompany/company/human_resource/employees/
```

应该有：
- 00001 (CEO - 你)
- 00002 (HR)
- 00003 (COO)
- 00004 (EA)
- 00005 (CSO)
- 00006 (全栈工程师 - 需要改配置)
- 00007 (API Tester - 需要改配置)
- 00008 (DevOps)
- 00009 (QA)
- 00010 (项目经理 - 需要改为Tech Lead)

**需要招募**：
- 00011 (中级后端工程师) - 新招
- 00012 (自动化测试工程师) - 新招

### 2.2 执行招募

**在 CEO Console 中执行**：

```
HR（00002），请从人才市场招募2名员工：

1. 中级后端工程师
   员工ID: 00011
   职责: 辅助后端核心开发，负责中等复杂度的API开发和单元测试
   要求: 熟悉 Python/FastAPI，有数据库经验
   技能: API开发、数据库、单元测试、代码重构
   工作模式: 24/7 全天候工作
   配置模型: gpt-5.6-sol
   汇报对象: 00006 (高级后端工程师)
   
2. 自动化测试工程师
   员工ID: 00012
   职责: 编写自动化测试脚本，搭建CI/CD，运行夜间测试
   要求: 熟悉 pytest/Playwright/Selenium，有测试经验
   技能: 自动化测试、CI/CD、测试报告、性能测试
   工作模式: 24/7 全天候工作，主要夜间运行测试
   配置模型: gpt-5.6-sol
   汇报对象: 00009 (QA Lead)

请在今天完成招募，明天他们就可以开始工作。
```

### 2.3 验证招募结果

```bash
# 检查新员工目录是否创建
test -d .onemancompany/company/human_resource/employees/00011 && echo "✅ 00011已创建"
test -d .onemancompany/company/human_resource/employees/00012 && echo "✅ 00012已创建"
```

---

## Phase 3: 配置员工模型

### 3.1 通过 UI 修改（推荐）

打开浏览器访问：http://localhost:8000

**需要修改的员工**：

1. **00003 (COO)**
   - 点击 COO 头像
   - 找到 "LLM 配置" → "当前模型"
   - 改为：`claude-opus-5`
   - 保存

2. **00006 (全栈 → 高级后端)**
   - 点击员工头像
   - 修改职位：`高级后端工程师`
   - 修改模型：`claude-opus-5`
   - 保存

3. **00007 (API Tester → 全栈)**
   - 修改职位：`全栈工程师`
   - 修改模型：`claude-sonnet-5`
   - 保存

4. **00009 (QA)**
   - 修改模型：`claude-sonnet-5`
   - 保存

5. **00010 (项目经理 → Tech Lead)**
   - 修改职位：`Tech Lead`
   - 修改模型：`claude-fable-5`
   - 保存

### 3.2 通过命令行修改（高级）

或者直接编辑配置文件：

```bash
# 00003 (COO)
sed -i '' 's/llm_model: .*/llm_model: claude-opus-5/' \
  .onemancompany/company/human_resource/employees/00003/profile.yaml

# 00006 (高级后端)
sed -i '' 's/llm_model: .*/llm_model: claude-opus-5/' \
  .onemancompany/company/human_resource/employees/00006/profile.yaml
sed -i '' 's/role: .*/role: Senior Backend Engineer/' \
  .onemancompany/company/human_resource/employees/00006/profile.yaml

# 00007 (全栈)
sed -i '' 's/llm_model: .*/llm_model: claude-sonnet-5/' \
  .onemancompany/company/human_resource/employees/00007/profile.yaml
sed -i '' 's/role: .*/role: Full-stack Engineer/' \
  .onemancompany/company/human_resource/employees/00007/profile.yaml

# 00009 (QA)
sed -i '' 's/llm_model: .*/llm_model: claude-sonnet-5/' \
  .onemancompany/company/human_resource/employees/00009/profile.yaml

# 00010 (Tech Lead)
sed -i '' 's/llm_model: .*/llm_model: claude-fable-5/' \
  .onemancompany/company/human_resource/employees/00010/profile.yaml
sed -i '' 's/role: .*/role: Tech Lead/' \
  .onemancompany/company/human_resource/employees/00010/profile.yaml

echo "✅ 模型配置完成"
```

### 3.3 验证配置

```bash
# 验证所有模型配置
grep "llm_model" .onemancompany/company/human_resource/employees/*/profile.yaml

# 应该看到：
# 00003/profile.yaml:llm_model: claude-opus-5
# 00006/profile.yaml:llm_model: claude-opus-5
# 00007/profile.yaml:llm_model: claude-sonnet-5
# 00009/profile.yaml:llm_model: claude-sonnet-5
# 00010/profile.yaml:llm_model: claude-fable-5
# 00011/profile.yaml:llm_model: gpt-5.6-sol
# 00012/profile.yaml:llm_model: gpt-5.6-sol
```

---

## Phase 4: 应用工作原则

### 4.1 复制工作原则文档

所有员工的工作原则已经生成在：
```
docs/employee-work-principles/
```

**方法1：通过 UI 逐个应用**

1. 打开员工详情页
2. 找到 "工作原则" 区域
3. 点击 "编辑"
4. 复制对应的工作原则内容
5. 粘贴并保存

**方法2：通过脚本批量应用**

创建应用脚本：

```bash
#!/bin/bash
# scripts/apply-work-principles.sh

set -e

DOCS_DIR="docs/employee-work-principles"
EMPLOYEES_DIR=".onemancompany/company/human_resource/employees"

echo "应用工作原则..."

# 00003 - COO
cp "$DOCS_DIR/00003-coo-work-principles.md" \
   "$EMPLOYEES_DIR/00003/work_principles.md"
echo "✅ 00003 (COO) 工作原则已应用"

# 00010 - Tech Lead
cp "$DOCS_DIR/00010-tech-lead-work-principles.md" \
   "$EMPLOYEES_DIR/00010/work_principles.md"
echo "✅ 00010 (Tech Lead) 工作原则已应用"

# 00006 - 高级后端
cp "$DOCS_DIR/00006-senior-backend-work-principles.md" \
   "$EMPLOYEES_DIR/00006/work_principles.md"
echo "✅ 00006 (高级后端) 工作原则已应用"

# 00011 - 中级后端
cp "$DOCS_DIR/00011-mid-backend-work-principles.md" \
   "$EMPLOYEES_DIR/00011/work_principles.md"
echo "✅ 00011 (中级后端) 工作原则已应用"

# 00007 - 全栈
cp "$DOCS_DIR/00007-fullstack-work-principles.md" \
   "$EMPLOYEES_DIR/00007/work_principles.md"
echo "✅ 00007 (全栈) 工作原则已应用"

# 00008 - DevOps
cp "$DOCS_DIR/00008-devops-work-principles.md" \
   "$EMPLOYEES_DIR/00008/work_principles.md"
echo "✅ 00008 (DevOps) 工作原则已应用"

# 00009 - QA Lead
cp "$DOCS_DIR/00009-qa-lead-work-principles.md" \
   "$EMPLOYEES_DIR/00009/work_principles.md"
echo "✅ 00009 (QA Lead) 工作原则已应用"

# 00012 - 自动化测试
cp "$DOCS_DIR/00012-automation-test-work-principles.md" \
   "$EMPLOYEES_DIR/00012/work_principles.md"
echo "✅ 00012 (自动化测试) 工作原则已应用"

# 支持层（简化版本）
cp "$DOCS_DIR/00002-hr-work-principles.md" \
   "$EMPLOYEES_DIR/00002/work_principles.md"
echo "✅ 00002 (HR) 工作原则已应用"

cp "$DOCS_DIR/00004-ea-work-principles.md" \
   "$EMPLOYEES_DIR/00004/work_principles.md"
echo "✅ 00004 (EA) 工作原则已应用"

cp "$DOCS_DIR/00005-cso-work-principles.md" \
   "$EMPLOYEES_DIR/00005/work_principles.md"
echo "✅ 00005 (CSO) 工作原则已应用"

echo ""
echo "✅ 所有工作原则已应用完成！"
```

运行脚本：

```bash
chmod +x scripts/apply-work-principles.sh
./scripts/apply-work-principles.sh
```

---

## Phase 5: 启动24小时模式

### 5.1 重启服务

```bash
# 停止当前服务（Ctrl+C）

# 重新启动
bash start.sh

# 等待服务完全启动（约30秒）
sleep 30

# 验证服务
curl http://localhost:8000/api/health
```

### 5.2 执行启动指令

**在 CEO Console 中执行**：

```
启动24小时不间断工作模式。

## 系统配置

从现在开始，本公司实行24/7全天候工作制：

### 工作时间规定
- 所有工程师和QA团队：24小时随时待命
- 没有上下班概念，没有周末和假期
- 任务完成后立即自动接受下一个任务
- 空闲超过10分钟自动分配新任务

### 自动化调度
COO（00003）：
- 每2小时自动扫描任务队列和员工状态
- 自动为空闲员工分配任务
- 不等待人工指示，完全自动化运行
- 白天策略：激进（快速推进关键任务）
- 夜间策略：保守（执行安全的辅助任务）

### 时段策略

#### 白天模式（09:00-21:00）
优先级：
1. 关键路径任务（核心功能开发）
2. 需要人工决策的任务（CEO在线）
3. 复杂技术任务（Tech Lead可支持）
4. 常规开发任务

策略：
- 快速推进主线功能
- 可以做架构级变更
- 遇到问题立即上报

#### 夜间模式（21:00-09:00）
优先级：
1. 独立任务（不依赖其他模块）
2. 测试任务（自动化测试、回归测试）
3. 代码重构（低风险优化）
4. 文档编写
5. 简单功能开发

策略：
- 不做破坏性改动
- 不修改核心模块
- 遇到重大问题记录等早上处理
- 任务失败2次自动暂停

### 自动化任务
启用所有自动化任务：
- ✅ COO每2小时自动调度
- ✅ COO每1小时检查阻塞
- ✅ 每天8:30生成夜间报告
- ✅ 每天21:00生成日间报告
- ✅ 夜间自动化测试（00:00-06:00）
- ✅ 数据库自动备份（02:00）
- ✅ 日志自动清理（每小时）
- ✅ 系统健康检查（每4小时）

### 进度报告
COO自动生成报告：
- 每天早上8:30：夜间工作总结
- 每天晚上21:00：日间进度和夜间计划

EA（00004）处理报告：
- 整理成早间简报给CEO
- 标记需要决策的事项
- 准备当日议程

### 质量保障
即使24小时全速运转，质量门禁不降低：
- 单元测试覆盖率 >= 60%
- 集成测试必须通过
- 无P0 bug
- 代码审查通过（关键模块）
- QA验收通过

### 团队配置（12人）

指挥层：
- 00003 COO (claude-opus-5) - 24/7自动调度
- 00010 Tech Lead (claude-fable-5) - 按需工作

核心工程：
- 00006 高级后端 (claude-opus-5) - 24/7开发
- 00011 中级后端 (gpt-5.6-sol) - 24/7开发
- 00007 全栈 (claude-sonnet-5) - 24/7开发
- 00008 DevOps (gpt-5.6-sol) - 24/7监控

质量保障：
- 00009 QA Lead (claude-sonnet-5) - 24/7测试
- 00012 自动化测试 (gpt-5.6-sol) - 24/7测试

支持层：
- 00002 HR (deepseek-v4-flash) - 按需
- 00004 EA (gpt-5.6-sol) - 白天
- 00005 CSO (claude-sonnet-5) - 按需

### 项目目标
- 项目周期：31天
- 工作时长：24小时/天
- 总工时：12人 × 24小时 × 31天 = 8,928人·小时
- 产出：相当于人类团队3个月的工作量

### 项目阶段
- Day 0-2: 架构设计和任务分解
- Day 3-10: Sprint 1（1设备基本可用）
- Day 11-17: Sprint 2（4设备扩展）
- Day 18-24: Sprint 3（8设备完整）
- Day 25-31: Sprint 4（生产准备和验收）

## 执行确认

请所有相关员工确认收到并理解：

@00003 COO - 启动24小时自动调度
@00010 Tech Lead - 启动按需响应模式
@00006 高级后端 - 启动24小时开发模式
@00011 中级后端 - 启动24小时开发模式
@00007 全栈工程师 - 启动24小时开发模式
@00008 DevOps - 启动24小时监控模式
@00009 QA Lead - 启动24小时测试模式
@00012 自动化测试 - 启动夜间测试模式

## 启动时间
立即生效

## 项目开始
COO，请立即开始：
1. 将项目总体目标分解为100+个小任务
2. 建立任务依赖关系
3. 设置任务优先级
4. 开始第一轮任务分配
5. 启动自动调度循环

项目名称：云测试平台
项目ID：18b1e9d4a1fc
工作目录：/Users/hanzhen/Documents/云测试的项目
目标：完成生产级云测试平台，支持8设备并发测试

现在开始24小时不间断工作！
```

---

## Phase 6: 验证和监控

### 6.1 立即验证（前10分钟）

```bash
# 1. 检查员工状态
curl http://localhost:8000/api/employees | jq '.[] | {id, name, status}'

# 2. 检查任务队列
curl http://localhost:8000/api/state | jq '.active_tasks'

# 3. 检查COO是否开始工作
tail -f .onemancompany/company/human_resource/employees/00003/progress.log
```

### 6.2 第一天验证

**白天检查（每2-3小时）**：
- 查看员工工作状态
- 查看任务完成数量
- 检查是否有阻塞
- 处理需要决策的问题

**晚上检查（21:00）**：
- 阅读COO的日间报告
- 确认夜间任务计划
- 处理睡前需决策事项

**第二天早上（8:30）**：
- 阅读EA的早间简报
- 查看夜间完成的任务
- 确认今日优先级

### 6.3 持续监控

创建监控脚本：

```bash
#!/bin/bash
# scripts/monitor-24h-mode.sh

echo "=== OneManCompany 24小时模式监控 ==="
echo ""

# 员工状态
echo "📊 员工状态："
curl -s http://localhost:8000/api/employees | \
  jq -r '.[] | "\(.employee_number) \(.name): \(.runtime.status)"'
echo ""

# 任务统计
echo "📋 任务统计："
curl -s http://localhost:8000/api/state | \
  jq '{active_tasks, completed_today: .completed_tasks}'
echo ""

# 系统健康
echo "🏥 系统健康："
curl -s http://localhost:8000/api/health | jq '.'
echo ""

# COO最近活动
echo "🤖 COO最近活动："
tail -5 .onemancompany/company/human_resource/employees/00003/progress.log
echo ""

echo "✅ 监控完成"
```

运行监控：

```bash
chmod +x scripts/monitor-24h-mode.sh

# 每小时检查一次
watch -n 3600 ./scripts/monitor-24h-mode.sh
```

---

## 🎯 成功标志

### 第一天结束时应该看到：

- ✅ 12个员工全部显示为 `active` 或 `working`
- ✅ COO已完成至少1轮任务分配
- ✅ 至少有3-5个任务被完成
- ✅ 早间报告和晚间报告自动生成
- ✅ 夜间测试已执行
- ✅ 没有员工长时间空闲（>30分钟）

### 第一周结束时应该看到：

- ✅ Sprint 1 任务分解完成
- ✅ 架构设计文档生成
- ✅ 第一个API端点完成并测试通过
- ✅ CI/CD管道建立
- ✅ 每日早晚报告稳定生成

---

## 🚨 常见问题和解决

### 问题1：COO没有自动分配任务

**检查**：
```bash
# 查看COO状态
curl http://localhost:8000/api/employees/00003

# 查看COO日志
tail -100 .onemancompany/company/human_resource/employees/00003/progress.log
```

**解决**：
```
在 CEO Console 手动触发：
"COO，请开始自动任务调度"
```

### 问题2：员工一直空闲

**原因**：任务队列可能为空

**解决**：
```
"COO，请创建第一批任务并分配给团队"
```

### 问题3：夜间没有活动

**检查**：
```bash
# 查看夜间日志（第二天早上）
grep "21:00\|22:00\|23:00\|00:00\|01:00" \
  .onemancompany/logs/activity.log
```

**解决**：确认工作原则中 `available_24_7: true`

---

## 📞 获取帮助

如果遇到问题：
1. 查看 [故障排查指南](./troubleshooting.md)
2. 查看 [常见问题](./faq.md)
3. 查看系统日志
4. 联系社区支持

---

*最后更新：2026-08-12*
