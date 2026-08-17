# 24小时工作模式 - 快速操作指南

> 所有常用命令和操作的快速参考

---

## 🚀 快速开始

### 检查当前状态

```bash
# 完整系统检查
./scripts/check-system-ready.sh

# 实时监控
./scripts/monitor-24h-mode.sh

# 完整验证（服务运行时）
./scripts/verify-24h-mode.sh
```

### 应用配置

```bash
# 应用所有员工工作原则
./scripts/apply-work-principles.sh

# 验证应用结果
for i in {2..12}; do
    id=$(printf "%05d" $i)
    [ -f ".onemancompany/company/human_resource/employees/$id/work_principles.md" ] && echo "✅ $id" || echo "❌ $id"
done
```

---

## 📁 文档快速访问

### 核心文档

```bash
# 总览（从这里开始）
cat docs/24h-work-mode/README.md | less

# 团队配置详情
cat docs/24h-work-mode/team-configuration.md | less

# 启动指南（分步操作）
cat docs/24h-work-mode/startup-guide.md | less

# 验证清单
cat docs/24h-work-mode/verification-checklist.md | less

# 当前状态报告
cat docs/24h-work-mode/STATUS-REPORT.md | less

# 修复总结
cat docs/24h-work-mode/FIX-SUMMARY.md | less

# 文档索引（查找文档）
cat docs/24h-work-mode/DOCUMENT-INDEX.md | less
```

### 员工工作原则

```bash
# 查看所有员工工作原则
ls -1 docs/employee-work-principles/

# 查看特定员工（例如COO）
cat docs/employee-work-principles/00003-coo-work-principles.md | less

# 批量查看（在编辑器中打开）
open docs/employee-work-principles/
```

### 自动化配置

```bash
# 查看所有自动化任务
cat docs/automation/cron-tasks.yaml | less

# 验证YAML格式
.venv/bin/python << 'EOF'
import yaml
with open('docs/automation/cron-tasks.yaml') as f:
    data = yaml.safe_load(f)
    print(f"✅ {len(data['cron_tasks'])} 个任务")
    for task in data['cron_tasks']:
        print(f"  - {task['name']} ({task['schedule']})")
EOF
```

---

## 👥 员工管理

### 查看员工状态

```bash
# 列出所有员工目录
ls -1 .onemancompany/company/human_resource/employees/

# 查看员工数量
ls -1d .onemancompany/company/human_resource/employees/*/ | wc -l

# 查看特定员工配置
cat .onemancompany/company/human_resource/employees/00003/profile.yaml
```

### 检查员工模型配置

```bash
# 查看所有员工的模型
for id in {1..12}; do
    emp_id=$(printf "%05d" $id)
    if [ -f ".onemancompany/company/human_resource/employees/$emp_id/profile.yaml" ]; then
        model=$(grep "llm_model:" ".onemancompany/company/human_resource/employees/$emp_id/profile.yaml" | awk '{print $2}')
        role=$(grep "role:" ".onemancompany/company/human_resource/employees/$emp_id/profile.yaml" | awk '{print $2}')
        echo "$emp_id: $role - $model"
    fi
done
```

### 修改员工配置

```bash
# 方法1：通过UI（推荐）
open http://localhost:8000

# 方法2：直接编辑配置文件
vim .onemancompany/company/human_resource/employees/00003/profile.yaml

# 修改后重启服务生效
```

---

## 🔧 系统操作

### 服务管理

```bash
# 启动服务
bash start.sh

# 检查服务状态
curl http://localhost:8000/api/health | jq '.'

# 查看员工列表
curl http://localhost:8000/api/employees | jq '.[] | {id: .employee_number, name: .name, status: .runtime.status}'

# 查看任务状态
curl http://localhost:8000/api/state | jq '{active_tasks, completed_today}'
```

### 日志查看

```bash
# 查看COO日志
tail -f .onemancompany/company/human_resource/employees/00003/progress.log

# 查看所有员工最近活动
for id in {1..12}; do
    emp_id=$(printf "%05d" $id)
    if [ -f ".onemancompany/company/human_resource/employees/$emp_id/progress.log" ]; then
        echo "=== $emp_id ==="
        tail -3 ".onemancompany/company/human_resource/employees/$emp_id/progress.log"
        echo ""
    fi
done

# 查看系统日志
tail -f .onemancompany/logs/activity.log
```

---

## 💾 备份和恢复

### 完整备份

```bash
# 执行完整备份
./docs/automation/backup-scripts/backup-all.sh

# 查看备份列表
ls -lh backups/db/
ls -lh backups/employees/
ls -lh backups/projects/

# 查看备份日志
cat backups/db/backup_log.txt
```

### 恢复数据

```bash
# 1. 查看可用备份
ls -1 backups/employees/ | grep "employees_" | sed 's/employees_//' | sed 's/.tar.gz//'

# 2. 恢复指定时间点的备份
./docs/automation/backup-scripts/restore.sh YYYYMMDD_HHMMSS

# 例如：
./docs/automation/backup-scripts/restore.sh 20260813_140000
```

### 仅备份数据库

```bash
# 在线备份（推荐，服务运行时）
curl -X POST http://localhost:8000/api/admin/runtime/backup \
  -o backups/db/runtime_$(date +%Y%m%d_%H%M%S).sqlite3

# 离线备份（服务停止时）
cp .onemancompany/data/runtime.sqlite3 \
   backups/db/runtime_$(date +%Y%m%d_%H%M%S).sqlite3
```

---

## 📊 监控和报告

### 实时监控

```bash
# 一次性检查
./scripts/monitor-24h-mode.sh

# 持续监控（每小时刷新）
watch -n 3600 ./scripts/monitor-24h-mode.sh

# 持续监控（每10分钟刷新）
watch -n 600 ./scripts/monitor-24h-mode.sh
```

### 查看每日报告

```bash
# 查看今日早间报告
cat reports/daily/morning_briefing_$(date +%Y-%m-%d).md

# 查看今日早间简报（EA整理）
cat reports/daily/morning_brief_$(date +%Y-%m-%d).md

# 查看昨晚晚间报告
cat reports/daily/evening_briefing_$(date -v-1d +%Y-%m-%d).md

# 列出所有报告
ls -lh reports/daily/
```

### 查看测试报告

```bash
# 列出所有测试报告
ls -lh reports/tests/

# 查看最新回归测试
ls -t reports/tests/regression_*.html | head -1 | xargs open

# 查看最新性能测试
ls -t reports/tests/performance_*.html | head -1 | xargs open

# 查看最新设备兼容性测试
ls -t reports/tests/device_compatibility_*.html | head -1 | xargs open
```

---

## 🛠️ 故障排查

### 检查系统问题

```bash
# 完整系统检查
./scripts/check-system-ready.sh

# 检查服务健康
curl http://localhost:8000/api/health

# 检查数据库连接
.venv/bin/python << 'EOF'
import sqlite3
conn = sqlite3.connect('.onemancompany/data/runtime.sqlite3')
print(f"✅ 数据库连接成功")
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM checkpoints")
print(f"Checkpoints 数量: {cursor.fetchone()[0]}")
conn.close()
EOF
```

### 检查员工问题

```bash
# 检查空闲员工
curl -s http://localhost:8000/api/employees | \
  jq '.[] | select(.runtime.status == "idle") | {id: .employee_number, name: .name}'

# 检查长时间运行的任务
curl -s http://localhost:8000/api/state | \
  jq '.active_tasks[] | select(.duration > 3600)'

# 查看员工错误日志
grep -i "error" .onemancompany/company/human_resource/employees/*/progress.log
```

### 清理和重置

```bash
# 清理旧日志（保留最近7天）
find .onemancompany/logs/ -name "*.log" -mtime +7 -delete

# 清理旧报告（保留最近14天）
find reports/ -name "*.html" -mtime +14 -delete
find reports/ -name "*.md" -mtime +14 -delete

# 清理旧备份（保留最近7天）
find backups/ -name "*.tar.gz" -mtime +7 -delete
find backups/ -name "*.sqlite3" -mtime +7 -delete
```

---

## 📝 常见任务

### 招募新员工

在 CEO Console 执行：

```
HR（00002），请从人才市场招募一名员工：

员工ID: 00011
角色: 中级后端工程师
职责: 辅助后端开发，负责中等复杂度的API开发和单元测试
要求: 熟悉 Python/FastAPI，有数据库经验
技能: API开发、数据库、单元测试、代码重构
工作模式: 24/7 全天候工作
配置模型: gpt-5.6-sol
汇报对象: 00006 (高级后端工程师)

请在今天完成招募。
```

### 修改员工模型

```bash
# 1. 编辑配置文件
vim .onemancompany/company/human_resource/employees/00003/profile.yaml

# 2. 找到并修改这一行：
# llm_model: gpt-5.6-sol
# 改为：
# llm_model: gpt-5.6-sol

# 3. 保存并重启服务
```

### 启动24小时模式

在 CEO Console 执行启动指令（完整内容见 `startup-guide.md`）：

```
启动24小时不间断工作模式。

## 系统配置

从现在开始，本公司实行24/7全天候工作制：
...
（参考 docs/24h-work-mode/startup-guide.md 的完整指令）
```

---

## 🔍 调试技巧

### 查看详细的执行日志

```bash
# 启用调试模式
export OMC_DEBUG=true

# 查看详细日志
tail -f .onemancompany/logs/debug.log
```

### 验证YAML配置

```bash
# 验证cron-tasks.yaml
.venv/bin/python << 'EOF'
import yaml
try:
    with open('docs/automation/cron-tasks.yaml') as f:
        data = yaml.safe_load(f)
    print(f"✅ YAML有效，包含 {len(data['cron_tasks'])} 个任务")
except Exception as e:
    print(f"❌ YAML错误: {e}")
EOF
```

### 测试单个脚本

```bash
# 测试语法
bash -n scripts/check-system-ready.sh

# 调试模式运行
bash -x scripts/check-system-ready.sh
```

---

## 📞 获取帮助

### 查看文档

```bash
# 查看所有可用文档
find docs -name "*.md" | sort

# 搜索关键词
grep -r "关键词" docs/

# 查看文档索引
cat docs/24h-work-mode/DOCUMENT-INDEX.md
```

### 检查状态

```bash
# 当前状态报告
cat docs/24h-work-mode/STATUS-REPORT.md

# 修复总结
cat docs/24h-work-mode/FIX-SUMMARY.md
```

---

## ⚡ 速查表

### 一行命令

```bash
# 系统检查
./scripts/check-system-ready.sh && echo "✅ 系统就绪" || echo "❌ 系统未就绪"

# 验证24小时模式
./scripts/verify-24h-mode.sh && echo "✅ 运行正常" || echo "❌ 存在问题"

# 备份
./docs/automation/backup-scripts/backup-all.sh > /dev/null 2>&1 && echo "✅ 备份完成"

# 员工数量
echo "员工数量: $(ls -1d .onemancompany/company/human_resource/employees/*/ 2>/dev/null | wc -l)/12"

# 活跃任务数
echo "活跃任务: $(curl -s http://localhost:8000/api/state | jq '.active_tasks' 2>/dev/null || echo 0)"

# 今日完成
echo "今日完成: $(curl -s http://localhost:8000/api/state | jq '.completed_today' 2>/dev/null || echo 0)"
```

---

*最后更新：2026-08-13*  
*版本：1.0*
