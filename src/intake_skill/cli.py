from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .asr import run_asr
from .audio import make_sample_audio
from .config import DEFAULT_SOURCE_DIR, load_config
from .cron import append_cron
from .dashboard import serve_dashboard
from .postprocess import run_postprocess
from .sync import sync_voice_memos


def today() -> str:
    return datetime.now().strftime("%Y%m%d")


def json_print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", default=None, help=f"Voice Memos source directory. Default: {DEFAULT_SOURCE_DIR}")
    parser.add_argument("--data-dir", default=None, help="Local intake data directory. Default: ./data")
    parser.add_argument("--repo-root", default=None, help="Repository root. Default: installed package repository root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intake-skill", description="Voice Memos intake CLI for AI agents")
    parser.add_argument("--version", action="version", version=f"intake-skill {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local environment and paths")
    add_common_paths(doctor)

    sync = subparsers.add_parser("sync", help="Copy or convert Voice Memos into dated intake folders")
    add_common_paths(sync)
    sync.add_argument("--date", default=None, help="Limit sync to recordings from one day as YYYYMMDD")
    sync.add_argument("--dry-run", action="store_true", help="Plan sync without writing files")

    asr = subparsers.add_parser("asr", help="Transcribe one day of synced audio")
    add_common_paths(asr)
    asr.add_argument("--date", default=today(), help="Day to transcribe as YYYYMMDD")
    asr.add_argument("--engine", choices=["mock", "mlx"], default="mlx")
    asr.add_argument("--mock-text", default=None, help="Use this exact content for each mock transcript row")
    asr.add_argument("--vad", action="store_true", default=False, help="Enable VAD preprocessing to remove silence before transcription")
    asr.add_argument("--vad-threshold", type=float, default=0.5, help="VAD detection threshold (default: 0.5)")
    asr.add_argument("--vad-speech-pad-ms", type=int, default=1000, help="Padding in ms around speech segments (default: 1000)")

    post = subparsers.add_parser("postprocess", help="Generate daily reports from a transcript")
    add_common_paths(post)
    post.add_argument("--date", default=today(), help="Day to postprocess as YYYYMMDD")
    post.add_argument("--engine", choices=["mock", "codex"], default="codex")

    run_day = subparsers.add_parser("run-day", help="Run sync, ASR, and postprocess for one day")
    add_common_paths(run_day)
    run_day.add_argument("--date", default=today(), help="Day to process as YYYYMMDD")
    run_day.add_argument("--asr-engine", choices=["mock", "mlx"], default="mlx")
    run_day.add_argument("--postprocess-engine", choices=["mock", "codex"], default="codex")
    run_day.add_argument("--mock-text", default=None, help="Use this exact content for mock ASR during run-day")
    run_day.add_argument("--dry-run-sync", action="store_true", help="Plan sync only; ASR and postprocess still run against existing data")
    run_day.add_argument("--vad", action="store_true", default=False, help="Enable VAD preprocessing to remove silence before transcription")
    run_day.add_argument("--vad-threshold", type=float, default=0.5, help="VAD detection threshold (default: 0.5)")
    run_day.add_argument("--vad-speech-pad-ms", type=int, default=1000, help="Padding in ms around speech segments (default: 1000)")

    cron = subparsers.add_parser("install-cron", help="Set up the optional nightly run")
    add_common_paths(cron)
    cron.add_argument("--dry-run", action="store_true")

    dashboard = subparsers.add_parser("dashboard", help="Serve a local read-only monitoring dashboard")
    add_common_paths(dashboard)
    dashboard.add_argument("--host", default="127.0.0.1", help="Dashboard bind host")
    dashboard.add_argument("--port", type=int, default=8765, help="Dashboard port")

    sample = subparsers.add_parser("make-sample-audio", help="Create synthetic sample audio with no private content")
    add_common_paths(sample)
    sample.add_argument("--output", default="examples/sample.wav")
    sample.add_argument("--seconds", type=float, default=10.0)
    return parser


def doctor_payload(source: str | None = None, data_dir: str | None = None, repo_root: str | None = None) -> dict[str, object]:
    config = load_config(source, data_dir, repo_root)
    data_parent = config.data_dir if config.data_dir.exists() else config.data_dir.parent
    checks = {
        "source_exists": config.source_dir.exists(),
        "source_is_voice_memos_default": str(config.source_dir) == str(Path(DEFAULT_SOURCE_DIR).expanduser().resolve()),
        "data_parent_exists": data_parent.exists(),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "say_available": shutil.which("say") is not None,
        "codex_available": shutil.which("codex") is not None,
    }
    return {
        "command": "doctor",
        "status": "ok" if checks["data_parent_exists"] else "warning",
        "repo_root": str(config.repo_root),
        "source_dir": str(config.source_dir),
        "data_dir": str(config.data_dir),
        "checks": checks,
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.source, args.data_dir, args.repo_root)
    if args.command == "doctor":
        return doctor_payload(args.source, args.data_dir, args.repo_root)
    if args.command == "sync":
        return sync_voice_memos(config.source_dir, config.data_dir, dry_run=args.dry_run, day=args.date)
    if args.command == "asr":
        return run_asr(
            config.data_dir,
            args.date,
            engine=args.engine,
            mock_text=args.mock_text,
            vad=args.vad,
            vad_threshold=args.vad_threshold,
            vad_speech_pad_ms=args.vad_speech_pad_ms,
        )
    if args.command == "postprocess":
        return run_postprocess(config.data_dir, args.date, engine=args.engine)
    if args.command == "run-day":
        sync_summary = sync_voice_memos(config.source_dir, config.data_dir, dry_run=args.dry_run_sync, day=args.date)
        asr_summary = run_asr(
            config.data_dir,
            args.date,
            engine=args.asr_engine,
            mock_text=args.mock_text,
            vad=args.vad,
            vad_threshold=args.vad_threshold,
            vad_speech_pad_ms=args.vad_speech_pad_ms,
        )
        post_summary = run_postprocess(config.data_dir, args.date, engine=args.postprocess_engine)
        return {"command": "run-day", "day": args.date, "steps": [sync_summary, asr_summary, post_summary]}
    if args.command == "install-cron":
        return append_cron(config.repo_root, dry_run=args.dry_run)
    if args.command == "dashboard":
        server = serve_dashboard(args.host, args.port, config=config)
        return {"command": "dashboard", "url": server.url}
    if args.command == "make-sample-audio":
        return make_sample_audio(Path(args.output).expanduser().resolve(), seconds=args.seconds)
    raise ValueError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        json_print(run(args))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
