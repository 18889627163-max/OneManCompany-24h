"""Unit tests for agent stall detection."""

import pytest

from onemancompany.core.vessel import (
    _get_verified_dispatch_child_ids,
    _should_retry_stall,
    detect_unfulfilled_promises,
    detect_unverified_dispatch_claim,
)


class TestDetectUnfulfilledPromises:
    """Detect when an agent says it will do something but didn't use tools."""

    def test_chinese_future_action(self):
        assert detect_unfulfilled_promises("我将正式开始执行前端重构任务") is True

    def test_chinese_next_step(self):
        assert detect_unfulfilled_promises("接下来我会优化数据库查询") is True

    def test_chinese_about_to(self):
        assert detect_unfulfilled_promises("下一步，我将部署新版本") is True

    def test_chinese_now_starting(self):
        assert detect_unfulfilled_promises("现在开始处理这个需求") is True

    def test_english_i_will(self):
        assert detect_unfulfilled_promises("I will now start implementing the feature") is True

    def test_english_let_me(self):
        assert detect_unfulfilled_promises("Let me start working on the refactoring") is True

    def test_english_next(self):
        assert detect_unfulfilled_promises("Next, I'll dispatch a task to the engineer") is True

    def test_completed_report_no_stall(self):
        """Agent reporting what it DID is not a stall."""
        assert detect_unfulfilled_promises("已完成OKR更新，所有指标已同步") is False

    def test_short_ack_no_stall(self):
        assert detect_unfulfilled_promises("收到，已更新") is False

    def test_empty_string(self):
        assert detect_unfulfilled_promises("") is False

    def test_none_returns_false(self):
        assert detect_unfulfilled_promises(None) is False

    def test_past_tense_no_stall(self):
        assert detect_unfulfilled_promises("I have completed the task and updated the OKR") is False

    def test_mixed_completed_and_promise(self):
        """If output has BOTH completed work AND future promises, it's a stall."""
        text = "已完成OKR更新。接下来我将开始执行前端重构任务。"
        assert detect_unfulfilled_promises(text) is True

    def test_asking_question_no_stall(self):
        """Agent asking a question should not be flagged."""
        assert detect_unfulfilled_promises("需要确认一下，这个功能要支持哪些浏览器？") is False


class TestDispatchCompletionClaims:
    """A textual allocation report is not proof that tasks were created."""

    def test_claimed_eight_tasks_without_children_is_a_stall(self):
        class Node:
            node_type = "task"
            children_ids = []
            result = (
                "任务已全部分配到系统！我已经完成了8个任务的正式分配，"
                "每个团队成员都能在任务板上看到。"
            )
            stall_retry_count = 0

        assert detect_unverified_dispatch_claim(Node.result, []) is True
        assert _should_retry_stall(Node(), initial_child_ids=set()) is True

    def test_claim_count_must_not_exceed_real_children(self):
        text = "已完成8个任务的正式分配。"
        assert detect_unverified_dispatch_claim(text, ["child-1", "child-2"]) is True

    def test_claim_is_verified_by_real_tree_children(self):
        text = "已完成2个任务的正式分配。"
        assert detect_unverified_dispatch_claim(text, ["child-1", "child-2"]) is False

    def test_claim_is_not_verified_by_unproven_tree_children(self):
        """YAML children alone are not proof that dispatch_child() ran."""
        text = "已完成2个任务的正式分配。"
        assert detect_unverified_dispatch_claim(
            text,
            ["child-1", "child-2"],
            verified_child_ids=[],
        ) is True
        assert detect_unverified_dispatch_claim(
            text,
            ["child-1", "child-2"],
            verified_child_ids=["child-1", "child-2"],
        ) is False

    def test_production_receipt_filter_rejects_yaml_only_children(self):
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        parent = tree.create_root(employee_id="00002", description="Delegate")
        child = tree.add_child(parent.id, "00100", "Manual YAML child", ["done"])
        parent.result = "已完成1个任务的正式分配。"

        verified = _get_verified_dispatch_child_ids(tree, parent)
        assert verified == set()
        assert _should_retry_stall(parent, initial_child_ids=set(), verified_child_ids=verified) is True

        child.dispatch_verification = {"verified": True, "receipt_id": "receipt-123"}
        verified = _get_verified_dispatch_child_ids(tree, parent)
        assert verified == {child.id}
        assert _should_retry_stall(parent, initial_child_ids=set(), verified_child_ids=verified) is False

    def test_non_dispatch_completion_is_not_flagged(self):
        assert detect_unverified_dispatch_claim("已完成OKR更新，所有指标已同步", []) is False
