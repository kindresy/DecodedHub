from __future__ import annotations

import sys

from scripts import stdio_smoke


def test_stdio_smoke_falls_back_to_running_interpreter(tmp_path) -> None:
    assert hasattr(stdio_smoke, "python_executable")
    assert stdio_smoke.python_executable(tmp_path) == sys.executable
