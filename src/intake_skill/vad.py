from __future__ import annotations

import importlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TARGET_SAMPLE_RATE = 16_000


def preprocess_with_vad(
    audio_path: Path,
    vad_threshold: float = 0.5,
    vad_speech_pad_ms: int = 1000,
) -> tuple[Path | None, dict[str, object]]:
    """Convert audio to 16 kHz mono WAV, run Silero VAD, concatenate speech segments.

    Optional VAD dependencies are loaded lazily at runtime so the module
    itself is importable without them.

    Returns
    -------
    (Path to concatenated speech WAV, metadata) if speech is detected.
    (None, metadata with speech_chunks=0) if the file contains no speech.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="intake_vad_"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        wav_path = tmp_dir / "input.wav"
        _convert_to_wav(audio_path, wav_path)

        model, utils = _load_silero_vad()
        speech_timestamps, wav, sample_rate = _detect_speech(
            wav_path, model, utils, vad_threshold, vad_speech_pad_ms,
        )

        speech_duration = sum(
            (seg["end"] - seg["start"]) / float(sample_rate)
            for seg in speech_timestamps
        )

        if not speech_timestamps:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return None, {
                "vad_applied": True,
                "speech_chunks": 0,
                "speech_duration_sec": speech_duration,
            }

        output_path = tmp_dir / "speech.wav"
        _concatenate_chunks(speech_timestamps, wav, sample_rate, output_path)

        return output_path, {
            "vad_applied": True,
            "speech_chunks": len(speech_timestamps),
            "speech_duration_sec": speech_duration,
        }
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def cleanup_vad_temp(processed_path: Path | None) -> None:
    """Remove the temporary directory created for a VAD-processed file."""
    if processed_path is not None:
        shutil.rmtree(processed_path.parent, ignore_errors=True)


def _convert_to_wav(input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-i", str(input_path),
            "-ar", str(TARGET_SAMPLE_RATE),
            "-ac", "1",
            "-y",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _load_silero_vad() -> tuple[Any, Any]:
    silero_vad = importlib.import_module("silero_vad")
    model = silero_vad.load_silero_vad(onnx=False)
    return model, silero_vad


def _detect_speech(
    wav_path: Path,
    model: Any,
    utils: Any,
    threshold: float,
    speech_pad_ms: int,
) -> tuple[list[dict[str, int]], Any, int]:
    wav = utils.read_audio(str(wav_path), sampling_rate=TARGET_SAMPLE_RATE)
    wav = wav.float()
    timestamps = utils.get_speech_timestamps(
        wav,
        model,
        sampling_rate=TARGET_SAMPLE_RATE,
        threshold=threshold,
        speech_pad_ms=speech_pad_ms,
    )
    return timestamps, wav, TARGET_SAMPLE_RATE


def _concatenate_chunks(
    timestamps: list[dict[str, int]],
    wav: Any,
    sample_rate: int,
    output_path: Path,
) -> None:
    silero_vad = importlib.import_module("silero_vad")
    concatenated = silero_vad.collect_chunks(timestamps, wav)
    silero_vad.save_audio(str(output_path), concatenated, sampling_rate=sample_rate)
