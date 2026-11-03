"""Import the questions a PDF already contains (quizzes, mock exams) verbatim into the bank.

The LLM only locates and copies: question stem, options, the answer letters and the
explanation. Code then checks every copied stem and option against the page text (folded
with ``textnorm.fold``, so layout differences don't matter) and drops anything not found.
The right answer is kept only when the document states it in text ("Option A is correct",
"Answer: B"…); otherwise the question is stored ``to_confirm`` and stays out of revision
until the learner picks the answer.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import fitz  # pymupdf

from rag_bachelor.config import settings
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.study.store import BankQuestion, add_bank_questions, bank_question_keys
from rag_bachelor.study.subjects import subject_of
from rag_bachelor.study.textnorm import fold

_WINDOW = 3  # pages per LLM call
_STEP = 2  # one page of overlap: a question cut by a page break is seen whole once
_MAX_OPTIONS = 8
_LETTERS = "ABCDEFGH"

_PROMPT = (
    "Tu reçois des pages d'un document qui contient des questions d'examen déjà rédigées "
    "(QCM, vrai/faux), souvent avec leur correction. Repère chaque question COMPLÈTE "
    "(énoncé + options) et recopie-la MOT POUR MOT, dans la langue du document, sans corriger, "
    "traduire ni reformuler. Ignore les éléments d'interface (boutons, scores, bandeaux) et les "
    "questions coupées par le bord des pages.\n"
    "Pour chaque question :\n"
    "- « question » : l'énoncé complet (contexte + question posée), sans le numéro "
    "« Question N » ni la consigne « Select … » ;\n"
    "- « multi » : true si la consigne demande plusieurs réponses (« Select two/three/all… ») ;\n"
    "- « options » : le texte de chaque option, sans sa lettre ;\n"
    "- « explanation » : le texte de correction / feedback de CETTE question recopié tel quel, "
    'y compris la phrase qui donne la réponse (« Option A is correct »…), sinon "". '
    "Ne devine jamais la réponse.\n"
    'Réponds uniquement avec le JSON {"questions": [{"question": "...", "multi": false, '
    '"options": ["..."], "explanation": "..."}]}.'
)

# A written statement of the right option(s): "Option A is correct", "Options A and C are
# correct", "Correct answer: B", "Answer: B, D", "Réponse : C"…
_LETTER_LIST = r"([A-H](?:\s*(?:,|and|&|et|or)\s*[A-H])*)"
_STATED = [
    re.compile(rf"\boptions?\s+{_LETTER_LIST}\s+(?:is|are)\s+(?:the\s+)?correct\b", re.I),
    re.compile(rf"\b(?:correct\s+)?answers?\s*(?:is|are|:)\s*(?:option\s+)?{_LETTER_LIST}\b", re.I),
    re.compile(rf"\bbonnes?\s+r[ée]ponses?\s*(?:est|sont|:)\s*{_LETTER_LIST}\b", re.I),
    re.compile(rf"\br[ée]ponses?\s*:\s*{_LETTER_LIST}\b", re.I),
]
_OPTION_PREFIX = re.compile(r"^\s*(?:\(?[A-Ha-h][.):]|[A-H]\s+-)\s+")
_TF = {"true", "false", "vrai", "faux"}


@dataclass
class ImportReport:
    imported: int = 0
    to_confirm: int = 0
    duplicates: int = 0
    rejected: int = 0  # stem or an option not found in the document text
    failed_windows: int = 0  # unusable model replies
    pages: int = 0
    samples: list[str] = field(default_factory=list)  # a few rejected stems, for the report


def stated_answer(text: str) -> tuple[list[str], str]:
    """``(letters, phrase)`` of the first explicit answer statement in *text*, else ``([], "")``."""
    for pattern in _STATED:
        m = pattern.search(text)
        if m:
            return sorted(set(re.findall(r"[A-H]", m.group(1)))), m.group(0)
    return [], ""


def _page_texts(source: str) -> list[tuple[int, str]]:
    with fitz.open(settings.pdfs_dir / source) as doc:
        return [(i + 1, page.get_text("text")) for i, page in enumerate(doc)]


def _build(
    entry: dict[str, object], window: list[tuple[int, str]], source: str
) -> BankQuestion | str:
    """A validated bank question from one model entry, or the reason it was rejected."""
    stem = str(entry.get("question", "")).strip()
    raw_options = entry.get("options")
    options = [
        _OPTION_PREFIX.sub("", str(o)).strip()
        for o in (raw_options if isinstance(raw_options, list) else [])
        if str(o).strip()
    ]
    if not stem or not 2 <= len(options) <= _MAX_OPTIONS:
        return "incomplete"
    folded = [(page, fold(text)) for page, text in window]
    where = [page for page, text in folded if fold(stem) in text]
    whole = "".join(text for _, text in folded)
    if not where and fold(stem) not in whole:  # a stem split across two pages
        return "stem"
    if any(not fold(o) or fold(o) not in whole for o in options):
        return "option"
    explanation = str(entry.get("explanation", "")).strip()
    # The answer must be stated in this question's own correction, and that statement must be
    # in the document: the model's "correct" field alone is never trusted (it could guess).
    letters, phrase = stated_answer(explanation)
    if not phrase or fold(phrase) not in whole:
        letters = []
    if explanation and fold(explanation) not in whole:
        explanation = ""  # not copied verbatim: drop it rather than keep altered text
    correct = [_LETTERS.index(x) for x in letters if _LETTERS.index(x) < len(options)]
    is_tf = len(options) == 2 and {fold(o) for o in options} <= _TF
    multi = bool(entry.get("multi")) or len(correct) > 1
    return BankQuestion(
        question=stem,
        answer=explanation,
        difficulty="moyen",
        source=source,
        pages=where or [window[0][0]],
        chunk_ids=[],
        qtype="tf" if is_tf else ("mcq_multi" if multi else "mcq_single"),
        options=options,
        correct=correct or [],
        origin="imported",
        to_confirm=not correct,
    )


def import_questions(
    source: str,
    progress_cb: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ImportReport:
    """Copy the questions of *source*'s PDF into the bank; returns what happened."""
    pages = _page_texts(source)
    report = ImportReport(pages=len(pages))
    seen = bank_question_keys(subject_of(source))  # the same quiz in two PDFs counts once
    starts = list(range(0, max(len(pages) - _WINDOW, 0) + 1, _STEP)) or [0]
    if starts[-1] + _WINDOW < len(pages):
        starts.append(len(pages) - _WINDOW)
    provider, _ = get_provider()
    for n, start in enumerate(starts):
        if should_stop and should_stop():
            break
        window = pages[start : start + _WINDOW]
        text = "\n\n".join(f"[page {p}]\n{t}" for p, t in window)
        data = loads_object(
            provider.chat(
                [{"role": "system", "content": _PROMPT}, {"role": "user", "content": text}],
                json_mode=True,
            )
        )
        entries = data.get("questions") if data else None
        if not isinstance(entries, list):
            report.failed_windows += 1
            entries = []
        new: list[BankQuestion] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            built = _build(entry, window, source)
            if isinstance(built, str):
                if built != "incomplete":
                    report.rejected += 1
                    if len(report.samples) < 5:
                        report.samples.append(str(entry.get("question", ""))[:120])
                continue
            key = fold(built.question)
            if key in seen:
                report.duplicates += 1
                continue
            seen.add(key)
            new.append(built)
        add_bank_questions(new)
        report.imported += len(new)
        report.to_confirm += sum(q.to_confirm for q in new)
        if progress_cb:
            progress_cb(n + 1, len(starts))
    return report
