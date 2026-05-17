from __future__ import annotations

import importlib
import json


cli = importlib.import_module("intake_skill.cli")


def test_parser_accepts_required_commands() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["doctor"]).command == "doctor"
    sync_args = parser.parse_args(["sync", "--dry-run", "--date", "20260512"])
    assert sync_args.dry_run is True
    assert sync_args.date == "20260512"
    asr_args = parser.parse_args(["asr", "--engine", "mock", "--mock-text", "installer text"])
    assert asr_args.engine == "mock"
    assert asr_args.mock_text == "installer text"
    assert parser.parse_args(["asr"]).engine == "mlx"
    assert parser.parse_args(["postprocess", "--engine", "codex"]).engine == "codex"
    assert parser.parse_args(["postprocess"]).engine == "codex"
    assert parser.parse_args(["run-day", "--asr-engine", "mock", "--postprocess-engine", "mock"]).command == "run-day"
    run_day_defaults = parser.parse_args(["run-day"])
    assert run_day_defaults.asr_engine == "mlx"
    assert run_day_defaults.postprocess_engine == "codex"
    assert parser.parse_args(["install-cron", "--dry-run"]).dry_run is True
    dashboard_args = parser.parse_args(["dashboard", "--port", "8766"])
    assert dashboard_args.command == "dashboard"
    assert dashboard_args.port == 8766
    assert parser.parse_args(["make-sample-audio", "--seconds", "1"]).seconds == 1


def test_parser_accepts_vad_flags() -> None:
    parser = cli.build_parser()
    asr_args = parser.parse_args(["asr", "--vad", "--vad-threshold", "0.3", "--vad-speech-pad-ms", "500"])
    assert asr_args.vad is True
    assert asr_args.vad_threshold == 0.3
    assert asr_args.vad_speech_pad_ms == 500

    asr_defaults = parser.parse_args(["asr"])
    assert asr_defaults.vad is False
    assert asr_defaults.vad_threshold == 0.5
    assert asr_defaults.vad_speech_pad_ms == 1000

    run_day_args = parser.parse_args(["run-day", "--vad", "--vad-threshold", "0.4", "--vad-speech-pad-ms", "800"])
    assert run_day_args.vad is True
    assert run_day_args.vad_threshold == 0.4
    assert run_day_args.vad_speech_pad_ms == 800

    run_day_defaults = parser.parse_args(["run-day"])
    assert run_day_defaults.vad is False
    assert run_day_defaults.vad_threshold == 0.5
    assert run_day_defaults.vad_speech_pad_ms == 1000


def test_run_day_with_vad_passes_flags_to_asr(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / "source"
    data = tmp_path / "data"
    source.mkdir()
    audio = source / "memo.m4a"
    audio.write_bytes(b"fake-audio")

    captured = {}

    def fake_asr(data_dir, day, engine="mock", mock_text=None, vad=False, vad_threshold=0.5, vad_speech_pad_ms=1000):
        captured["vad"] = vad
        captured["vad_threshold"] = vad_threshold
        captured["vad_speech_pad_ms"] = vad_speech_pad_ms
        return {"command": "asr", "engine": engine, "day": day, "output_path": str(tmp_path / "out.csv")}

    monkeypatch.setattr(cli, "run_asr", fake_asr)
    monkeypatch.setattr(cli, "sync_voice_memos", lambda source_dir, data_dir, dry_run=False, day=None: {"command": "sync", "copied": 0})
    monkeypatch.setattr(cli, "run_postprocess", lambda data_dir, day, engine="mock": {"command": "postprocess", "engine": "mock"})

    code = cli.main([
        "run-day", "--date", "20260512", "--source", str(source),
        "--data-dir", str(data), "--repo-root", str(tmp_path),
        "--asr-engine", "mock", "--postprocess-engine", "mock",
        "--vad", "--vad-threshold", "0.35", "--vad-speech-pad-ms", "600",
    ])
    assert code == 0
    assert captured["vad"] is True
    assert captured["vad_threshold"] == 0.35
    assert captured["vad_speech_pad_ms"] == 600


def test_doctor_cli_outputs_json(tmp_path, capsys) -> None:
    code = cli.main(["doctor", "--source", str(tmp_path), "--data-dir", str(tmp_path / "data"), "--repo-root", str(tmp_path)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "doctor"
    assert payload["checks"]["source_exists"] is True
    assert payload["data_dir"] == str(tmp_path / "data")
