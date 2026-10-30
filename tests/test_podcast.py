"""Podcast tests: script validation, fake-TTS synthesis to MP3, store, audio route."""

from __future__ import annotations

import io
import json
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rag_bachelor.core import podcast
from rag_bachelor.core.bank import SourceChunk
from rag_bachelor.study import store


class FakeTTS:
    sample_rate = 8000  # a rate MP3 supports

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synth(self, text: str, *, voice: str) -> np.ndarray:
        self.calls.append((text, voice))
        return np.full(4000, 0.5, dtype=np.float32)  # 0.5 s


def _run(reply: str, tts: FakeTTS | None = None, lo: int = 1, hi: int = 2) -> int | None:
    provider = MagicMock()
    provider.chat = MagicMock(return_value=reply)
    chunks = [SourceChunk(f"c.pdf__p{p}__c0", p, 0, f"texte {p}") for p in (1, 2, 3)]
    with (
        patch.object(podcast, "get_source_chunks", return_value=chunks),
        patch.object(podcast, "get_provider", return_value=(provider, "ollama")),
        patch.object(podcast, "_load_tts", return_value=tts or FakeTTS()),
    ):
        return podcast.generate_podcast("c.pdf", lo, hi)


def test_clean_lines_keeps_valid_and_strips_citations() -> None:
    raw = json.dumps(
        {
            "lines": [
                {"speaker": "a", "text": "Le TCP [p.3] est fiable."},
                {"speaker": "C", "text": "intrus"},
                {"speaker": "B", "text": "   "},
                "junk",
                {"speaker": "B", "text": "x" * 1000},
            ]
        }
    )
    lines = podcast._clean_lines(raw)
    assert lines[0] == {"speaker": "A", "text": "Le TCP est fiable."}
    assert len(lines) == 2 and len(lines[1]["text"]) == podcast._MAX_LINE
    assert podcast._clean_lines("pas du json") == []


def test_generate_synthesizes_two_voices_into_mp3() -> None:
    reply = json.dumps(
        {"lines": [{"speaker": "A", "text": "Bonjour."}, {"speaker": "B", "text": "Salut."}]}
    )
    tts = FakeTTS()
    pid = _run(reply, tts)
    assert pid is not None
    assert [v for _, v in tts.calls] == [
        podcast.settings.podcast_voice_a,
        podcast.settings.podcast_voice_b,
    ]
    audio = store.get_podcast_audio(pid)
    assert audio is not None and podcast.audio_mime(audio[:4]) == "audio/mpeg"
    assert audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0  # MPEG frame sync
    saved = store.get_podcast(pid)
    # two lines, each followed by 0.3 s of silence
    assert saved is not None and abs(saved.duration - 1.6) < 1e-6
    assert json.loads(saved.script)[1]["speaker"] == "B"


def test_no_usable_dialogue_or_empty_range_raises() -> None:
    with pytest.raises(ValueError):
        _run('{"lines": []}')
    with pytest.raises(ValueError):
        _run("{}", lo=50, hi=60)


def test_store_list_delete_and_rename() -> None:
    pid = store.add_podcast("a.pdf", 1, 3, "[]", b"RIFF", 2.0)
    assert [p.id for p in store.list_podcasts()] == [pid]
    store.rename_document("a.pdf", "b.pdf")
    renamed = store.get_podcast(pid)
    assert renamed is not None and renamed.source == "b.pdf"
    store.delete_podcast(pid)
    assert store.get_podcast_audio(pid) is None


def test_generate_records_line_starts() -> None:
    reply = json.dumps(
        {"lines": [{"speaker": "A", "text": "Un."}, {"speaker": "B", "text": "Deux, plus long."}]}
    )
    pid = _run(reply)
    assert pid is not None
    saved = store.get_podcast(pid)
    assert saved is not None
    assert [line["start"] for line in json.loads(saved.script)] == [0.0, 0.8]  # 0.5 + 0.3 s


def test_line_starts_from_silence_in_legacy_wav() -> None:
    speech, pause = np.full(500, 0.5, dtype=np.float32), np.zeros(300, dtype=np.float32)
    wav = podcast._wav_bytes(np.concatenate([speech, pause, speech, pause]), 1000)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 1000 and w.getnframes() == 1600
    lines = [{"speaker": "A", "text": "Un."}, {"speaker": "B", "text": "Deux, plus long."}]
    assert podcast.line_starts(lines, wav) == [0.0, 0.8]


def test_line_starts_falls_back_to_text_length() -> None:
    samples = np.full(1000, 0.5, dtype=np.float32)  # 1 s, no silence at all
    lines = [{"speaker": "A", "text": "x" * 9}, {"speaker": "B", "text": "y" * 9}]
    assert podcast.line_starts(lines, podcast._wav_bytes(samples, 1000)) == [0.0, 0.5]
    assert podcast.line_starts([], podcast._wav_bytes(samples, 1000)) == []


def test_audio_route_serves_byte_ranges() -> None:
    from fastapi.testclient import TestClient

    from rag_bachelor.app.web import server

    data = bytes(range(256)) * 4  # 1024 bytes
    pid = store.add_podcast("a.pdf", 1, 2, "[]", data, 1.0)
    url = f"/podcasts/{pid}/audio"
    with TestClient(server.app) as client:
        full = client.get(url)
        assert full.status_code == 200 and full.content == data
        assert full.headers["accept-ranges"] == "bytes"
        assert full.headers["content-type"] == "audio/mpeg"
        part = client.get(url, headers={"Range": "bytes=10-19"})
        assert part.status_code == 206 and part.content == data[10:20]
        assert part.headers["content-range"] == "bytes 10-19/1024"
        assert client.get(url, headers={"Range": "bytes=1000-"}).content == data[1000:]
        assert client.get(url, headers={"Range": "bytes=-4"}).content == data[-4:]
        assert client.get(url, headers={"Range": "bytes=1020-5000"}).content == data[1020:]
        bad = client.get(url, headers={"Range": "bytes=2000-"})
        assert bad.status_code == 416 and bad.headers["content-range"] == "bytes */1024"
        assert client.get(url, headers={"Range": "bytes=0-1,5-6"}).status_code == 200
        assert client.get("/podcasts/999/audio").status_code == 404
        wav = store.add_podcast("a.pdf", 1, 2, "[]", b"RIFF" + data, 1.0)
        legacy = client.get(f"/podcasts/{wav}/audio.wav", headers={"Range": "bytes=0-3"})
        assert legacy.content == b"RIFF" and legacy.headers["content-type"] == "audio/wav"


def test_view_makes_transcript_lines_seekable() -> None:
    from fastapi.testclient import TestClient

    from rag_bachelor.app.web import server

    samples = np.concatenate([np.full(500, 0.5), np.zeros(300), np.full(500, 0.5), np.zeros(300)])
    legacy = json.dumps([{"speaker": "A", "text": "Un."}, {"speaker": "B", "text": "Deux."}])
    pid = store.add_podcast(
        "a.pdf", 1, 2, legacy, podcast._wav_bytes(samples.astype(np.float32), 1000), 1.6
    )
    with TestClient(server.app) as client:
        page = client.get(f"/podcasts/{pid}").text
    assert 'data-start="0.0"' in page and 'data-start="0.8"' in page
