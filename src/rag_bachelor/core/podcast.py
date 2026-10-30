"""Two-voice audio lesson of a page range: windowed dialogue script → local TTS → one WAV.

Script lines are validated by code (speaker, length, no stray citations); the audio is
synthesized with Phonon (``ptts``), imported lazily so the app starts without it. Each stored
line carries its ``start`` second so the transcript can seek the audio.
"""

from __future__ import annotations

import io
import json
import re
import threading
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

from rag_bachelor.config import settings
from rag_bachelor.core.bank import get_source_chunks
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.sheet import _WINDOW_SIZE
from rag_bachelor.study.store import add_podcast

_MAX_LINE = 400  # chars per spoken line: keeps each TTS call short
_PAUSE_S = 0.3  # silence between lines
_CITE = re.compile(r"\[p\.\d+\]")
_SCRIPT_SHARE = 0.3  # share of the progress bar given to writing the script

_PROMPT = (
    "Tu écris un extrait de podcast pédagogique en français, un dialogue entre deux voix :\n"
    "- A : l'expert, qui répond aux questions et explique clairement les notions ;\n"
    "- B : la podcasteuse, qui mène l'échange, pose les questions et reformule.\n"
    "Base-toi UNIQUEMENT sur l'extrait de cours fourni. Écris 6 à 12 répliques courtes, "
    "à l'oral (pas de listes, pas de Markdown, pas de numéros de page, pas d'émojis). "
    'Réponds uniquement avec le JSON {"lines": [{"speaker": "A", "text": "..."}]}.'
)

_tts: Any = None
_tts_lock = threading.Lock()


def _load_tts() -> Any:
    """Load the Phonon model once (first call downloads ~75 MB into HF_HOME)."""
    global _tts
    with _tts_lock:
        if _tts is None:
            import ptts

            _tts = ptts.TTS(settings.podcast_model, lang="fr", quant=settings.podcast_quant)
        return _tts


def _clean_lines(raw: str) -> list[dict[str, str]]:
    """Keep only well-formed ``{speaker: A|B, text}`` lines, citations removed."""
    data = loads_object(raw)
    entries = data.get("lines") if data else None
    lines: list[dict[str, str]] = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        speaker = str(e.get("speaker", "")).strip().upper()
        text = re.sub(r"\s+", " ", _CITE.sub("", str(e.get("text", "")))).strip()[:_MAX_LINE]
        if speaker in ("A", "B") and text:
            lines.append({"speaker": speaker, "text": text})
    return lines


def _wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def generate_podcast(
    source: str,
    page_from: int,
    page_to: int,
    progress_cb: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int | None:
    """Build, store and return the id of a podcast for ``source`` pages ``page_from..page_to``.

    Same contract as ``core.sheet.generate_sheet``: *progress_cb* gets ``(done, total)``,
    a stop returns ``None`` and stores nothing, ``ValueError`` means no usable text.
    """
    chunks = [c for c in get_source_chunks(source) if page_from <= c.page <= page_to]
    if not chunks:
        raise ValueError("no indexed text in that page range")
    windows = [chunks[i : i + _WINDOW_SIZE] for i in range(0, len(chunks), _WINDOW_SIZE)]
    provider, _name = get_provider()

    def report(fraction: float) -> None:
        if progress_cb:
            progress_cb(round(fraction * 1000), 1000)

    script: list[dict[str, str]] = []
    for i, window in enumerate(windows):
        if should_stop and should_stop():
            return None
        place = "début" if i == 0 else "fin" if i == len(windows) - 1 else "milieu"
        if len(windows) == 1:
            place = "début et fin"
        excerpt = "\n\n".join(f"[p.{c.page}]\n{c.text}" for c in window)
        raw = provider.chat(
            [
                {"role": "system", "content": _PROMPT},
                {
                    "role": "user",
                    "content": f"Position dans l'épisode : {place} (ouvre le sujet au début, "
                    f"conclus à la fin).\n\nExtrait de cours ({source}) :\n\n{excerpt}",
                },
            ],
            json_mode=True,
        )
        script += _clean_lines(raw)
        report(_SCRIPT_SHARE * (i + 1) / len(windows))
    if not script:
        raise ValueError("the model returned no usable dialogue")

    tts = _load_tts()
    voices = {"A": settings.podcast_voice_a, "B": settings.podcast_voice_b}
    pause = np.zeros(round(tts.sample_rate * _PAUSE_S), dtype=np.float32)
    parts: list[np.ndarray] = []
    timed: list[dict[str, object]] = []
    offset = 0
    for j, line in enumerate(script):
        if should_stop and should_stop():
            return None
        timed.append({**line, "start": round(offset / tts.sample_rate, 2)})
        speech = tts.synth(line["text"], voice=voices[line["speaker"]])
        parts += [speech, pause]
        offset += len(speech) + len(pause)
        report(_SCRIPT_SHARE + (1 - _SCRIPT_SHARE) * (j + 1) / len(script))

    samples = np.concatenate(parts)
    return add_podcast(
        source,
        page_from,
        page_to,
        json.dumps(timed, ensure_ascii=False),
        _wav_bytes(samples, tts.sample_rate),
        len(samples) / tts.sample_rate,
    )


def line_starts(lines: list[dict[str, Any]], audio: bytes) -> list[float]:
    """Start second of each script line, for podcasts stored before ``start`` was recorded.

    Every line is followed by exactly ``_PAUSE_S`` of digital silence, so when the WAV holds
    one such zero run per line, each run's end is the next line's start. Otherwise the
    duration is split in proportion to the text length.
    """
    with wave.open(io.BytesIO(audio)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if not lines or not len(pcm):
        return [0.0] * len(lines)
    edges = np.diff(np.concatenate(([0], (pcm == 0).astype(np.int8), [0])))
    run_starts, run_ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    run_ends = run_ends[run_ends - run_starts >= round(rate * _PAUSE_S)]
    if len(run_ends) == len(lines):
        return [0.0] + [round(int(e) / rate, 2) for e in run_ends[:-1]]
    sizes = [len(str(line.get("text", ""))) + 1 for line in lines]
    starts: list[float] = []
    seen = 0
    for size in sizes:
        starts.append(round(len(pcm) / rate * seen / sum(sizes), 2))
        seen += size
    return starts
