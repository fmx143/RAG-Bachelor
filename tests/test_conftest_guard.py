"""The conftest guard must abort the run when env points at a real backend."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("var", ["CHROMA_HOST", "POSTGRES_HOST"])
def test_pytest_aborts_when_backend_host_is_set(var: str) -> None:
    env = {**os.environ, var: "example.invalid"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_srs.py"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert var in proc.stdout + proc.stderr
