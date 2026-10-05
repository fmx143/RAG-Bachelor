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


def test_no_path_setting_points_to_real_data_dir() -> None:
    """Every Path setting must be redirected away from the repo's data/ during tests."""
    from rag_bachelor.config import Settings, settings

    real_data = (_ROOT / "data").resolve()
    path_fields = [n for n, f in Settings.model_fields.items() if f.annotation is Path]
    assert path_fields, "Settings has no Path fields; update this test"
    for name in path_fields:
        assert not getattr(settings, name).resolve().is_relative_to(real_data), name
