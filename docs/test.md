# Test Plan

## Unit and CLI Tests

The pytest suite runs offline. It creates temporary `.m4a` files, temporary data directories, and fake transcript CSVs. It does not inspect the real Voice Memos folder.

Covered areas:

- Argument parsing and subcommand availability.
- Sync dry-run planning, date filtering, and idempotent skip behavior.
- Cron line generation, dry-run output, and non-overwriting backup paths.
- Mock ASR transcript CSV shape with exactly `speaker,content` columns and controlled `--mock-text` content.
- Optional VAD argument parsing, offline VAD preprocessing fakes, MLX ASR routing through VAD output, and no-speech transcript handling.
- Codex driver prompt construction from the external prompt template with prompt-injection guardrails.
- Mock postprocess markdown, HTML, and meeting-note artifacts.
- `run-day` across sync, mock ASR, and mock postprocess.
- `doctor` JSON status with explicit path checks.

## Manual Validation

Run these commands from the repo root after installation. Unit tests may use mock engines for deterministic local assertions; the first-run setup guide in `skills/skill_intake.md` requires the sample to pass through real MLX Qwen3 ASR and Codex postprocessing before setup is complete.

```bash
python -m intake_skill --help
python -m intake_skill doctor --source examples/sample_audio --data-dir tmp/validation_data --repo-root .
python -m intake_skill make-sample-audio --output examples/sample_audio/sample.m4a --seconds 1
python -m intake_skill make-sample-audio --output examples/sample_audio/sample.wav --seconds 1
python -m intake_skill sync --source examples/sample_audio --data-dir tmp/validation_data --date 20260512 --dry-run
python -m intake_skill run-day --source examples/sample_audio --data-dir tmp/validation_data --date 20260512 --asr-engine mock --postprocess-engine mock --mock-text "Installer validation transcript."
```

The end-to-end mock validation should copy one sample `.m4a` into `tmp/validation_data/20260512/`, write `transcript_20260512.csv`, then write `daily_20260512.md`, `daily_20260512.html`, and `meetings/meeting_20260512.md`. If the sample file timestamp is different from `20260512`, update the command date to the sample file's modification day before running the validation.

For setup validation, install `mlx-qwen3-asr` in the venv, generate a non-private `.m4a` sample, sync it or place it under `tmp/validation_data/YYYYMMDD/`, run `python -m intake_skill asr --data-dir tmp/validation_data --date YYYYMMDD --engine mlx`, and verify the CSV has exactly `speaker,content` columns with non-empty content. This run must force the `Qwen/Qwen3-ASR-1.7B` model download and execution path. If MLX Qwen3 ASR cannot install, download its model, or transcribe the sample on the target Mac, setup is blocked and the exact failing command should be recorded. VAD is an optional runtime path: install `.[vad]` and add `--vad` only when validating silence removal before MLX ASR.

During setup, run a trivial non-private prompt through the user's configured Codex CLI:

```bash
codex exec --full-auto -c model_reasoning_effort=low "Reply with exactly: intake codex ok"
```

The CLI intentionally omits a `-m` flag so Codex uses the user's configured default model. Codex validation on synthetic sample transcripts is part of the default installer flow.

For live Voice Memos use, run `sync --dry-run` first and inspect the planned destination paths before copying private audio.

## Integration Notes

No live integration tests are enabled by default. MLX Qwen3 ASR depends on operator-installed `mlx-qwen3-asr` and a locally downloaded `Qwen/Qwen3-ASR-1.7B` model. Optional VAD depends on `ffmpeg` and the lockfile-covered `silero-vad` extra at runtime. Codex postprocessing depends on a configured `codex` CLI. Offline tests validate the Qwen ASR call shape, VAD routing with fakes, Codex prompt text, driver prompt, and command construction without invoking live integrations.
