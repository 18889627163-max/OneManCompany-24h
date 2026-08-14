"""Regression tests preventing pytest from touching formal HR runtime paths."""

from __future__ import annotations

from pathlib import Path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def test_employee_registry_paths_are_redirected_to_tmp_path(tmp_path):
    from onemancompany.core import config as config_mod
    from onemancompany.core import memory_service as memory_service_mod
    from onemancompany.core import store as store_mod

    isolated_root = tmp_path / "runtime"
    employee_paths = {
        "config.EMPLOYEES_DIR": config_mod.EMPLOYEES_DIR,
        "config.EX_EMPLOYEES_DIR": config_mod.EX_EMPLOYEES_DIR,
        "store.EMPLOYEES_DIR": store_mod.EMPLOYEES_DIR,
        "store.EX_EMPLOYEES_DIR": store_mod.EX_EMPLOYEES_DIR,
        "memory_service.EMPLOYEES_DIR": memory_service_mod.EMPLOYEES_DIR,
        "memory_service.EX_EMPLOYEES_DIR": memory_service_mod.EX_EMPLOYEES_DIR,
    }

    leaked = {
        name: str(path)
        for name, path in employee_paths.items()
        if not _is_within(Path(path), isolated_root)
    }
    assert leaked == {}, f"pytest employee paths escaped tmp_path: {leaked}"
