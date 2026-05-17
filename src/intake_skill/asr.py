from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from typing import Any, cast

from .vad import cleanup_vad_temp, preprocess_with_vad

QWEN_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"


def day_dir(data_dir: Path, day: str) -> Path:
    return data_dir / day


def transcript_path(data_dir: Path, day: str) -> Path:
    return day_dir(data_dir, day) / f"transcript_{day}.csv"


def audio_files_for_day(data_dir: Path, day: str) -> list[Path]:
    directory = day_dir(data_dir, day)
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.m4a") if path.is_file())


def write_transcript(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["speaker", "content"], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({"speaker": row.get("speaker", ""), "content": row.get("content", "")})


def run_mock_asr(data_dir: Path, day: str, mock_text: str | None = None) -> dict[str, object]:
    files = audio_files_for_day(data_dir, day)
    rows = [
        {"speaker": "", "content": mock_text or f"Mock transcript for {path.name}."}
        for path in files
    ]
    output = transcript_path(data_dir, day)
    write_transcript(rows, output)
    return {"command": "asr", "engine": "mock", "day": day, "audio_count": len(files), "output_path": str(output), "output_dir": str(output.parent)}


def run_mlx_asr(
    data_dir: Path,
    day: str,
    vad: bool = False,
    vad_threshold: float = 0.5,
    vad_speech_pad_ms: int = 1000,
) -> dict[str, object]:
    try:
        mlx_qwen3_asr = importlib.import_module("mlx_qwen3_asr")
    except ImportError as exc:
        raise RuntimeError("mlx engine requires mlx-qwen3-asr to be installed in this environment") from exc

    rows: list[dict[str, str]] = []
    vad_meta: dict[str, object] = {}
    for path in audio_files_for_day(data_dir, day):
        audio_for_transcribe: Path = path
        if vad:
            processed, info = preprocess_with_vad(
                path,
                vad_threshold=vad_threshold,
                vad_speech_pad_ms=vad_speech_pad_ms,
            )
            vad_meta = info
            if processed is None:
                continue
            audio_for_transcribe = processed

        transcribe = cast(Any, mlx_qwen3_asr).transcribe
        try:
            result = transcribe(
                str(audio_for_transcribe),
                model=QWEN_ASR_MODEL,
                verbose=False,
                return_timestamps=False,
                return_chunks=True,
            )
        finally:
            if vad:
                cleanup_vad_temp(audio_for_transcribe)

        chunks = cast(list[dict[str, object]], getattr(result, "chunks", None) or [])
        for chunk in chunks:
            text = str(chunk.get("text", "")).strip()
            if text:
                rows.append({"speaker": "", "content": text})
    output = transcript_path(data_dir, day)
    write_transcript(rows, output)
    result: dict[str, object] = {
        "command": "asr",
        "engine": "mlx",
        "model": QWEN_ASR_MODEL,
        "day": day,
        "audio_count": len(audio_files_for_day(data_dir, day)),
        "row_count": len(rows),
        "output_path": str(output),
        "output_dir": str(output.parent),
        "user_note": "The first real transcription can take a little while because the local speech model may need to download or warm up.",
    }
    if vad:
        result["vad"] = vad_meta
    return result


def run_asr(
    data_dir: Path,
    day: str,
    engine: str = "mock",
    mock_text: str | None = None,
    vad: bool = False,
    vad_threshold: float = 0.5,
    vad_speech_pad_ms: int = 1000,
) -> dict[str, object]:
    if engine == "mock":
        return run_mock_asr(data_dir, day, mock_text=mock_text)
    if engine == "mlx":
        return run_mlx_asr(
            data_dir,
            day,
            vad=vad,
            vad_threshold=vad_threshold,
            vad_speech_pad_ms=vad_speech_pad_ms,
        )
    raise ValueError(f"unsupported ASR engine: {engine}")


def dumps_summary(summary: dict[str, object]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)
