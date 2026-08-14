#!/bin/bash
# 监控24小时工作模式

echo "=========================================="
echo "OneManCompany 24小时模式监控"
echo "时间: $(date)"
echo "=========================================="
echo ""

# 1. 员工状态
echo "📊 员工状态："
echo "---"
curl -s http://localhost:8000/api/employees 2>/dev/null | \
  jq -r '.[] | "\(.employee_number) \(.name): \(.runtime.status)"' 2>/dev/null || \
  echo "⚠️  无法获取员工状态"
echo ""

# 2. 任务统计
echo "📋 任务统计："
echo "---"
curl -s http://localhost:8000/api/state 2>/dev/null | \
  jq '{active_tasks, completed_today: .completed_tasks}' 2>/dev/null || \
  echo "⚠️  无法获取任务统计"
echo ""

# 3. 系统健康
echo "🏥 系统健康："
echo "---"
curl -s http://localhost:8000/api/health 2>/dev/null | \
  jq '.' 2>/dev/null || \
  echo "⚠️  无法获取健康状态"
echo ""

# 4. COO最近活动
echo "🤖 COO最近活动："
echo "---"
if [ -f ".onemancompany/company/human_resource/employees/00003/progress.log" ]; then
    tail -5 .onemancompany/company/human_resource/employees/00003/progress.log
else
    echo "⚠️  COO日志文件不存在"
fi
echo ""

# 5. 资源使用
echo "💻 资源使用："
echo "---"
echo "CPU: $(top -l 1 | grep "CPU usage" | awk '{print $3}' || echo "N/A")"
echo "内存: $(top -l 1 | grep "PhysMem" | awk '{print $2}' || echo "N/A")"
echo "磁盘: $(df -h . | tail -1 | awk '{print $5}' || echo "N/A") 使用率"
echo ""

echo "=========================================="
echo "✅ 监控完成"
echo "=========================================="
