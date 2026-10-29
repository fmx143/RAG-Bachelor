"""Mind-map tests: tree validation, map/reduce flow, store, rendering route."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rag_bachelor.core import mindmap
from rag_bachelor.core.bank import SourceChunk
from rag_bachelor.study import store


def _run(replies: list[str], lo: int = 1, hi: int = 2) -> int | None:
    provider = MagicMock()
    provider.chat = MagicMock(side_effect=replies)
    chunks = [SourceChunk(f"c.pdf__p{p}__c0", p, 0, f"texte {p}") for p in (1, 2, 3)]
    with (
        patch.object(mindmap, "get_source_chunks", return_value=chunks),
        patch.object(mindmap, "get_provider", return_value=(provider, "ollama")),
    ):
        return mindmap.generate_map("c.pdf", lo, hi)


def test_clean_bounds_depth_width_and_drops_junk() -> None:
    deep: dict[str, object] = {"title": "n5"}
    for t in ("n4", "n3", "n2", "n1"):
        deep = {"title": t, "children": [deep]}
    cleaned = mindmap._clean(deep)
    assert cleaned is not None
    depth, node = 0, cleaned
    while node["children"]:
        depth, node = depth + 1, node["children"][0]  # type: ignore[index]
    assert depth == mindmap._MAX_DEPTH - 1  # n5 cut
    wide = {"title": "r", "children": [{"title": str(i)} for i in range(20)] + ["x", {"title": ""}]}
    out = mindmap._clean(wide)
    assert out is not None and len(out["children"]) == mindmap._MAX_CHILDREN  # type: ignore[arg-type]
    assert mindmap._clean({"children": []}) is None


def test_generate_stores_clean_tree() -> None:
    tree = {"title": "Réseaux", "children": [{"title": "TCP", "children": []}]}
    map_id = _run(["plan 1", "```json\n" + json.dumps(tree) + "\n```"])
    assert map_id is not None
    saved = store.get_map(map_id)
    assert saved is not None and json.loads(saved.content) == tree


def test_unusable_reply_raises_and_empty_range_raises() -> None:
    with pytest.raises(mindmap.BadTree):
        _run(["plan", "pas du json"])
    with pytest.raises(ValueError):
        _run([], 50, 60)


def test_store_list_delete_and_rename() -> None:
    mid = store.add_map("a.pdf", 1, 5, "{}")
    assert [m.id for m in store.list_maps()] == [mid]
    assert store.list_maps()[0].content == ""
    store.rename_document("a.pdf", "b.pdf")
    renamed = store.get_map(mid)
    assert renamed is not None and renamed.source == "b.pdf"
    store.delete_map(mid)
    assert store.get_map(mid) is None
