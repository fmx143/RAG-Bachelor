"""Carte du savoir: the fact graph's entities as concepts, with mastery from the linked deck cards.

``graphe_annote`` turns the stored facts of one subject into a networkx graph whose nodes carry the
attributes ``construire_carte`` reads; ``construire_carte`` is pure and produces the JSON contract of
``static/carte-savoir/carte-savoir.js`` (modules = chapters of the subject's books, concepts = graph entities).
A concept's mastery is the mean predicted recall of the cards made from the chunks it appears in;
``None`` while none of them was ever reviewed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date

import networkx as nx
from pydantic import BaseModel, Field

from rag_bachelor.core.chapters import chapters_for
from rag_bachelor.ingest.index import get_facts_collection
from rag_bachelor.study.srs import retention
from rag_bachelor.study.store import Chapter, bank_chunk_cards, get_all_cards
from rag_bachelor.study.subjects import doc_kind, subject_of

PER_MODULE = 8  # concepts shown per chapter until it is opened
MAX_MODULE = 60  # concepts of one opened chapter (the front's O(n²) repulsion stays fluid up to ~400)
_PAGE = re.compile(r"__p(\d+)__c\d+$")  # chunk ids are "<source>__p<page>__c<index>"


class ModuleCarte(BaseModel):
    id: str
    nom: str
    couleur: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ConceptCarte(BaseModel):
    id: str
    nom: str
    module: str
    maitrise: float | None = Field(default=None, ge=0, le=1)
    a_revoir: bool = False
    prochaine_revision: date | None = None


class LienCarte(BaseModel):
    source: str
    target: str


class CarteSavoir(BaseModel):
    titre: str
    modules: list[ModuleCarte]
    concepts: list[ConceptCarte]
    liens: list[LienCarte]


def construire_carte(graphe: nx.Graph, titre: str) -> CarteSavoir:
    """Contract JSON of the front from a graph whose concept nodes have ``type == "concept"``.

    Node attributes: ``nom``, ``module`` (id), ``module_nom``, ``maitrise`` (0..1 or None),
    ``a_revoir``, ``prochaine_revision`` (date). Other nodes (chunks, documents) are skipped, and so
    are links to nodes that are not concepts.
    """
    modules: dict[str, ModuleCarte] = {}
    concepts: list[ConceptCarte] = []
    ids: set[str] = set()

    for nid, attrs in graphe.nodes(data=True):
        if attrs.get("type") != "concept":
            continue
        module_id = str(attrs.get("module") or "autres")
        modules.setdefault(
            module_id, ModuleCarte(id=module_id, nom=str(attrs.get("module_nom") or module_id))
        )
        concept_id = str(nid)
        ids.add(concept_id)
        concepts.append(
            ConceptCarte(
                id=concept_id,
                nom=str(attrs.get("nom") or concept_id),
                module=module_id,
                maitrise=attrs.get("maitrise"),
                a_revoir=bool(attrs.get("a_revoir", False)),
                prochaine_revision=attrs.get("prochaine_revision"),
            )
        )

    liens = [
        LienCarte(source=str(u), target=str(v))
        for u, v in graphe.edges()
        if str(u) in ids and str(v) in ids and u != v
    ]
    return CarteSavoir(titre=titre, modules=list(modules.values()), concepts=concepts, liens=liens)


def _chapter_title(chapters: list[Chapter], page: int) -> str:
    """Title of the chapter holding *page*; ``""`` for a book with one chapter or a page outside all."""
    if len(chapters) < 2:
        return ""
    return next((c.title for c in chapters if c.page_from <= page <= c.page_to), "")


def graphe_annote(
    subject: str,
    label: Callable[[str], str],
    today: date | None = None,
    only: str | None = None,
) -> nx.Graph:
    """Concept graph of *subject*: entities linked per fact, annotated for ``construire_carte``.

    Only books count (``<Subject>/Livres/…``); a module is one chapter of a book. *label* shortens
    a document path into the module's display name. Each module keeps its ``PER_MODULE`` most
    connected entities; with *only* (a module id) set, only that module is returned, up to ``MAX_MODULE``.
    """
    today = today or date.today()
    got = get_facts_collection().get(include=["metadatas", "documents"])
    metas = got.get("metadatas") or []
    docs = got.get("documents") or []

    g: nx.Graph = nx.Graph()
    names: defaultdict[str, Counter[str]] = defaultdict(Counter)  # raw spellings of an entity
    sources: defaultdict[str, Counter[str]] = defaultdict(Counter)
    chunks: defaultdict[str, set[str]] = defaultdict(set)
    chapters: dict[str, list[Chapter]] = {}
    modules: dict[str, tuple[str, str]] = {}  # module id -> (book, chapter title or "")
    for meta, doc in zip(metas, docs):
        if not meta:
            continue
        source = str(meta["source"])
        if subject_of(source) != subject or doc_kind(source) != "livre":
            continue
        if source not in chapters:
            chapters[source] = chapters_for(source)
        page = _PAGE.search(str(meta["chunk_id"]))
        title = _chapter_title(chapters[source], int(page[1])) if page else ""
        module = f"{source}#{title}" if title else source
        modules[module] = (source, title)
        parts = (doc or "").split(" | ")  # "sujet | relation | objet"
        raws = (parts[0], parts[2]) if len(parts) == 3 else ("", "")
        ends = (str(meta["subject"]), str(meta["object"]))
        for entity, raw in zip(ends, raws):
            if not entity:
                continue
            g.add_node(entity)
            if raw.strip():
                names[entity][raw.strip()] += 1
            sources[entity][module] += 1
            chunks[entity].add(str(meta["chunk_id"]))
        if ends[0] and ends[1] and ends[0] != ends[1]:
            g.add_edge(*ends)

    by_module: defaultdict[str, list[str]] = defaultdict(list)
    for n in sorted(g.nodes, key=lambda n: (-g.degree(n), n)):
        by_module[sources[n].most_common(1)[0][0]].append(n)
    if only is not None:
        keep = by_module.get(only, [])[:MAX_MODULE]
    else:
        keep = [n for ns in by_module.values() for n in ns[:PER_MODULE]]
    g = g.subgraph(keep).copy()

    cards = {c.id: c for c in get_all_cards(subject=subject)}
    by_chunk = bank_chunk_cards()
    for entity in g.nodes:
        module = sources[entity].most_common(1)[0][0]
        book, title = modules[module]
        linked = [
            cards[i]
            for i in {i for ch in chunks[entity] for i in by_chunk.get(ch, ())}
            if i in cards
        ]
        reviewed = [retention(c, today) for c in linked if c.last_review is not None]
        due = min((c.due_date for c in linked), default=None)
        g.nodes[entity].update(
            type="concept",
            nom=names[entity].most_common(1)[0][0] if names[entity] else entity,
            module=module,
            module_nom=f"{label(book)} · {title}" if title else label(book),
            maitrise=sum(reviewed) / len(reviewed) if reviewed else None,
            prochaine_revision=due,
            a_revoir=due is not None and due <= today,
        )
    return g
