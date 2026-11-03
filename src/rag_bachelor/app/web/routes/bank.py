"""🏦 Bank routes — whole-document question-bank generation and browsing."""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.core.bank import generate_bank_for_source
from rag_bachelor.core.domains import (
    FRAMEWORKS,
    SHORT,
    match_domain,
    subject_frameworks,
    tag_subject,
)
from rag_bachelor.core.qtypes import DIFFICULTIES as _DIFFICULTIES
from rag_bachelor.core.qtypes import QTYPE_LABELS, QTYPES, SELF_GRADED
from rag_bachelor.study.store import (
    BankQuestion,
    confirm_bank_answer,
    count_bank_questions,
    delete_bank_question,
    delete_bank_questions,
    get_bank_question,
    list_bank_questions,
    materialize_bank_question,
    record_bank_attempt,
    set_bank_domains,
)
from rag_bachelor.study.subjects import active_sources, active_subject, subject_of

router = APIRouter()

_DIFF_LABELS = {"facile": "Facile", "moyen": "Moyen", "difficile": "Difficile"}
_RESULTS = ("correct", "incorrect", "untried")
_RESULT_LABELS = {"correct": "Juste", "incorrect": "Faux", "untried": "Jamais tenté"}
_DECKS = ("in", "out", "bad")
_DECK_LABELS = {"bad": "Mal conçues"}  # every question is in revision except those flagged
_ORIGIN_LABELS = {"imported": "Importées", "generated": "Générées", "to_confirm": "À confirmer"}
_PAGE_SIZE = 20
_MIN_TARGET = 1
_MAX_TARGET = 100
_DEFAULT_TARGET = 20

# Single in-process generation job, aligned on routes/documentation.py's
# _JOB/_JOB_LOCK/_try_start pattern rather than introducing a second variant.
# Safe without extra locking around field reads/writes (GIL + single uvicorn
# worker, no --workers); the lock only guards the check-and-set at job start
# so two POSTs can't both start.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "source": None,
    "qtype": "free",
    "inserted": 0,
    "target": 0,
    "skipped": 0,
    "cancel": False,
    "error": None,
}


def _try_start(source: str, qtype: str, target: int) -> bool:
    """Atomically claim the job slot. Returns False if a job is already running."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(
            running=True,
            source=source,
            qtype=qtype,
            inserted=0,
            target=target,
            skipped=0,
            cancel=False,
            error=None,
        )
        return True


def _run_bank_job(
    source: str, qtype: str, target: int, pages: tuple[int, int] | None = None
) -> None:
    """Generate the bank for *source* (or its *pages* range) in the background."""

    def _progress(inserted: int, tgt: int, skipped: int) -> None:
        _JOB.update(inserted=inserted, target=tgt, skipped=skipped)

    try:
        generate_bank_for_source(
            source,
            qtype=qtype,
            target=target,
            progress_cb=_progress,
            should_stop=lambda: bool(_JOB["cancel"]),
            page_range=pages,
        )
        try:  # exam domains of the new questions; the bank itself is already saved
            tag_subject(subject_of(source), should_stop=lambda: bool(_JOB["cancel"]))
        except Exception:  # noqa: BLE001 — tags can be added later from Paramètres
            pass
    except Exception:
        # Never surface the raw exception: the LLM SDKs can echo an API key on auth errors.
        _JOB["error"] = (
            "Erreur lors de la génération. Vérifie la configuration du fournisseur LLM "
            "dans Paramètres."
        )
    finally:
        _JOB["running"] = False


def _status_ctx(error_override: str | None = None) -> dict[str, object]:
    target = cast(int, _JOB["target"])
    inserted = cast(int, _JOB["inserted"])
    return {
        "running": _JOB["running"],
        "gen_source": _JOB["source"],
        "gen_inserted": inserted,
        "gen_target": target,
        "gen_skipped": _JOB["skipped"],
        "gen_error": error_override or _JOB["error"],
        "gen_pct": round(inserted / target * 100) if target else 0,
        "gen_cancelling": bool(_JOB["cancel"]),
    }


async def list_ctx(
    source: str,
    difficulty: str,
    search: str,
    limit: int,
    qtype: str = "",
    result: str = "",
    deck: str = "",
    domain: str = "",
    origin: str = "",
) -> dict[str, object]:
    src = source or None
    diff = difficulty if difficulty in _DIFFICULTIES else None
    q = search.strip() or None
    qt = qtype if qtype in QTYPES else None
    res = result if result in _RESULTS else None
    dk = deck if deck in _DECKS else None
    dom = _valid_domain(domain)
    org = origin if origin in _ORIGIN_LABELS else None

    def _sync() -> tuple[list[BankQuestion], int, dict[str, int], list[str]]:
        subject = active_subject()

        def count(r: str | None) -> int:
            return count_bank_questions(
                source=src, difficulty=diff, search=q, qtype=qt, result=r, deck=dk,
                subject=subject, domain=dom, origin=org, with_unconfirmed=True,
            )  # fmt: skip

        items = list_bank_questions(
            source=src, difficulty=diff, search=q, qtype=qt, result=res, deck=dk,
            limit=limit, subject=subject, domain=dom, origin=org, with_unconfirmed=True,
        )  # fmt: skip
        by_result = {r: count(r) for r in ("correct", "incorrect", "untried")}
        return list(items), count(res), by_result, subject_frameworks(subject)

    items, total, by_result, frameworks = await asyncio.to_thread(_sync)
    return {
        "frameworks": {fw: FRAMEWORKS[fw] for fw in frameworks},
        "domain_short": SHORT,
        "filter_domain": dom or "",
        "filter_origin": org or "",
        "origin_labels": _ORIGIN_LABELS,
        "items": items,
        "diff_labels": _DIFF_LABELS,
        "qtype_labels": QTYPE_LABELS,
        "qtypes": QTYPES,
        "result_labels": _RESULT_LABELS,
        "deck_labels": _DECK_LABELS,
        "filter_source": source,
        "filter_difficulty": difficulty,
        "filter_search": search,
        "filter_qtype": qtype,
        "filter_result": result,
        "filter_deck": deck,
        "list_total": total,
        "by_result": by_result,
        "has_more": limit < total,
        "next_limit": limit + _PAGE_SIZE,
    }


def _valid_domain(domain: str) -> str | None:
    """``"FRAMEWORK:Domain"`` when it names an official domain, else None."""
    framework, _, name = domain.partition(":")
    official = match_domain(framework, name)
    return f"{framework}:{official}" if official else None


def generation_ctx() -> dict[str, object]:
    """Context for the bank generation form (the document page's Questions tab)."""
    return {
        "bank_sources": active_sources(),
        "default_target": _DEFAULT_TARGET,
        "min_target": _MIN_TARGET,
        "max_target": _MAX_TARGET,
        **_status_ctx(),
    }


# ── Generation ─────────────────────────────────────────────────────────────────


@router.post("/bank/generate", response_class=HTMLResponse)
async def start_generation(
    request: Request,
    background_tasks: BackgroundTasks,
    source: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "free",
    count: Annotated[int, Form()] = _DEFAULT_TARGET,
    page_from: Annotated[int, Form()] = 0,
    page_to: Annotated[int, Form()] = 0,
) -> Response:
    """Start a bank run over the whole document, or pages ``page_from..page_to`` if given."""
    source = source.strip()
    qt = qtype if qtype in QTYPES else "free"
    target = min(max(count, _MIN_TARGET), _MAX_TARGET)
    pages = (page_from, page_to) if page_from or page_to else None
    error: str | None = None
    if not source:
        error = "Choisis un document."
    elif pages and (page_from < 1 or page_to < page_from):
        error = "Plage de pages invalide."
    elif _try_start(source, qt, target):
        background_tasks.add_task(_run_bank_job, source, qt, target, pages)
    else:
        error = "Une génération est déjà en cours."
    return templates.TemplateResponse(
        request, "partials/bank_status.html", {"request": request, **_status_ctx(error)}
    )


@router.get("/bank/status", response_class=HTMLResponse)
async def generation_status(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/bank_status.html", {"request": request, **_status_ctx()}
    )


@router.post("/bank/cancel", response_class=HTMLResponse)
async def cancel_generation(request: Request) -> Response:
    """Request a stop of the running generation (cooperative: takes effect between windows)."""
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return templates.TemplateResponse(
        request, "partials/bank_status.html", {"request": request, **_status_ctx()}
    )


# ── Browsing ───────────────────────────────────────────────────────────────────


@router.get("/bank/list", response_class=HTMLResponse)
async def list_bank(
    request: Request,
    source: str = "",
    difficulty: str = "",
    search: str = "",
    qtype: str = "",
    result: str = "",
    deck: str = "",
    domain: str = "",
    origin: str = "",
    limit: int = _PAGE_SIZE,
) -> Response:
    ctx = {
        "request": request,
        **(await list_ctx(source, difficulty, search, limit, qtype, result, deck, domain, origin)),
    }
    return templates.TemplateResponse(request, "partials/bank_list.html", ctx)


@router.post("/bank/add-all", response_class=HTMLResponse)
async def add_all_bank_to_deck(
    request: Request,
    source: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "",
    search: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "",
    result: Annotated[str, Form()] = "",
    deck: Annotated[str, Form()] = "",
    domain: Annotated[str, Form()] = "",
) -> Response:
    """Add every bank question matching the current filter that isn't in the deck yet."""
    src = source or None
    diff = difficulty if difficulty in _DIFFICULTIES else None
    q = search.strip() or None
    qt = qtype if qtype in QTYPES else None
    res = result if result in _RESULTS else None
    dk = deck if deck in _DECKS else None
    dom = _valid_domain(domain)

    def _sync() -> int:
        # Fetch the whole matching set in one shot rather than paging: with the
        # deck="out" filter, adding a card removes that row from later pages,
        # so offset-based paging silently skips rows as the filtered set shrinks.
        total = count_bank_questions(
            source=src, difficulty=diff, search=q, qtype=qt, result=res, deck=dk, domain=dom
        )
        if total == 0:
            return 0
        batch = list_bank_questions(
            source=src, difficulty=diff, search=q, qtype=qt, result=res, deck=dk,
            domain=dom, limit=total,
        )  # fmt: skip
        added = 0
        for item in batch:
            if item.id is None or item.card_id is not None:
                continue
            materialize_bank_question(item)
            added += 1
        return added

    added = await asyncio.to_thread(_sync)
    ctx = {
        "request": request,
        "added_count": added,
        **(await list_ctx(source, difficulty, search, _PAGE_SIZE, qtype, result, deck, domain)),
    }
    return templates.TemplateResponse(request, "partials/bank_list.html", ctx)


@router.post("/bank/delete-selected", response_class=HTMLResponse)
async def delete_selected_bank(
    request: Request,
    ids: Annotated[list[int], Form()] = [],  # noqa: B006 — read-only, never mutated
    source: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "",
    search: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "",
    result: Annotated[str, Form()] = "",
    deck: Annotated[str, Form()] = "",
    domain: Annotated[str, Form()] = "",
    origin: Annotated[str, Form()] = "",
) -> Response:
    # Questions flagged "Mal conçue" are deleted with their revision card, or they would linger there.
    await asyncio.to_thread(delete_bank_questions, ids, deck == "bad")
    ctx = {
        "request": request,
        **(
            await list_ctx(
                source, difficulty, search, _PAGE_SIZE, qtype, result, deck, domain, origin
            )
        ),
    }
    return templates.TemplateResponse(request, "partials/bank_list.html", ctx)


@router.post("/bank/{bank_id}/add", response_class=HTMLResponse)
async def add_bank_to_deck(bank_id: int) -> Response:
    def _sync() -> int | None:
        q = get_bank_question(bank_id)
        if q is None or q.to_confirm:  # nothing to grade against until its answer is confirmed
            return None
        return materialize_bank_question(q)

    card_id = await asyncio.to_thread(_sync)
    if card_id is None:
        return HTMLResponse(
            '<span class="alert alert-warning" style="display:inline-block;padding:.2rem .6rem;">'
            "Question introuvable ou réponse à confirmer</span>"
        )
    return HTMLResponse(
        f'<span class="alert alert-success" style="display:inline-block;padding:.2rem .6rem;">'
        f"Carte #{card_id} ajoutée !</span>"
    )


@router.post("/bank/{bank_id}/answer", response_class=HTMLResponse)
async def answer_bank_question(
    request: Request,
    bank_id: int,
    selected: Annotated[list[int], Form()] = [],  # noqa: B006 — never mutated
    self_grade: Annotated[str, Form()] = "",
) -> Response:
    """Grade an inline self-test attempt; a wrong answer auto-adds it to révision.

    Structured (QCM/Vrai-Faux) questions are auto-graded against the stored
    ``correct`` indices, mirroring ``revision.py``'s ``answer_card``. Free-text
    questions are self-graded via ``self_grade`` ("correct"/"incorrect") after
    the learner reveals the model answer. Either way the result is recorded on
    the bank question (for the Résultat filter) and, if wrong and not already
    in the deck, copied into a new SRS card — same mapping as the manual
    "➕ Ajouter" action above.
    """

    def _sync() -> dict[str, object] | None:
        q = get_bank_question(bank_id)
        if q is None or q.to_confirm:
            return None
        if q.qtype in SELF_GRADED:
            is_correct = self_grade == "correct"
        else:
            is_correct = q.correct is not None and set(selected) == set(q.correct)
        record_bank_attempt(bank_id, is_correct)
        added = False
        if not is_correct and q.card_id is None:
            materialize_bank_question(q)
            added = True
        return {"q": q, "is_correct": is_correct, "selected": selected, "added": added}

    outcome = await asyncio.to_thread(_sync)
    if outcome is None:
        return HTMLResponse(
            '<span class="alert alert-warning" style="display:inline-block;padding:.2rem .6rem;">'
            "Question introuvable ou réponse à confirmer</span>"
        )
    ctx = {"request": request, "bank_id": bank_id, **outcome}
    return templates.TemplateResponse(request, "partials/bank_answer_feedback.html", ctx)


@router.post("/bank/{bank_id}/delete", response_class=HTMLResponse)
async def delete_bank(bank_id: int) -> Response:
    await asyncio.to_thread(delete_bank_question, bank_id)
    return HTMLResponse("<!-- supprimé -->")


@router.post("/bank/{bank_id}/domains", response_class=HTMLResponse)
async def set_domains(request: Request, bank_id: int) -> Response:
    """Correct a question's exam domains: one ``<framework>`` form field per framework."""
    form = await request.form()
    fields = {fw: f"d{bank_id}_{fw}" for fw in FRAMEWORKS}
    tags = {fw: match_domain(fw, form.get(f)) or "" for fw, f in fields.items() if f in form}
    await asyncio.to_thread(set_bank_domains, bank_id, tags)
    return HTMLResponse('<span role="status">Domaines enregistrés.</span>')


@router.post("/bank/{bank_id}/confirm", response_class=HTMLResponse)
async def confirm_answer(request: Request, bank_id: int) -> Response:
    """Set the right option(s) of an imported question whose document gave no readable answer.

    The ticked options arrive as ``c<bank id>`` fields (the list sits inside the bulk form).
    """
    form = await request.form()
    raw = form.getlist(f"c{bank_id}")
    selected = [int(v) for v in raw if isinstance(v, str) and v.isdigit()]
    if await asyncio.to_thread(confirm_bank_answer, bank_id, selected):
        return HTMLResponse('<span class="qpill ok" role="status">Réponse confirmée</span>')
    return HTMLResponse('<span class="qpill ko" role="status">Coche au moins une option</span>')
