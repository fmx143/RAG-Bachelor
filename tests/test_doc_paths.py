"""Client-supplied document paths: normalisation and containment."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from rag_bachelor.app.web.routes.documentation import _resolve, _safe_relpath
from rag_bachelor.config import settings


@pytest.mark.parametrize(
    "raw",
    [
        "../x.pdf",
        "a/../b.pdf",
        "/abs.pdf",
        "C:\\x.pdf",
        "C:x.pdf",
        "a\\b.pdf",
        "a\x00b.pdf",
        "a\x1fb.pdf",
        ".cache/x.pdf",
        "a/._x.pdf",
        "",
        "./",
        "x.txt",
        "/".join(["d"] * 9) + "/x.pdf",
        "a/" + "é" * 128 + ".pdf",
        "/".join(["d" * 200] * 6) + "/x.pdf",
    ],
)
def test_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        _safe_relpath(raw)


def test_depth_limit_boundary() -> None:
    assert _safe_relpath("/".join(["d"] * 8) + "/x.pdf").count("/") == 8


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Algo/Chapitre 1.pdf", "Algo/Chapitre 1.pdf"),
        ("Cours/Théorie/é.PDF", "Cours/Théorie/é.PDF"),
        ("a//./b.pdf", "a/b.pdf"),
    ],
)
def test_accepted(raw: str, expected: str) -> None:
    assert _safe_relpath(raw) == expected


def test_nfd_becomes_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "Théorie/é.pdf")
    assert nfd != "Théorie/é.pdf"
    assert _safe_relpath(nfd) == "Théorie/é.pdf"


def test_folder_does_not_need_pdf_suffix() -> None:
    assert _safe_relpath("a/b", file=False) == "a/b"


def test_resolve_stays_inside(tmp_path: Path) -> None:
    settings.pdfs_dir.mkdir()
    assert _resolve("a/b.pdf") == settings.pdfs_dir.resolve() / "a" / "b.pdf"


def test_resolve_rejects_symlink_out(tmp_path: Path) -> None:
    settings.pdfs_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, settings.pdfs_dir / "link")
    with pytest.raises(ValueError):
        _resolve("link/x.pdf")
