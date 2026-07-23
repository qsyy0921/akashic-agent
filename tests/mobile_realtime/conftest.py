from __future__ import annotations

import sys
from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if sys.platform != "win32":
        return
    marker = pytest.mark.skip(
        reason="mobile realtime secure-file semantics are not supported on Windows"
    )
    root = Path(__file__).resolve().parent
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(root):
            item.add_marker(marker)
