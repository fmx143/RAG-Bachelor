"""``rag-eval``: score retrieval on the validated reference questions (read-only)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from rag_bachelor.config import settings
from rag_bachelor.eval.dataset import EvalQuestion, load_questions
from rag_bachelor.eval.metrics import Hit, Scored, aggregate, recall_at_k, reciprocal_rank

_REPORT_KS = (1, 3, 5, 10)
UNCATEGORIZED = "non classée"
Searcher = Callable[[str, int], list[Hit]]


def evaluate(
    questions: Sequence[EvalQuestion], search: Searcher, k: int
) -> tuple[dict[str, float], list[Scored]]:
    ks = [x for x in _REPORT_KS if x <= k] or [k]
    scored: list[Scored] = []
    for q in questions:
        if q.type == "hors_sujet":  # no expected page: kept out of recall/MRR
            continue
        ranked = search(q.question, k)
        recall = {x: recall_at_k(ranked, q.source, q.pages, x) for x in ks}
        scored.append(Scored(q.id, recall, reciprocal_rank(ranked, q.source, q.pages)))
    return aggregate(scored), scored


def _breakdown(labels: dict[str, str], scored: Sequence[Scored]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[Scored]] = defaultdict(list)
    for s in scored:
        groups[labels[s.id]].append(s)
    return {t: {"n": len(g), **aggregate(g)} for t, g in sorted(groups.items())}


def by_type(
    questions: Sequence[EvalQuestion], scored: Sequence[Scored]
) -> dict[str, dict[str, float]]:
    return _breakdown({q.id: q.type or "(sans type)" for q in questions}, scored)


def by_category(
    questions: Sequence[EvalQuestion], scored: Sequence[Scored]
) -> dict[str, dict[str, float]]:
    return _breakdown({q.id: q.category or UNCATEGORIZED for q in questions}, scored)


def _search(query: str, k: int) -> list[Hit]:
    from rag_bachelor.core.retriever import retrieve

    return [(r.source, r.page) for r in retrieve(query, top_k=k)]


def _print_metrics(metrics: dict[str, float], baseline: dict[str, float] | None) -> None:
    for name, value in metrics.items():
        delta = (
            f"  ({value - baseline[name]:+.3f} vs baseline)"
            if baseline and name in baseline
            else ""
        )
        print(f"{name:>10}: {value:.3f}{delta}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rag-eval", description=__doc__)
    ap.add_argument("--questions", type=Path, default=Path("eval/questions.jsonl"))
    ap.add_argument("--k", type=int, default=10, help="retrieval depth (default 10)")
    ap.add_argument("--out", type=Path, help="report path (default eval/reports/<timestamp>.json)")
    ap.add_argument("--compare", type=Path, help="earlier report JSON to diff against")
    args = ap.parse_args(argv)

    # Safety: never evaluate against whatever index the defaults happen to point at.
    if not {"chroma_dir", "chroma_host"} & settings.model_fields_set:
        print("Refusing to run: set CHROMA_DIR (or CHROMA_HOST) explicitly.", file=sys.stderr)
        return 2

    questions = [q for q in load_questions(args.questions) if q.status == "valide"]
    if not questions:
        print("No question with status 'valide' — validate the drafts first.", file=sys.stderr)
        return 1

    metrics, scored = evaluate(questions, _search, args.k)
    baseline = json.loads(args.compare.read_text())["metrics"] if args.compare else None
    kinds = by_type(questions, scored)
    cats = by_category(questions, scored)
    labels = {q.id: q.category or UNCATEGORIZED for q in questions}
    n_off = len(questions) - len(scored)
    print(f"{len(scored)} questions (+{n_off} hors_sujet, excluded from recall), k={args.k}")
    _print_metrics(metrics, baseline)
    for group in (kinds, cats):
        for name, m in group.items():
            print(
                f"[{name}] n={m['n']:.0f}  "
                + "  ".join(f"{a}={v:.3f}" for a, v in m.items() if a != "n")
            )

    out = args.out or Path("eval/reports") / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "k": args.k,
        "n": len(scored),
        "n_hors_sujet": n_off,
        "metrics": metrics,
        "by_type": kinds,
        "by_category": cats,
        "per_question": [
            {"id": s.id, "recall": s.recall, "rr": s.rr, "category": labels[s.id]} for s in scored
        ],
    }
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
