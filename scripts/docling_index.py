"""Build a throwaway index of one PDF with the production Docling pipeline.

For extraction/chunking experiments without touching ``data/``. Always run with
``CHROMA_DIR`` pointing to a scratch directory; from the repo root:
``CHROMA_DIR=/tmp/x python scripts/docling_index.py <pdf> [--cache doc.json]``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag_bachelor.config import settings

_BATCH = 64


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--cache", type=Path, help="Docling JSON cache (skips the slow conversion)")
    args = ap.parse_args()
    if "chroma_dir" not in settings.model_fields_set or "data" in settings.chroma_dir.parts:
        print("Refusing to run: set CHROMA_DIR to a scratch directory.", file=sys.stderr)
        return 2

    from docling_core.types.doc import DoclingDocument

    from rag_bachelor.ingest.index import index_chunks
    from rag_bachelor.ingest.layout import convert, layout_items, section_chunks

    if args.cache and args.cache.exists():
        doc = DoclingDocument.load_from_json(args.cache)
    else:
        doc = convert(args.pdf)
        if args.cache:
            doc.save_as_json(args.cache)
    chunks = section_chunks(layout_items(doc), args.pdf.name)
    for start in range(0, len(chunks), _BATCH):
        index_chunks(chunks[start : start + _BATCH])
    print(f"{len(chunks)} chunks indexed into {settings.chroma_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
