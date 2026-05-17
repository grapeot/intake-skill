from __future__ import annotations

import importlib
from types import SimpleNamespace
from pathlib import Path
from typing import Protocol


vad = importlib.import_module("intake_skill.vad")


class MonkeyPatch(Protocol):
    def setattr(self, target: object, name: str, value: object) -> None: ...


def test_vad_preprocess_detects_speech(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    audio = tmp_path / "test.m4a"
    audio.write_bytes(b"fake-audio")
    called: dict[str, bool] = {}

    def fake_convert(input_path: Path, output_path: Path) -> None:
        called["convert"] = True
        output_path.write_bytes(b"fake-wav-data")

    def fake_load_vad() -> tuple[object, object]:
        called["load_vad"] = True
        return object(), (None, None, None, None, None)

    def fake_detect_speech(
        wav_path: Path, model: object, utils: object,
        threshold: float, speech_pad_ms: int,
    ) -> tuple[list[dict[str, int]], object, int]:
        called["detect"] = True
        timestamps = [{"start": 0, "end": 8000}, {"start": 8000, "end": 16000}]
        return timestamps, object(), 16000

    def fake_concat(
        timestamps: list[dict[str, int]], wav: object,
        sample_rate: int, output_path: Path,
    ) -> None:
        called["concat"] = True
        output_path.write_bytes(b"fake-speech-wav")

    monkeypatch.setattr(vad, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(vad, "_load_silero_vad", fake_load_vad)
    monkeypatch.setattr(vad, "_detect_speech", fake_detect_speech)
    monkeypatch.setattr(vad, "_concatenate_chunks", fake_concat)

    processed_path, metadata = vad.preprocess_with_vad(audio)

    assert processed_path is not None
    assert processed_path.name == "speech.wav"
    assert processed_path.read_bytes() == b"fake-speech-wav"
    assert metadata["vad_applied"] is True
    assert metadata["speech_chunks"] == 2
    assert metadata["speech_duration_sec"] == 1.0
    assert called["convert"]
    assert called["detect"]
    assert called["concat"]

    assert processed_path.parent.exists()
    vad.cleanup_vad_temp(processed_path)
    assert not processed_path.parent.exists()


def test_vad_preprocess_no_speech(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    audio = tmp_path / "silent.m4a"
    audio.write_bytes(b"fake-audio")
    called: dict[str, bool] = {}

    def fake_convert(input_path: Path, output_path: Path) -> None:
        called["convert"] = True
        output_path.write_bytes(b"fake-wav-data")

    def fake_load_vad() -> tuple[object, object]:
        called["load_vad"] = True
        return object(), (None, None, None, None, None)

    def fake_detect_speech(
        wav_path: Path, model: object, utils: object,
        threshold: float, speech_pad_ms: int,
    ) -> tuple[list[dict[str, int]], object, int]:
        called["detect"] = True
        return [], object(), 16000

    monkeypatch.setattr(vad, "_convert_to_wav", fake_convert)
    monkeypatch.setattr(vad, "_load_silero_vad", fake_load_vad)
    monkeypatch.setattr(vad, "_detect_speech", fake_detect_speech)
    monkeypatch.setattr(vad.tempfile, "mkdtemp", lambda prefix: str(tmp_path / f"{prefix}test"))

    processed_path, metadata = vad.preprocess_with_vad(audio)

    assert processed_path is None
    assert metadata["vad_applied"] is True
    assert metadata["speech_chunks"] == 0
    assert metadata["speech_duration_sec"] == 0.0
    assert called["convert"]
    assert called["detect"]
    assert not (tmp_path / "intake_vad_test").exists()


def test_vad_cleanup_none_is_noop() -> None:
    vad.cleanup_vad_temp(None)


def test_vad_cleanup_nonexistent_path(tmp_path: Path) -> None:
    fake = tmp_path / "nonexistent" / "speech.wav"
    vad.cleanup_vad_temp(fake)


def test_vad_loads_lockfile_package_instead_of_torch_hub(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_import_module(name: str) -> object:
        captured["module"] = name

        def fake_load_silero_vad(onnx: bool = True) -> object:
            captured["onnx"] = onnx
            return "model"

        return SimpleNamespace(load_silero_vad=fake_load_silero_vad)

    monkeypatch.setattr(vad.importlib, "import_module", fake_import_module)

    model, module = vad._load_silero_vad()

    assert model == "model"
    assert module is not None
    assert captured == {"module": "silero_vad", "onnx": False}


def test_vad_params_passed_through(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    asr_module = importlib.import_module("intake_skill.asr")
    captured: dict[str, object] = {}

    def fake_vad(
        audio_path: Path,
        vad_threshold: float = 0.5,
        vad_speech_pad_ms: int = 1000,
    ) -> tuple[Path | None, dict[str, object]]:
        captured["vad_threshold"] = vad_threshold
        captured["vad_speech_pad_ms"] = vad_speech_pad_ms
        return None, {"vad_applied": True, "speech_chunks": 0, "speech_duration_sec": 0.0}

    monkeypatch.setattr(asr_module, "preprocess_with_vad", fake_vad)

    day = "20260512"
    day_dir = tmp_path / day
    day_dir.mkdir()
    (day_dir / "20260512_0930_watch.m4a").write_bytes(b"fake-audio")

    def fake_import(name: str) -> object:
        from types import SimpleNamespace
        assert name == "mlx_qwen3_asr"
        return SimpleNamespace(transcribe=lambda path, **kwargs: SimpleNamespace(chunks=[]))

    monkeypatch.setattr(asr_module.importlib, "import_module", fake_import)

    asr_module.run_asr(
        Path(tmp_path), day,
        engine="mlx",
        vad=True,
        vad_threshold=0.3,
        vad_speech_pad_ms=500,
    )

    assert captured["vad_threshold"] == 0.3
    assert captured["vad_speech_pad_ms"] == 500
