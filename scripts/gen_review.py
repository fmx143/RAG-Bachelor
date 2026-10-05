"""Regenerate eval/review.md: draft eval questions sorted by lexical overlap with their page.

Overlap = share of the question's content words (lowercased, French stopwords and
1-letter tokens dropped, U+00AD removed) found in the text of its expected page(s).
High overlap = question probably copied from the page. Read-only on data/pdfs and
eval/questions.jsonl; run from the repo root: ``python scripts/gen_review.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

STOP = set(
    """a au aux avec ce ces cet cette ceux celui celle dans de des du elle elles en et eux il
    ils je la le les leur leurs lui ma mais me même mes moi mon ne nos notre nous on ou par pas
    pour qu que quel quelle quelles quels qui sa se ses si son sur ta te tes toi ton tu un une
    vos votre vous c d j l m n s t y à été être est sont sera seront était étaient suis es
    sommes êtes ont avoir as avons avez avait ai aurait fait faire peut peuvent doit doivent
    plus moins très comme aussi donc or ni car lors alors ainsi entre vers sous chez sans afin
    quand dont où quoi comment pourquoi lesquels lequel laquelle""".split()
)

HEADER = """# Relecture des {n} questions d'éval (brouillons, triées par recopie décroissante)

Pour chaque question : la réponse est-elle bien sur la/les page(s) indiquée(s), et seulement là ? \
Pour un `multi_page`, faut-il vraiment les deux pages ? Pour un `hors_sujet`, la réponse est-elle \
bien absente des 4 PDF ?
Si oui, renseigner `type` (lexical | paraphrase | multi_page | hors_sujet), puis passer `status` à \
`valide` dans `eval/questions.jsonl`. Texte de page tel qu'extrait (PyMuPDF).

Le % en tête de chaque question = part de ses mots pleins présents dans le texte de la/des page(s) \
attendue(s). Élevé = question probablement recopiée de la page (test lexical facile). `hors_sujet` \
à la fin.
"""


def words(text: str) -> set[str]:
    text = text.replace("­", "").lower()
    return {w for w in re.findall(r"\w+", text) if w not in STOP and len(w) > 1}


def copy_pct(question: str, page_texts: list[str]) -> float:
    """% of the question's content words present in the union of *page_texts*."""
    qw = words(question)
    if not qw:
        return 0.0
    pw = set().union(*(words(t) for t in page_texts))
    return len(qw & pw) / len(qw) * 100


def _block(q: dict[str, Any], pct: float | None, pages: dict[str, dict[int, str]]) -> str:
    off = q.get("type") == "hors_sujet"
    where = "hors corpus" if off else f"{q['source']}, " + ",".join(f"p{p}" for p in q["pages"])
    label = f"{pct:.0f} % recopié — " if pct is not None else ""
    kind = q.get("type") or "(non renseigné)"
    out = [f"## {q['id']} — {label}{where} — type : {kind}", "", f"**Q :** {q['question']}", ""]
    if not off:
        out += [f"> **p{p}** : {' '.join(pages[q['source']][p].split())}\n" for p in q["pages"]]
    if q.get("note"):
        out += [f"*Note : {q['note']}*", ""]
    return "\n".join([*out, "- [ ] valide", ""])


def main() -> None:
    from rag_bachelor.ingest.extract import extract_pages

    pages = {
        p.name: {pg.page_num: pg.text for pg in extract_pages(p)}
        for p in sorted(Path("data/pdfs").glob("*.pdf"))
    }
    qs = [
        json.loads(line) for line in Path("eval/questions.jsonl").read_text().splitlines() if line
    ]
    on = [q for q in qs if q.get("type") != "hors_sujet"]
    off = [q for q in qs if q.get("type") == "hors_sujet"]
    scored = sorted(
        ((copy_pct(q["question"], [pages[q["source"]][p] for p in q["pages"]]), q) for q in on),
        key=lambda x: -x[0],
    )
    blocks = [_block(q, pct, pages) for pct, q in scored] + [_block(q, None, pages) for q in off]
    Path("eval/review.md").write_text(
        HEADER.format(n=len(qs)) + "\n" + "\n".join(blocks), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
