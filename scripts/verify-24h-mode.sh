#!/bin/bash
# 验证24小时工作模式运行状态

echo "=========================================="
echo "OneManCompany 24小时模式验证"
echo "时间: $(date)"
echo "=========================================="
echo ""

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

check_pass() {
    echo "✅ $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

check_fail() {
    echo "❌ $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

check_warn() {
    echo "⚠️  $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# ========================================
# 1. 服务运行状态
# ========================================
echo "🚀 服务运行状态"
echo "---"

if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    check_pass "OneManCompany服务运行中"

    # 检查健康状态
    HEALTH=$(curl -s http://localhost:8000/api/health)
    if echo "$HEALTH" | grep -q '"status":"healthy"'; then
        check_pass "服务健康状态正常"
    else
        check_fail "服务健康状态异常"
    fi
else
    check_fail "OneManCompany服务未运行"
    echo ""
    echo "❌ 服务未运行，无法继续验证"
    exit 1
fi

echo ""

# ========================================
# 2. 员工状态验证
# ========================================
echo "👥 员工状态验证"
echo "---"

# 检查12个员工是否都存在
EMPLOYEE_COUNT=$(curl -s http://localhost:8000/api/employees 2>/dev/null | jq '. | length' 2>/dev/null || echo "0")

if [ "$EMPLOYEE_COUNT" -eq 12 ]; then
    check_pass "12个员工全部在线"
else
    check_fail "员工数量: $EMPLOYEE_COUNT/12"
fi

# 检查活跃员工数
ACTIVE_COUNT=$(curl -s http://localhost:8000/api/employees 2>/dev/null | \
    jq '[.[] | select(.runtime.status == "working" or .runtime.status == "active")] | length' 2>/dev/null || echo "0")

if [ "$ACTIVE_COUNT" -ge 3 ]; then
    check_pass "活跃员工数: $ACTIVE_COUNT"
else
    check_warn "活跃员工数较少: $ACTIVE_COUNT"
fi

# 检查COO是否活跃
COO_STATUS=$(curl -s http://localhost:8000/api/employees/00003 2>/dev/null | \
    jq -r '.runtime.status' 2>/dev/null || echo "unknown")

if [ "$COO_STATUS" == "working" ] || [ "$COO_STATUS" == "active" ]; then
    check_pass "COO状态: $COO_STATUS"
else
    check_fail "COO状态异常: $COO_STATUS"
fi

echo ""

# ========================================
# 3. 任务执行验证
# ========================================
echo "📋 任务执行验证"
echo "---"

# 检查活跃任务
ACTIVE_TASKS=$(curl -s http://localhost:8000/api/state 2>/dev/null | \
    jq '.active_tasks' 2>/dev/null || echo "0")

if [ "$ACTIVE_TASKS" -gt 0 ]; then
    check_pass "活跃任务数: $ACTIVE_TASKS"
else
    check_warn "当前无活跃任务"
fi

# 检查今日完成任务
COMPLETED_TODAY=$(curl -s http://localhost:8000/api/state 2>/dev/null | \
    jq '.completed_today' 2>/dev/null || echo "0")

if [ "$COMPLETED_TODAY" -gt 0 ]; then
    check_pass "今日完成任务: $COMPLETED_TODAY"
else
    check_warn "今日尚未完成任务"
fi

echo ""

# ========================================
# 4. 自动化任务验证
# ========================================
echo "🤖 自动化任务验证"
echo "---"

# 检查COO最近活动
if [ -f ".onemancompany/company/human_resource/employees/00003/progress.log" ]; then
    LAST_COO_ACTIVITY=$(tail -1 .onemancompany/company/human_resource/employees/00003/progress.log | \
        grep -o '[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\} [0-9]\{2\}:[0-9]\{2\}' | head -1)

    if [ -n "$LAST_COO_ACTIVITY" ]; then
        check_pass "COO最近活动: $LAST_COO_ACTIVITY"
    else
        check_warn "无法解析COO活动时间"
    fi
else
    check_fail "COO日志文件不存在"
fi

# 检查今日报告
TODAY=$(date +%Y-%m-%d)
if [ -f "reports/daily/morning_briefing_${TODAY}.md" ]; then
    check_pass "今日早间报告已生成"
else
    check_warn "今日早间报告未生成"
fi

if [ -f "reports/daily/evening_briefing_${TODAY}.md" ]; then
    check_pass "今日晚间报告已生成"
else
    check_warn "今日晚间报告未生成（可能还未到晚上）"
fi

echo ""

# ========================================
# 5. 夜间测试验证（如果是早上）
# ========================================
HOUR=$(date +%H)
if [ "$HOUR" -ge 8 ] && [ "$HOUR" -lt 12 ]; then
    echo "🧪 夜间测试验证"
    echo "---"

    YESTERDAY=$(date -v-1d +%Y-%m-%d 2>/dev/null || date -d "yesterday" +%Y-%m-%d 2>/dev/null)

    if [ -f "reports/tests/regression_${YESTERDAY}.html" ]; then
        check_pass "昨夜回归测试已完成"
    else
        check_warn "昨夜回归测试报告未找到"
    fi

    if [ -f "reports/tests/performance_${YESTERDAY}.html" ]; then
        check_pass "昨夜性能测试已完成"
    else
        check_warn "昨夜性能测试报告未找到"
    fi

    echo ""
fi

# ========================================
# 6. 资源使用验证
# ========================================
echo "💻 资源使用验证"
echo "---"

# CPU使用率（macOS）
CPU_USAGE=$(top -l 1 | grep "CPU usage" | awk '{print $3}' | sed 's/%//' || echo "N/A")
if [ "$CPU_USAGE" != "N/A" ]; then
    if [ "$(echo "$CPU_USAGE < 80" | bc 2>/dev/null || echo 1)" -eq 1 ]; then
        check_pass "CPU使用率: ${CPU_USAGE}%"
    else
        check_warn "CPU使用率较高: ${CPU_USAGE}%"
    fi
fi

# 磁盘使用率
DISK_USAGE=$(df -h . | tail -1 | awk '{print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 90 ]; then
    check_pass "磁盘使用率: ${DISK_USAGE}%"
else
    check_warn "磁盘使用率较高: ${DISK_USAGE}%"
fi

echo ""

# ========================================
# 7. 配置一致性验证
# ========================================
echo "⚙️  配置一致性验证"
echo "---"

# 检查关键员工模型配置
check_employee_model() {
    local emp_id=$1
    local expected=$2
    local actual=$(grep "llm_model:" .onemancompany/company/human_resource/employees/$emp_id/profile.yaml 2>/dev/null | awk '{print $2}')

    if [ "$actual" == "$expected" ]; then
        check_pass "$emp_id 模型配置正确: $actual"
    else
        check_fail "$emp_id 模型配置错误: $actual (期望: $expected)"
    fi
}

check_employee_model "00003" "claude-opus-5"
check_employee_model "00006" "claude-opus-5"
check_employee_model "00010" "claude-fable-5"

echo ""

# ========================================
# 总结
# ========================================
echo "=========================================="
echo "📊 验证总结"
echo "=========================================="
echo "通过: $PASS_COUNT"
echo "警告: $WARN_COUNT"
echo "失败: $FAIL_COUNT"
echo ""

# 判断整体状态
if [ $FAIL_COUNT -eq 0 ]; then
    if [ $WARN_COUNT -eq 0 ]; then
        echo "✅ 24小时模式运行完美！"
        EXIT_CODE=0
    else
        echo "⚠️  24小时模式运行正常，但有 $WARN_COUNT 个警告"
        EXIT_CODE=0
    fi
else
    echo "❌ 24小时模式存在 $FAIL_COUNT 个问题，需要处理"
    EXIT_CODE=1
fi

echo ""
echo "详细日志:"
echo "- 员工状态: curl http://localhost:8000/api/employees"
echo "- 任务状态: curl http://localhost:8000/api/state"
echo "- COO日志: tail -f .onemancompany/company/human_resource/employees/00003/progress.log"
echo "- 报告目录: ls -la reports/daily/"
echo ""

exit $EXIT_CODE
