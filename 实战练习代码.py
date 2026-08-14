#!/usr/bin/env python3
"""
OneManCompany 实战练习代码
包含 10 个渐进式练习，从简单到复杂
"""

import asyncio
import json
from pathlib import Path
from typing import Any

# ============================================================================
# 练习 1：理解 ScheduleEntry
# ============================================================================

def exercise_1_schedule_entry():
    """练习 1：理解调度入口的数据结构"""
    print("=" * 60)
    print("练习 1：ScheduleEntry - 纯指针设计")
    print("=" * 60)

    from onemancompany.core.vessel import ScheduleEntry

    # 创建调度入口
    entry = ScheduleEntry(
        node_id="T001",
        tree_path="/path/to/project/task_tree.yaml"
    )

    print(f"节点 ID: {entry.node_id}")
    print(f"树路径: {entry.tree_path}")
    print(f"数据大小: {entry.__sizeof__()} bytes")

    print("\n💡 关键点：")
    print("- ScheduleEntry 只存指针，不存业务数据")
    print("- 这样调度队列始终保持轻量级")
    print("- 业务数据从 task_tree.yaml 按需加载")

    return entry


# ============================================================================
# 练习 2：模拟任务状态转换
# ============================================================================

def exercise_2_task_lifecycle():
    """练习 2：任务生命周期状态机"""
    print("\n" + "=" * 60)
    print("练习 2：任务状态机模拟")
    print("=" * 60)

    from onemancompany.core.task_lifecycle import TaskPhase, safe_transition

    # 模拟一个任务的完整生命周期
    class MockNode:
        def __init__(self):
            self.status = TaskPhase.PENDING.value

        def set_status(self, phase: TaskPhase):
            self.status = phase.value

    node = MockNode()

    transitions = [
        (TaskPhase.PROCESSING, "开始执行"),
        (TaskPhase.COMPLETED, "执行完成，等待验收"),
        (TaskPhase.ACCEPTED, "上级验收通过"),
    ]

    for phase, description in transitions:
        if safe_transition(node, phase):
            node.set_status(phase)
            print(f"✓ {phase.value.upper()} - {description}")
        else:
            print(f"✗ 不能从 {node.status} 转到 {phase.value}")

    print("\n💡 关键点：")
    print("- 状态转换有严格的规则（不能跳跃）")
    print("- COMPLETED 必须经过上级 accept 才能到 ACCEPTED")
    print("- HOLDING 可以恢复到 PROCESSING")

    return node


# ============================================================================
# 练习 3：Stall Detection 测试
# ============================================================================

def exercise_3_stall_detection():
    """练习 3：停滞检测算法"""
    print("\n" + "=" * 60)
    print("练习 3：Stall Detection - 防止 Agent 空话")
    print("=" * 60)

    from onemancompany.core.vessel import (
        detect_unfulfilled_promises,
        detect_unverified_dispatch_claim,
        _dispatch_claim_details,
    )

    # 测试案例
    test_cases = [
        {
            "name": "未兑现的承诺",
            "output": "我将立即分配 5 个子任务给团队成员",
            "children": [],
            "expected_unfulfilled": True,
            "expected_unverified": False,
        },
        {
            "name": "虚假的分派声明",
            "output": "已成功分配 8 个子任务，分别负责不同模块",
            "children": ["T001", "T002"],
            "expected_unfulfilled": False,
            "expected_unverified": True,
        },
        {
            "name": "真实的分派",
            "output": "已分配 2 个子任务：T001 负责前端，T002 负责后端",
            "children": ["T001", "T002"],
            "expected_unfulfilled": False,
            "expected_unverified": False,
        },
        {
            "name": "正常完成",
            "output": "任务已完成，代码已提交到仓库",
            "children": [],
            "expected_unfulfilled": False,
            "expected_unverified": False,
        },
    ]

    for i, case in enumerate(test_cases, 1):
        print(f"\n{i}. {case['name']}")
        print(f"   输出: {case['output'][:50]}...")
        print(f"   子任务数: {len(case['children'])}")

        unfulfilled = detect_unfulfilled_promises(case["output"])
        unverified = detect_unverified_dispatch_claim(
            case["output"],
            case["children"],
            verified_child_ids=case["children"]
        )

        claim = _dispatch_claim_details(case["output"])
        if claim:
            print(f"   声称数量: {claim[0]}，仅新增: {claim[1]}")

        status = "✓ 通过" if (
            unfulfilled == case["expected_unfulfilled"] and
            unverified == case["expected_unverified"]
        ) else "✗ 失败"

        print(f"   未兑现承诺: {unfulfilled}")
        print(f"   未验证声明: {unverified}")
        print(f"   {status}")

    print("\n💡 关键点：")
    print("- 检测 '我将' '接下来' 等未来时态")
    print("- 检测 '已分配 N 个' 但实际子任务少于 N")
    print("- 最多重试 2 次，避免无限循环")


# ============================================================================
# 练习 4：Progress Log 读写
# ============================================================================

def exercise_4_progress_log():
    """练习 4：工作记忆的读写"""
    print("\n" + "=" * 60)
    print("练习 4：Progress Log - 跨任务持久化记忆")
    print("=" * 60)

    from onemancompany.core.vessel import _append_progress, _load_progress
    from onemancompany.core.config import EMPLOYEES_DIR
    from datetime import datetime

    test_employee_id = "99999"

    # 写入一些进度条目
    entries = [
        "开始开发用户登录模块",
        "实现了 JWT token 生成逻辑",
        "添加了密码加密功能",
        "完成单元测试，覆盖率 85%",
        "修复了一个边界条件的 bug",
    ]

    print("写入进度日志:")
    for entry in entries:
        _append_progress(test_employee_id, entry)
        print(f"  + {entry}")

    # 读取
    print("\n读取进度日志 (最近 3 行):")
    recent = _load_progress(test_employee_id, max_lines=3)
    for line in recent.split("\n"):
        print(f"  {line}")

    # 显示文件位置
    log_path = EMPLOYEES_DIR / test_employee_id / "progress.log"
    print(f"\n文件路径: {log_path}")
    print(f"文件大小: {log_path.stat().st_size if log_path.exists() else 0} bytes")

    print("\n💡 关键点：")
    print("- 每条记录自动加时间戳")
    print("- 跨任务保留，形成员工的工作上下文")
    print("- 注入到 Agent 提示词，帮助连续性思考")
    print("- 最近 30 行进入上下文（避免过长）")


# ============================================================================
# 练习 5：自定义 ScriptExecutor
# ============================================================================

async def exercise_5_custom_launcher():
    """练习 5：实现自定义 Launcher"""
    print("\n" + "=" * 60)
    print("练习 5：自定义 ScriptExecutor - 接入外部 Agent")
    print("=" * 60)

    from onemancompany.core.vessel import ScriptExecutor, TaskContext, LaunchResult
    from onemancompany.core.config import EMPLOYEES_DIR

    test_employee_id = "99998"

    # 创建测试脚本
    script_dir = EMPLOYEES_DIR / test_employee_id
    script_dir.mkdir(parents=True, exist_ok=True)

    script_path = script_dir / "launch.sh"
    script_content = """#!/bin/bash
# 简单的 echo agent

read -r task

echo "=== Custom Agent Response ==="
echo "收到任务: $task"
echo ""
echo "执行步骤:"
echo "1. 分析需求"
echo "2. 设计方案"
echo "3. 实现代码"
echo "4. 测试验证"
echo ""
echo "状态: 已完成"
"""

    script_path.write_text(script_content)
    script_path.chmod(0o755)

    print(f"✓ 创建脚本: {script_path}")

    # 测试执行
    executor = ScriptExecutor(test_employee_id, str(script_path))
    context = TaskContext(
        project_id="test_001",
        work_dir=str(script_dir),
        employee_id=test_employee_id,
        task_id="T001"
    )

    print("\n执行任务: '开发一个计算器应用'")
    result = await executor.execute("开发一个计算器应用", context)

    print("\n执行结果:")
    print(result.output)

    if result.error:
        print(f"错误: {result.error}")

    print("\n💡 关键点：")
    print("- ScriptExecutor 通过 stdin/stdout 与脚本通信")
    print("- 脚本可以是任何语言（Python, Go, Rust...）")
    print("- 适合接入已有的 Agent 系统")
    print("- 600 秒超时保护")


# ============================================================================
# 练习 6：EventBus 发布订阅
# ============================================================================

async def exercise_6_event_bus():
    """练习 6：事件总线的使用"""
    print("\n" + "=" * 60)
    print("练习 6：EventBus - 异步发布订阅")
    print("=" * 60)

    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.models import EventType

    # 订阅者
    received_events = []

    async def task_completed_handler(event: CompanyEvent):
        received_events.append(event)
        print(f"  收到事件: {event.type.value}")
        print(f"  员工: {event.employee_id}")
        print(f"  数据: {event.data}")

    # 订阅
    event_bus.subscribe(EventType.TASK_COMPLETED, task_completed_handler)
    print("✓ 订阅 TASK_COMPLETED 事件")

    # 发布事件
    print("\n发布 3 个事件:")
    for i in range(3):
        await event_bus.publish(CompanyEvent(
            type=EventType.TASK_COMPLETED,
            employee_id=f"0000{i+1}",
            data={"task_id": f"T{i+1:03d}", "result": f"任务 {i+1} 完成"}
        ))

    # 等待处理
    await asyncio.sleep(0.1)

    print(f"\n处理了 {len(received_events)} 个事件")

    print("\n💡 关键点：")
    print("- 事件是异步传播的（不阻塞发布者）")
    print("- 一个事件可以有多个订阅者")
    print("- WebSocket 通过订阅事件推送到前端")
    print("- 解耦：发布者不需要知道订阅者")


# ============================================================================
# 练习 7：任务树构建
# ============================================================================

def exercise_7_task_tree():
    """练习 7：手动构建任务树"""
    print("\n" + "=" * 60)
    print("练习 7：TaskTree - 构建任务层级")
    print("=" * 60)

    from onemancompany.core.task_tree import TaskTree, TaskNode
    from onemancompany.core.task_lifecycle import TaskPhase
    import tempfile

    # 创建树
    tree = TaskTree(mode="standard")
    tree.project_id = "demo_project"

    # 根任务
    root = TaskNode(
        id="T001",
        description="开发一个博客系统",
        employee_id="00002",  # COO
        status=TaskPhase.PROCESSING.value,
    )
    tree.add_node(root)
    tree.root_id = root.id

    # 子任务 1
    child1 = TaskNode(
        id="T002",
        parent_id="T001",
        description="设计数据库模型",
        employee_id="00015",
        status=TaskPhase.ACCEPTED.value,
        result="完成了 User, Post, Comment 三个表的设计",
    )
    tree.add_node(child1)
    root.children_ids.append(child1.id)

    # 子任务 2（依赖子任务 1）
    child2 = TaskNode(
        id="T003",
        parent_id="T001",
        description="实现 RESTful API",
        employee_id="00021",
        status=TaskPhase.PROCESSING.value,
        depends_on=["T002"],
    )
    tree.add_node(child2)
    root.children_ids.append(child2.id)

    # 子任务 3（依赖子任务 2）
    child3 = TaskNode(
        id="T004",
        parent_id="T001",
        description="开发前端页面",
        employee_id="00022",
        status=TaskPhase.PENDING.value,
        depends_on=["T003"],
    )
    tree.add_node(child3)
    root.children_ids.append(child3.id)

    # 显示树结构
    print("任务树结构:")
    print(f"  {root.id}: {root.description} [{root.status}]")
    for child_id in root.children_ids:
        child = tree.get_node(child_id)
        deps = f" (依赖: {', '.join(child.depends_on)})" if child.depends_on else ""
        print(f"    ├─ {child.id}: {child.description} [{child.status}]{deps}")

    # 检查依赖
    print("\n依赖检查:")
    for node_id in ["T002", "T003", "T004"]:
        resolved = tree.all_deps_resolved(node_id)
        status = "✓ 可执行" if resolved else "✗ 阻塞"
        print(f"  {node_id}: {status}")

    # 保存到临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        tree_path = Path(f.name)

    tree.save(tree_path)
    print(f"\n✓ 保存到: {tree_path}")
    print(f"文件大小: {tree_path.stat().st_size} bytes")

    # 读取内容
    print("\nYAML 内容预览:")
    content = tree_path.read_text()
    for line in content.split('\n')[:20]:
        print(f"  {line}")
    print("  ...")

    print("\n💡 关键点：")
    print("- 树是层级结构：root → children → grandchildren")
    print("- depends_on 控制执行顺序")
    print("- 每个节点有独立的状态")
    print("- YAML 格式便于 Git 版本控制")

    return tree


# ============================================================================
# 练习 8：模拟完整调度流程
# ============================================================================

async def exercise_8_scheduling_flow():
    """练习 8：模拟 EmployeeManager 调度"""
    print("\n" + "=" * 60)
    print("练习 8：完整调度流程模拟")
    print("=" * 60)

    from onemancompany.core.vessel import (
        EmployeeManager,
        ScheduleEntry,
        Launcher,
        TaskContext,
        LaunchResult
    )

    # Mock Launcher
    class MockLauncher(Launcher):
        def __init__(self, name: str):
            self.name = name
            self.executed = []

        async def execute(self, task_description: str, context: TaskContext,
                         on_log=None) -> LaunchResult:
            self.executed.append(task_description)
            print(f"    [{self.name}] 执行: {task_description}")
            await asyncio.sleep(0.1)  # 模拟执行时间
            return LaunchResult(
                output=f"[{self.name}] 完成任务: {task_description}",
                model_used="mock-model",
                input_tokens=100,
                output_tokens=50,
            )

    # 创建 Manager
    manager = EmployeeManager()

    # 注册员工
    employees = {
        "emp_001": "Alice",
        "emp_002": "Bob",
        "emp_003": "Charlie",
    }

    print("注册员工:")
    for emp_id, name in employees.items():
        launcher = MockLauncher(name)
        vessel = manager.register(emp_id, launcher)
        print(f"  ✓ {emp_id} ({name})")

    # 推送任务
    print("\n推送任务:")
    tasks = [
        ("emp_001", "编写需求文档"),
        ("emp_002", "实现后端 API"),
        ("emp_001", "编写测试用例"),
        ("emp_003", "部署到生产环境"),
    ]

    for emp_id, description in tasks:
        # 注意：实际使用需要先创建 TaskNode
        print(f"  → {employees[emp_id]}: {description}")
        # manager.push_task(emp_id, description, ...)

    # 模拟调度（简化版）
    print("\n模拟调度过程:")
    for emp_id, description in tasks:
        print(f"\n[调度] {employees[emp_id]}")
        launcher = manager.executors[emp_id]
        result = await launcher.execute(
            description,
            TaskContext(employee_id=emp_id, task_id=f"T{len(launcher.executed)}")
        )
        print(f"  ✓ {result.output}")
        print(f"  tokens: {result.input_tokens} + {result.output_tokens}")

    # 统计
    print("\n执行统计:")
    for emp_id, name in employees.items():
        launcher = manager.executors[emp_id]
        print(f"  {name}: 完成 {len(launcher.executed)} 个任务")

    print("\n💡 关键点：")
    print("- EmployeeManager 是全局单例")
    print("- 每个员工一个 Launcher")
    print("- 任务按 FIFO 顺序执行")
    print("- 完成后自动调度下一个")


# ============================================================================
# 练习 9：依赖解析
# ============================================================================

def exercise_9_dependency_resolution():
    """练习 9：任务依赖解析"""
    print("\n" + "=" * 60)
    print("练习 9：任务依赖解析 - 确保执行顺序")
    print("=" * 60)

    from onemancompany.core.task_tree import TaskTree, TaskNode
    from onemancompany.core.task_lifecycle import TaskPhase

    # 创建带依赖的任务树
    tree = TaskTree(mode="standard")

    nodes_data = [
        ("T001", None, [], TaskPhase.ACCEPTED),      # 需求分析
        ("T002", "T001", ["T001"], TaskPhase.ACCEPTED),  # 数据库设计（依赖 T001）
        ("T003", "T001", ["T001"], TaskPhase.PROCESSING),  # UI 设计（依赖 T001）
        ("T004", "T001", ["T002"], TaskPhase.PENDING),  # 后端开发（依赖 T002）
        ("T005", "T001", ["T003"], TaskPhase.PENDING),  # 前端开发（依赖 T003）
        ("T006", "T001", ["T004", "T005"], TaskPhase.PENDING),  # 集成测试（依赖 T004+T005）
    ]

    descriptions = {
        "T001": "需求分析",
        "T002": "数据库设计",
        "T003": "UI 设计",
        "T004": "后端开发",
        "T005": "前端开发",
        "T006": "集成测试",
    }

    for node_id, parent_id, depends_on, status in nodes_data:
        node = TaskNode(
            id=node_id,
            parent_id=parent_id,
            description=descriptions[node_id],
            employee_id="00021",
            status=status.value,
            depends_on=depends_on,
        )
        tree.add_node(node)

    tree.root_id = "T001"

    # 显示依赖图
    print("任务依赖图:")
    print("  T001 (需求分析) [ACCEPTED]")
    print("    ├─ T002 (数据库设计) [ACCEPTED]")
    print("    │   └─ T004 (后端开发) [PENDING]")
    print("    ├─ T003 (UI 设计) [PROCESSING]")
    print("    │   └─ T005 (前端开发) [PENDING]")
    print("    └─ T006 (集成测试) [PENDING] ← 依赖 T004 + T005")

    # 检查哪些任务可以执行
    print("\n可执行性检查:")
    for node_id in ["T002", "T003", "T004", "T005", "T006"]:
        node = tree.get_node(node_id)
        can_run = tree.all_deps_resolved(node_id)

        # 分析依赖状态
        dep_status = []
        for dep_id in node.depends_on:
            dep = tree.get_node(dep_id)
            dep_status.append(f"{dep_id}={dep.status}")

        deps_str = f" (依赖: {', '.join(dep_status)})" if dep_status else ""
        status_icon = "✓" if can_run else "✗"

        print(f"  {status_icon} {node_id} [{node.status}]{deps_str}")

    print("\n💡 关键点：")
    print("- 依赖必须在 ACCEPTED 状态才算 resolved")
    print("- PROCESSING 不算 resolved（可能失败）")
    print("- 多个依赖需要全部满足")
    print("- 依赖检查在调度时自动进行")


# ============================================================================
# 练习 10：综合实战 - 模拟项目执行
# ============================================================================

async def exercise_10_full_simulation():
    """练习 10：完整项目执行模拟"""
    print("\n" + "=" * 60)
    print("练习 10：综合实战 - 完整项目执行流程")
    print("=" * 60)

    print("场景：CEO 要求开发一个 TODO 应用")
    print("\n执行流程:")

    steps = [
        ("1. CEO 输入", "开发一个简单的 TODO 应用"),
        ("2. EA 接收", "任务路由到 COO"),
        ("3. COO 分解", "创建 3 个子任务：设计、开发、测试"),
        ("4. 设计师执行", "完成 UI 设计稿"),
        ("5. COO 验收设计", "设计通过，分派开发任务"),
        ("6. 工程师执行", "实现前后端代码"),
        ("7. QA 测试", "发现 2 个 bug"),
        ("8. 工程师修复", "bug 已修复"),
        ("9. QA 回归测试", "测试通过"),
        ("10. COO 总结", "项目完成，提交 EA"),
        ("11. EA 复核", "质量合格，提交 CEO"),
        ("12. CEO 审批", "验收通过"),
    ]

    for step, description in steps:
        print(f"  {step}: {description}")
        await asyncio.sleep(0.3)  # 模拟执行延迟

    print("\n任务树结构（最终状态）:")
    tree_structure = """
  T001 [ACCEPTED] - 开发 TODO 应用 (COO)
    ├─ T002 [ACCEPTED] - UI 设计 (设计师)
    ├─ T003 [ACCEPTED] - 开发实现 (工程师)
    │   ├─ T004 [ACCEPTED] - 前端开发
    │   └─ T005 [ACCEPTED] - 后端 API
    ├─ T006 [ACCEPTED] - 测试 (QA)
    │   ├─ T007 [ACCEPTED] - 功能测试
    │   └─ T008 [ACCEPTED] - 回归测试
    └─ T009 [ACCEPTED] - Bug 修复 (工程师)
"""
    print(tree_structure)

    print("项目统计:")
    print("  总任务数: 9")
    print("  参与人数: 4 (COO, 设计师, 工程师, QA)")
    print("  总耗时: 约 2 小时")
    print("  LLM 调用: 15 次")
    print("  总成本: $0.24 USD")

    print("\n交付物:")
    deliverables = [
        "✓ frontend/index.html",
        "✓ frontend/app.js",
        "✓ frontend/styles.css",
        "✓ backend/api.py",
        "✓ backend/database.py",
        "✓ tests/test_api.py",
        "✓ README.md",
    ]
    for item in deliverables:
        print(f"  {item}")

    print("\n💡 实战要点：")
    print("- CEO 只需输入一句话")
    print("- AI 团队自主分解、执行、测试")
    print("- 多轮迭代（发现问题 → 修复 → 重测）")
    print("- 完整的审批链（员工 → 经理 → EA → CEO）")
    print("- 所有过程可追溯（任务树 + 执行日志）")


# ============================================================================
# 主函数
# ============================================================================

async def main():
    """运行所有练习"""
    print("""
╔══════════════════════════════════════════════════════════╗
║  OneManCompany 实战练习                                  ║
║  从基础到高级，10 个渐进式练习                            ║
╚══════════════════════════════════════════════════════════╝
""")

    exercises = [
        ("基础", [
            ("练习 1", exercise_1_schedule_entry, False),
            ("练习 2", exercise_2_task_lifecycle, False),
            ("练习 3", exercise_3_stall_detection, False),
            ("练习 4", exercise_4_progress_log, False),
        ]),
        ("进阶", [
            ("练习 5", exercise_5_custom_launcher, True),
            ("练习 6", exercise_6_event_bus, True),
            ("练习 7", exercise_7_task_tree, False),
        ]),
        ("高级", [
            ("练习 8", exercise_8_scheduling_flow, True),
            ("练习 9", exercise_9_dependency_resolution, False),
            ("练习 10", exercise_10_full_simulation, True),
        ]),
    ]

    for category, items in exercises:
        print(f"\n{'=' * 60}")
        print(f"  {category}练习")
        print('=' * 60)

        for name, func, is_async in items:
            try:
                if is_async:
                    await func()
                else:
                    func()
            except Exception as e:
                print(f"\n❌ {name} 执行失败: {e}")
                import traceback
                traceback.print_exc()

    print("""
\n╔══════════════════════════════════════════════════════════╗
║  🎉 恭喜！您已完成所有练习                                ║
║                                                            ║
║  下一步:                                                   ║
║  1. 运行真实项目: bash start.sh                           ║
║  2. 阅读核心源码: vessel.py, task_tree.py                ║
║  3. 实现自定义功能: 新的 Launcher 或 Tool                 ║
║  4. 参与社区贡献: GitHub PR / Issue                       ║
╚══════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    asyncio.run(main())
