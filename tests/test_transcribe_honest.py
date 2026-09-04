"""Task 2.4 — Transcription Honest Failure + Privacy Disclosure."""

import io
import sys
import types
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def _make_fake_sr(raise_on_recognize=None):
    """Create a fake speech_recognition module."""
    mod = types.ModuleType("speech_recognition")

    class UnknownValueError(Exception):
        pass

    class RequestError(Exception):
        pass

    class FakeAudioFile:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return "fake_src"
        def __exit__(self, *a):
            return False

    fake_recognizer = MagicMock()
    fake_audio = MagicMock()
    fake_recognizer.record.return_value = fake_audio
    if raise_on_recognize:
        fake_recognizer.recognize_google.side_effect = raise_on_recognize
    else:
        fake_recognizer.recognize_google.return_value = "hello world"

    mod.Recognizer = MagicMock(return_value=fake_recognizer)
    mod.AudioFile = FakeAudioFile
    mod.UnknownValueError = UnknownValueError
    mod.RequestError = RequestError
    return mod


def _make_fake_pydub(raise_on_from_file=None):
    mod = types.ModuleType("pydub")
    exc_mod = types.ModuleType("pydub.exceptions")

    class CouldntDecodeError(Exception):
        pass

    exc_mod.CouldntDecodeError = CouldntDecodeError

    fake_segment = MagicMock()
    def fake_export(path, format=None):
        Path(path).write_bytes(b"RIFF....WAVE")
    fake_segment.export.side_effect = fake_export

    fake_audio_segment = MagicMock()
    if raise_on_from_file:
        fake_audio_segment.from_file.side_effect = raise_on_from_file
    else:
        fake_audio_segment.from_file.return_value = fake_segment

    mod.AudioSegment = fake_audio_segment
    # attach exc_mod for caller to patch both
    mod._exc_mod = exc_mod
    return mod


def _client():
    from novi.webui_server import create_app
    app = create_app()
    return TestClient(app)


def _post_transcribe(client, data=b"fake audio bytes"):
    files = {"file": ("audio.webm", io.BytesIO(data), "audio/webm")}
    return client.post("/api/transcribe", files=files)


# ── success ───────────────────────────────────────────────────────────────

def test_success_returns_ok_true():
    fake_sr = _make_fake_sr()
    fake_pydub = _make_fake_pydub()
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake bytes")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["text"] == "hello world"
        assert "error" not in data


def test_empty_audio_returns_empty_error():
    # empty check does not need sr/pydub, but keep client creation outside patch
    client = _client()
    r = _post_transcribe(client, b"")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["text"] == ""
    assert data["error"] == "empty_audio"
    assert "detail" in data


# ── distinct errors ──────────────────────────────────────────────────────

def test_missing_speech_recognition():
    fake_pydub = _make_fake_pydub()
    fake_sr_ok = _make_fake_sr()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr_ok, "pydub": fake_pydub}):
        client = _client()
    # now post with missing sr (None in sys.modules triggers ModuleNotFoundError)
    with patch.dict(sys.modules, {"speech_recognition": None, "pydub": fake_pydub}):
        files = {"file": ("audio.webm", io.BytesIO(b"fake"), "audio/webm")}
        r = client.post("/api/transcribe", files=files)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "speech_recognition" in data["error"].lower()
        assert "detail" in data
        assert len(data["detail"]) > 0
        assert data["detail"][:500] == data["detail"]


def test_missing_pydub():
    fake_sr = _make_fake_sr()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr}):
        client = _client()
    # post with missing pydub
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": None, "pydub.exceptions": None}):
        files = {"file": ("audio.webm", io.BytesIO(b"fake"), "audio/webm")}
        r = client.post("/api/transcribe", files=files)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "pydub" in data["error"].lower()
        assert "detail" in data


def test_ffmpeg_missing_file_not_found():
    fake_sr = _make_fake_sr()
    fake_pydub = _make_fake_pydub(raise_on_from_file=FileNotFoundError("ffmpeg not found"))
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "ffmpeg" in data["error"].lower()
        assert "detail" in data


def test_ffmpeg_missing_os_error():
    fake_sr = _make_fake_sr()
    fake_pydub = _make_fake_pydub(raise_on_from_file=OSError("ffprobe not found, install ffmpeg"))
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "ffmpeg" in data["error"].lower()


def test_network_error():
    fake_sr = _make_fake_sr()
    fake_sr.Recognizer.return_value.recognize_google.side_effect = fake_sr.RequestError("network down: google.com")
    fake_pydub = _make_fake_pydub()
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["error"] == "network"
        assert "network" in data["detail"].lower() or "google" in data["detail"].lower()


def test_no_speech_error():
    fake_sr = _make_fake_sr()
    fake_sr.Recognizer.return_value.recognize_google.side_effect = fake_sr.UnknownValueError("no speech")
    fake_pydub = _make_fake_pydub()
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["error"] == "no_speech"
        assert "detail" in data


def test_unknown_error():
    fake_sr = _make_fake_sr()
    fake_pydub = _make_fake_pydub(raise_on_from_file=RuntimeError("something bizarre"))
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["error"] == "unknown"
        assert "detail" in data
        assert len(data["detail"]) <= 500


def test_detail_truncated_to_500():
    fake_sr = _make_fake_sr()
    long_msg = "x" * 2000
    fake_sr.Recognizer.return_value.recognize_google.side_effect = RuntimeError(long_msg)
    fake_pydub = _make_fake_pydub()
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"fake")
        data = r.json()
        assert data["ok"] is False
        assert len(data["detail"]) <= 500


# ── privacy disclosure ───────────────────────────────────────────────────

def test_privacy_disclosure_in_backend():
    text = Path("novi/webui_server.py").read_text(encoding="utf-8")
    assert "Transcription currently uses Google Speech API" in text
    assert "sends audio to google.com" in text
    assert "Offline alternative planned" in text
    assert "Disable mic to avoid" in text
    assert "Privacy:" in text
    assert "Privacy disclosure" in text


def test_privacy_disclosure_in_frontend():
    text = Path("novi/webui/src/components/chat/PromptInput.tsx").read_text(encoding="utf-8")
    assert "Transcription currently uses Google Speech API" in text
    assert "sends audio to google.com" in text
    assert "TRANSCRIPTION_PRIVACY_NOTE" in text or "Privacy" in text
    assert "TRANSCRIPTION_PRIVACY_NOTE" in text
    assert "micError" in text
    assert "setMicError" in text
    assert "data.ok" in text


def test_success_shape_has_ok_true_and_no_silent_empty():
    fake_sr = _make_fake_sr()
    fake_pydub = _make_fake_pydub()
    client = _client()
    with patch.dict(sys.modules, {"speech_recognition": fake_sr, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r = _post_transcribe(client, b"data")
        data = r.json()
        assert data["ok"] is True
        assert "text" in data
    fake_sr2 = _make_fake_sr()
    fake_sr2.Recognizer.return_value.recognize_google.side_effect = fake_sr2.RequestError("network")
    with patch.dict(sys.modules, {"speech_recognition": fake_sr2, "pydub": fake_pydub, "pydub.exceptions": fake_pydub._exc_mod}):
        r2 = _post_transcribe(client, b"data2")
        d2 = r2.json()
        assert d2["ok"] is False
        assert "error" in d2
        assert "detail" in d2
        assert d2["text"] == ""
