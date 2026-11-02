"""Image occlusion: zone validation, one card per zone, review rendering."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.app.web.routes.document import parse_occlusions
from rag_bachelor.ingest import index
from rag_bachelor.study import store

DOC = "Bachelor/Livres/Cours un.pdf"


def _zone(x: float = 10, y: float = 10, w: float = 20, h: float = 10, label: str = "A") -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "label": label}


def test_parse_occlusions_validates() -> None:
    assert parse_occlusions(json.dumps([_zone(label=" Cœur ")])) == [
        ([10.0, 10.0, 20.0, 10.0], "Cœur")
    ]
    assert parse_occlusions("pas du json") is None
    assert parse_occlusions("[]") is None
    assert parse_occlusions(json.dumps([_zone()] * 31)) is None
    assert parse_occlusions(json.dumps([_zone(x=90, w=20)])) is None  # leaves the page
    assert parse_occlusions(json.dumps([_zone(x=-1)])) is None
    assert parse_occlusions(json.dumps([_zone(w=0.1)])) is None  # accidental click
    assert parse_occlusions(json.dumps([_zone(w="20")])) is None
    assert parse_occlusions(json.dumps(["x"])) is None


def test_create_makes_one_card_per_zone_and_review_targets_each() -> None:
    index.get_collection().upsert(
        ids=[f"{DOC}__p1__c0"],
        documents=["Texte"],
        embeddings=[[1.0, 0.0, 0.0]],  # type: ignore[arg-type]
        metadatas=[{"source": DOC, "page": 1}],  # type: ignore[arg-type]
    )
    zones = [_zone(label="Aorte"), _zone(x=50, label="Oreillette"), _zone(y=60, label="Valve")]
    with TestClient(server.app) as client:
        url = "/document/" + DOC + "?tab=masquage"
        bad = client.post(
            "/occlusion/create",
            data={"source": DOC, "page": "1", "rects": "[]"},
            follow_redirects=False,
        )
        assert bad.status_code == 303 and "err=occlusion" in bad.headers["location"]
        assert store.get_all_cards() == []
        r = client.post(
            "/occlusion/create",
            data={"source": DOC, "page": "1", "rects": json.dumps(zones), "title": "Cœur"},
            follow_redirects=False,
        )
        assert r.status_code == 303 and "created=3" in r.headers["location"]
        assert client.get(url).status_code == 200

        cards = store.get_all_cards()
        assert [c.answer for c in cards] == ["Aorte", "Oreillette", "Valve"]
        assert [c.occlusion["target"] for c in cards if c.occlusion] == [0, 1, 2]
        assert all(c.qtype == "occlusion" and c.topic == DOC for c in cards)

        html = client.get("/revision/next", params={"source": DOC}).text
        assert html.count("occ-box") >= 3 and "Afficher la zone" in html and "Aorte" in html
