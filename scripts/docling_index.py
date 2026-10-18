"""Build a throwaway index of one PDF for the Docling vs PyMuPDF retrieval comparison.

``--mode pymupdf`` runs the current pipeline unchanged (baseline). ``--mode docling``
uses Docling's layout model (keeping the existing OCR layer) and section-based chunks,
each prefixed with its section title. Always run with ``CHROMA_DIR`` pointing to a
scratch directory, never ``data/``; from the repo root:
``CHROMA_DIR=/tmp/x python scripts/docling_index.py <pdf> --mode docling [--cache doc.json]``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import Chunk, _split_text, _with_overlap
from rag_bachelor.ingest.extract import clean_pages

Item = tuple[str, str, int]  # (docling label, text, 1-indexed page)
_SEPARATORS = ["\n\n", "\n", ". ", " "]
_BATCH = 64


def _table_pieces(table: str, size: int) -> list[str]:
    """Split a markdown table by rows into ≤ *size* pieces, each repeating the header rows."""
    if len(table) <= size:
        return [table]
    lines = table.splitlines()
    head, pieces, cur = "\n".join(lines[:2]), [], ""
    for row in lines[2:]:
        if cur and len(head) + len(cur) + len(row) + 2 > size:
            pieces.append(f"{head}\n{cur}")
            cur = ""
        cur = f"{cur}\n{row}" if cur else row
    return [*pieces, f"{head}\n{cur}"] if cur else pieces or [table]


def section_chunks(items: list[Item], source: str) -> list[Chunk]:
    """Pack each section's paragraphs into ≤ chunk_size chunks prefixed by the section title.

    A chunk's page is the page of its first paragraph (eval scores at page granularity).
    """
    texts = clean_pages([t.replace("\xad ", "") for _, t, _ in items])
    size, overlap = settings.chunk_size, settings.chunk_overlap
    chunks: list[Chunk] = []
    title = ""
    body: list[tuple[str, str, int]] = []  # (label, paragraph, page) of the current section

    def emit(text: str, page: int) -> None:
        text = f"{title}\n{text.strip()}" if title else text.strip()
        chunks.append(Chunk(text=text, source=source, page_num=page, chunk_index=len(chunks)))

    def emit_prose(run: list[tuple[str, int]]) -> None:
        packed: list[tuple[str, int]] = []
        for para, page in run:
            if packed and len(packed[-1][0]) + 2 + len(para) <= size:
                packed[-1] = (f"{packed[-1][0]}\n\n{para}", packed[-1][1])
            elif len(para) > size:
                packed.extend((p, page) for p in _split_text(para, _SEPARATORS, size, overlap))
            else:
                packed.append((para, page))
        pieces = _with_overlap([p for p, _ in packed], overlap)
        for piece, (_, page) in zip(pieces, packed, strict=True):
            emit(piece, page)

    def flush() -> None:
        # Tables are standalone chunks: no packing with prose, no overlap (it cuts rows).
        run: list[tuple[str, int]] = []
        for label, para, page in body:
            if label == "table":
                emit_prose(run)
                run = []
                for piece in _table_pieces(para, size):
                    emit(piece, page)
            else:
                run.append((para, page))
        emit_prose(run)
        body.clear()

    for (label, _, page), text in zip(items, texts, strict=True):
        if not text:
            continue
        if label == "section_header":
            flush()
            title = text
        else:
            body.append((label, text, page))
    flush()
    return chunks


def docling_items(pdf: Path, cache: Path | None) -> list[Item]:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import DoclingDocument, TableItem, TextItem

    if cache and cache.exists():
        doc = DoclingDocument.load_from_json(cache)
    else:
        opts = PdfPipelineOptions(
            artifacts_path="model_cache/docling", do_ocr=False, do_table_structure=True
        )
        conv = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        doc = conv.convert(pdf).document
        if cache:
            doc.save_as_json(cache)

    items: list[Item] = []
    for it, _ in doc.iterate_items():
        if not it.prov:
            continue
        if isinstance(it, TableItem):
            # Drop markdown column padding: it can triple a table past chunk_size.
            text = re.sub(r" {2,}", " ", it.export_to_markdown(doc=doc))
            text = re.sub(r"-{4,}", "---", text)
        elif isinstance(it, TextItem):
            text = it.text
        else:  # pictures: no text layer worth indexing
            continue
        items.append((str(it.label), text, it.prov[0].page_no))
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--mode", choices=["pymupdf", "docling"], required=True)
    ap.add_argument("--cache", type=Path, help="Docling JSON cache (docling mode)")
    args = ap.parse_args()
    if "chroma_dir" not in settings.model_fields_set or "data" in settings.chroma_dir.parts:
        print("Refusing to run: set CHROMA_DIR to a scratch directory.", file=sys.stderr)
        return 2

    from rag_bachelor.ingest.index import index_chunks

    if args.mode == "pymupdf":
        from rag_bachelor.ingest.chunk import chunk_pages
        from rag_bachelor.ingest.extract import extract_pages

        chunks = chunk_pages(extract_pages(args.pdf))
    else:
        chunks = section_chunks(docling_items(args.pdf, args.cache), args.pdf.name)
    for start in range(0, len(chunks), _BATCH):
        index_chunks(chunks[start : start + _BATCH])
    print(f"{len(chunks)} chunks indexed into {settings.chroma_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
