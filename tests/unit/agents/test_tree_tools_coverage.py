"""Coverage tests for agents/tree_tools.py — missing lines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# _load_tree (lines 28-29)
# ---------------------------------------------------------------------------

class TestLoadTree:
    def test_missing_tree_returns_empty(self, tmp_path):
        from onemancompany.agents.tree_tools import _load_tree
        tree = _load_tree(str(tmp_path))
        assert tree.project_id == ""

    def test_explicit_automation_tree_path_is_authoritative(self, tmp_path):
        from onemancompany.agents.tree_tools import _load_tree
        from onemancompany.core.task_tree import TaskTree
        ordinary = TaskTree(project_id="wrong")
        ordinary.save(tmp_path / "task_tree.yaml")
        automation_path = tmp_path / "node_tree.yaml"
        automation = TaskTree(project_id="_sys_automation_health")
        automation.save(automation_path)
        loaded = _load_tree(str(tmp_path), str(automation_path))
        assert loaded.project_id == "_sys_automation_health"

    def test_missing_explicit_tree_fails_closed(self, tmp_path):
        from onemancompany.agents.tree_tools import _load_tree
        with pytest.raises(FileNotFoundError, match="Authoritative TaskTree"):
            _load_tree(str(tmp_path), str(tmp_path / "missing_tree.yaml"))


# ---------------------------------------------------------------------------
# _find_entry_for_task (lines 49-50, 57-59)
# ---------------------------------------------------------------------------

class TestFindEntryForTask:
    def test_not_found(self):
        from onemancompany.agents.tree_tools import _find_entry_for_task
        with patch("onemancompany.core.vessel.employee_manager") as mock_em:
            mock_em._schedule = {}
            mock_em._current_entries = {}
            project_dir, tree_path = _find_entry_for_task("nonexistent")
        assert project_dir == ""
        assert tree_path == ""

    def test_found_in_schedule(self, tmp_path):
        from onemancompany.agents.tree_tools import _find_entry_for_task
        entry = MagicMock()
        entry.node_id = "node1"
        entry.tree_path = str(tmp_path / "tree.yaml")

        with patch("onemancompany.core.vessel.employee_manager") as mock_em:
            mock_em._schedule = {"emp1": [entry]}
            mock_em._current_entries = {}
            project_dir, tree_path = _find_entry_for_task("node1")
        assert project_dir == str(tmp_path)

    def test_found_in_current_entries(self, tmp_path):
        from onemancompany.agents.tree_tools import _find_entry_for_task
        entry = MagicMock()
        entry.node_id = "node2"
        entry.tree_path = str(tmp_path / "tree.yaml")

        with patch("onemancompany.core.vessel.employee_manager") as mock_em:
            mock_em._schedule = {}
            mock_em._current_entries = {"emp1": entry}
            project_dir, tree_path = _find_entry_for_task("node2")
        assert project_dir == str(tmp_path)


# ---------------------------------------------------------------------------
# _resolve_project_root (line 71, 101-102)
# ---------------------------------------------------------------------------

class TestResolveProjectRoot:
    def test_project_yaml_in_dir(self, tmp_path):
        from onemancompany.agents.tree_tools import _resolve_project_root
        (tmp_path / "project.yaml").write_text("name: test")
        assert _resolve_project_root(str(tmp_path)) == tmp_path

    def test_project_yaml_in_parent(self, tmp_path):
        from onemancompany.agents.tree_tools import _resolve_project_root
        sub = tmp_path / "iterations" / "iter_001"
        sub.mkdir(parents=True)
        (tmp_path / "project.yaml").write_text("name: test")
        result = _resolve_project_root(str(sub))
        assert result == tmp_path

    def test_not_found(self, tmp_path):
        from onemancompany.agents.tree_tools import _resolve_project_root
        sub = tmp_path / "deep" / "nested" / "dir" / "more"
        sub.mkdir(parents=True)
        result = _resolve_project_root(str(sub))
        assert result is None


# ---------------------------------------------------------------------------
# _save_tree (line 57-59)
# ---------------------------------------------------------------------------

class TestSaveTree:
    def test_save_tree(self, tmp_path):
        from onemancompany.agents.tree_tools import _save_tree
        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree(project_id="proj1")
        with patch("onemancompany.core.task_tree.save_tree_async") as mock_save:
            _save_tree(str(tmp_path), tree)
        mock_save.assert_called_once()

    def test_save_tree_uses_explicit_automation_path(self, tmp_path):
        from onemancompany.agents.tree_tools import _save_tree
        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree(project_id="_sys_automation_health")
        explicit = tmp_path / "node_tree.yaml"
        with patch("onemancompany.core.task_tree.save_tree_async") as mock_save:
            _save_tree(str(tmp_path), tree, str(explicit))
        mock_save.assert_called_once_with(explicit)


# ---------------------------------------------------------------------------
# set_project_name (lines 679-701)
# ---------------------------------------------------------------------------

class TestSetProjectName:
    def test_no_agent_context(self):
        from onemancompany.agents.tree_tools import set_project_name
        with patch("onemancompany.core.vessel._current_task_id") as mock_tid:
            mock_tid.get.return_value = ""
            result = set_project_name.invoke({"name": "Test"})
        assert result["status"] == "error"

    def test_no_project_context(self):
        from onemancompany.agents.tree_tools import set_project_name
        with patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("", "")):
            mock_tid.get.return_value = "task1"
            result = set_project_name.invoke({"name": "Test"})
        assert result["status"] == "error"

    def test_no_project_yaml(self, tmp_path):
        from onemancompany.agents.tree_tools import set_project_name
        with patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task",
                   return_value=(str(tmp_path), str(tmp_path / "tree.yaml"))):
            mock_tid.get.return_value = "task1"
            result = set_project_name.invoke({"name": "Test"})
        assert result["status"] == "error"

    def test_success(self, tmp_path):
        from onemancompany.agents.tree_tools import set_project_name
        (tmp_path / "project.yaml").write_text(yaml.dump({"name": "Old", "iterations": []}))
        with patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task",
                   return_value=(str(tmp_path), str(tmp_path / "tree.yaml"))):
            mock_tid.get.return_value = "task1"
            result = set_project_name.invoke({"name": "New Name"})
        assert result["status"] == "ok"
        data = yaml.safe_load((tmp_path / "project.yaml").read_text())
        assert data["name"] == "New Name"
