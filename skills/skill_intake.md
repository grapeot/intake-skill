---
name: intake-skill
description: >-
  Install, verify, operate, and debug the Apple Voice Memos Intake Skill via python -m intake_skill.
disable-model-invocation: true
---

# Intake Skill

Use this skill when a user asks an AI agent to install or operate the Intake Skill repo. The expected user request is:

```text
Install this repo for me: https://github.com/grapeot/intake-skill
```

The job is complete only after the repo is cloned as a project-local skill, the local CLI is installed, tests pass, real local ASR has been installed and verified, a sample audio file has gone through sync, MLX Qwen3 ASR, and Codex postprocessing end to end, the operator has been shown where the output files are, and the operator has made an explicit choice about the optional nightly automatic run.

When talking to the operator, keep the cognitive burden low. Prefer phrases like "nightly automatic run", "runs every night", or "daily background run" instead of leading with terms such as "cron", "cron job", or "crontab". Use the technical terms only when showing exact commands, errors, or backup files. Explain setup as: "I can set this up to run once every night while your Mac is awake."

## Scope and Runtime Defaults

Intake Skill is Voice Memos only. It reads files that Apple Voice Memos has already synced to the Mac. Do not add microphone recording, non-Voice-Memos sources, speaker recognition, diarization, reference voices, or participant attribution.

Mock engines are retained for unit tests and explicit offline debugging. Functional setup and operation should use MLX Qwen3 ASR and Codex postprocessing by default. Before processing real Voice Memos, verify Codex with a trivial non-private prompt and validate Codex postprocessing on synthetic sample audio.

## Install from GitHub

Start in the user's current workspace and install this repo as a project-local skill, not as a standard global Codex skill. If `skills/intake-skill` already exists, inspect it rather than cloning over it.

```bash
mkdir -p skills
git clone https://github.com/grapeot/intake-skill.git skills/intake-skill
cd skills/intake-skill
uv venv .venv
source .venv/bin/activate
uv pip install -e '.[dev,qwen-asr]'
python -m pytest -q
python -m intake_skill --help
```

If `uv` is missing, install `uv` first by the user's normal package manager or mark setup blocked with the exact reason. Do not switch to a global `pip install` unless the user explicitly requests it.

## First-Run Setup Gate

Run `doctor` and inspect the JSON before touching private audio:

```bash
python -m intake_skill doctor
```

Interpret the JSON directly:

- `status: ok` means the data parent exists. Continue checking individual fields.
- `checks.source_exists: true` means the configured source path exists.
- `checks.source_is_voice_memos_default: true` means the source is the standard macOS Voice Memos container.
- `checks.ffmpeg_available: true` is required for `.qta` conversion and reliable `.m4a` sample generation.
- `checks.codex_available: true` is required only for Codex postprocessing.

The standard Voice Memos source is:

```text
~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/
```

If this path is absent or empty, do not invent another intake source. Open Voice Memos, let iCloud/local sync finish, and rerun `doctor` and `sync --dry-run`. If the user stores Voice Memos elsewhere, pass `--source` explicitly and document that choice.

## Install and Verify Real ASR

The mock engine is useful for offline tests, but initial setup must verify the real ASR path before declaring installation complete. Install MLX Qwen3 ASR dependencies inside the repo venv:

```bash
source .venv/bin/activate
python -c "import mlx_qwen3_asr; print('mlx-qwen3-asr import ok')"
```

Then force the model download and execution path by running ASR on a non-private sample through `--engine mlx`. The current CLI calls `mlx_qwen3_asr.transcribe()` with the explicit `Qwen/Qwen3-ASR-1.7B` model. A successful run should download or initialize that local Qwen ASR model and produce a non-empty `transcript_YYYYMMDD.csv` with exactly `speaker,content` columns.

Before running this step, tell the user in plain language that the first transcription may take a while because the speech model may need to download or warm up locally. Do not make that pause sound like a failure.

If the Mac, Python version, package resolver, or network cannot install `mlx-qwen3-asr`, or if the model cannot download or execute, mark setup blocked. Include the exact command, exit code, and error text. Do not declare setup complete based on mock engines.

VAD preprocessing is optional. If the user asks to remove silence before ASR, install the optional extra with `uv pip install -e '.[vad]'` or include it with the existing install extras, then pass `--vad` to `asr` or `run-day`. VAD uses ffmpeg plus the lockfile-covered `silero-vad` package at runtime. Do not make VAD part of the default setup gate unless the user asks for it.

## Sample Audio End-to-End Validation

Use synthetic audio with no private content. This validates the same file contracts the nightly job uses.

```bash
source .venv/bin/activate
export VALIDATION_DAY=$(date +%Y%m%d)
export VALIDATION_ROOT="tmp/intake_validation_$(date +%Y%m%d_%H%M%S)"
export VALIDATION_SOURCE="$VALIDATION_ROOT/source"
export VALIDATION_DATA="$VALIDATION_ROOT/data"
mkdir -p "$VALIDATION_SOURCE" "$VALIDATION_DATA"
python -m intake_skill make-sample-audio --output "$VALIDATION_SOURCE/sample.m4a" --seconds 3
python -m intake_skill sync --source "$VALIDATION_SOURCE" --data-dir "$VALIDATION_DATA" --date "$VALIDATION_DAY" --dry-run
python -m intake_skill sync --source "$VALIDATION_SOURCE" --data-dir "$VALIDATION_DATA" --date "$VALIDATION_DAY"
python -m intake_skill asr --data-dir "$VALIDATION_DATA" --date "$VALIDATION_DAY" --engine mlx
python - <<'PY'
import csv
import os
from pathlib import Path

data_dir = Path(os.environ["VALIDATION_DATA"])
day = os.environ["VALIDATION_DAY"]
transcript = data_dir / day / f"transcript_{day}.csv"
with transcript.open(newline="", encoding="utf-8") as handle:
    reader = csv.DictReader(handle)
    rows = list(reader)
assert reader.fieldnames == ["speaker", "content"]
assert rows and any((row.get("content") or "").strip() for row in rows)
print(f"ASR transcript ok: {transcript}")
PY
codex exec --full-auto -c model_reasoning_effort=low "Reply with exactly: intake codex ok"
python -m intake_skill postprocess --data-dir "$VALIDATION_DATA" --date "$VALIDATION_DAY" --engine codex
```

If `make-sample-audio` reports a `.wav` output instead of `.m4a`, install or fix `ffmpeg` and rerun the sample generation. Sync only processes `.m4a` and `.qta` files.

Verify these artifacts exist and contain expected content:

- `$VALIDATION_DATA/YYYYMMDD/YYYYMMDD_HHMM_watch.m4a`
- `$VALIDATION_DATA/YYYYMMDD/transcript_YYYYMMDD.csv`
- `$VALIDATION_DATA/YYYYMMDD/daily_YYYYMMDD.md`
- `$VALIDATION_DATA/YYYYMMDD/daily_YYYYMMDD.html`
- `$VALIDATION_DATA/YYYYMMDD/meetings/*.md`

The inline Python check is part of the setup gate. Do not rely only on the ASR command exit code; confirm that the generated CSV path exists, the header is exactly `speaker,content`, and at least one `content` cell is non-empty. If you inspect the CSV in a separate shell later, use the exact `output_path` printed by the ASR JSON or re-export `VALIDATION_DATA` and `VALIDATION_DAY` first. Open the Markdown and HTML reports and confirm they reflect the sample transcript. After validation succeeds, tell the user the exact output directory and, on macOS, open it in Finder when possible:

```bash
open "$VALIDATION_DATA/$VALIDATION_DAY"
```

If opening Finder fails, continue and give the user the path. If the user asks for a mock-only smoke test as a separate development check, run `run-day` with `--asr-engine mock --postprocess-engine mock`, but keep real MLX Qwen3 ASR and Codex postprocessing as the setup gate.

## Validate Codex During Setup

Codex uses the user's configured default model. The CLI must not pass a hardcoded `-m` model flag.

Before running real Voice Memos through Codex, verify the CLI with a trivial non-private prompt:

```bash
codex exec --full-auto -c model_reasoning_effort=low "Reply with exactly: intake codex ok"
```

If this fails, leave Codex disabled and record the exact failure. If it succeeds, continue with synthetic-sample Codex validation.

Then validate Codex on the synthetic sample:

```bash
python -m intake_skill postprocess --data-dir "$VALIDATION_DATA" --date "$VALIDATION_DAY" --engine codex
```

Verify that Codex writes the same artifact contract: daily Markdown, daily HTML, and meeting-note Markdown under the requested day directory.

## Real Voice Memos Operation

Preview first:

```bash
python -m intake_skill sync --date YYYYMMDD --dry-run
```

Inspect the JSON `items` array. Confirm sources are under the Voice Memos directory and destinations are under `data/YYYYMMDD/`. Then run the day:

```bash
python -m intake_skill run-day --date YYYYMMDD --asr-engine mlx --postprocess-engine codex
```

To remove silence before MLX ASR on a manual run, install the optional VAD extra and add `--vad`:

```bash
uv pip install -e '.[vad]'
python -m intake_skill run-day --date YYYYMMDD --asr-engine mlx --postprocess-engine codex --vad
```

Use the real Voice Memos path only after the synthetic-sample MLX and Codex validation succeeds.

## Nightly Automatic Run

The nightly automatic run is optional. Install it only after the user confirms this Mac should run Intake Skill every night. In user-facing conversation, describe this as "a small job that runs once each night while your Mac is awake"; do not lead with "cron".

First preview what would be installed and where the backup would go:

```bash
python -m intake_skill install-cron --dry-run
```

Before installing for real, back up the current schedule file yourself as an extra operator-visible checkpoint:

```bash
mkdir -p logs
crontab -l > logs/crontab_manual_backup_$(date +%Y%m%d_%H%M%S).txt 2>/dev/null || true
```

Then install only after confirmation:

```bash
python -m intake_skill install-cron
```

The CLI also writes a timestamped backup under `logs/crontab_backup_*.txt` and appends one marked line if absent. Append only; never replace the user's existing schedule. Do not install the nightly run during tests or documentation-only changes.

Remind the operator that the nightly run works only when the Mac is awake at the scheduled time and Voice Memos is running or syncing often enough for new recordings to appear in the source directory. Also tell the operator that Voice Memos iCloud sync should be enabled if they rely on it, the Mac should be plugged in, and macOS may show a permission dialog when the schedule is installed.

## Debug Playbook

### `doctor` JSON shows warnings

Read the specific `checks` keys. `data_parent_exists: false` means create or choose a valid parent directory. `source_exists: false` means Voice Memos has not synced locally or the source path override is wrong. `ffmpeg_available: false` affects `.qta` conversion and `.m4a` sample generation. `codex_available: false` affects only Codex postprocessing.

### Voice Memos source is empty

Confirm the default path exists, Voice Memos is open, iCloud sync is enabled if the user relies on it, and recordings are visible in the app. Rerun `sync --dry-run`. Do not switch to microphone recording or another audio directory as a workaround.

### `ffmpeg` is missing

Install it with the user's normal package manager, then rerun `doctor`. Without `ffmpeg`, `.qta` conversion fails and sample `.m4a` generation may fall back to `.wav`, which sync ignores.

### MLX install or model download fails

Rerun `source .venv/bin/activate`, `uv pip install mlx-qwen3-asr`, and the `python -c "import mlx_qwen3_asr"` check. Then rerun ASR on the synthetic sample. If package install, import, model download, or transcribe fails, setup is blocked. Report the exact failing command and error text.

### ASR CSV missing or wrong schema

Confirm `data/YYYYMMDD/` contains at least one `.m4a`. Run `python -m intake_skill asr --data-dir DATA --date YYYYMMDD --engine mlx`. Open `transcript_YYYYMMDD.csv`; the header must be exactly `speaker,content`. If the header differs, treat it as a contract failure. If rows are empty, check that audio files exist and the MLX transcribe call returned text.

### Codex unavailable

Run `which codex` and the trivial `codex exec --full-auto -c model_reasoning_effort=low "Reply with exactly: intake codex ok"` prompt. If either fails, keep `postprocess --engine codex` disabled. Mock postprocess remains available for local validation.

### Nightly Run Did Not Run

Check `crontab -l` for the `# intake_skill nightly run` marker. Inspect `logs/intake_cron.log`. Confirm the repo `.venv/bin/python` path still exists, the Mac was awake, and Voice Memos had synced files before midnight. Do not reinstall the nightly run by replacing the whole schedule; rerun `install-cron --dry-run` and compare the marker.

### Output files are missing

Trace the pipeline in order: sync should produce `.m4a` files under `data/YYYYMMDD/`; ASR should produce `transcript_YYYYMMDD.csv`; postprocess should produce `daily_YYYYMMDD.md`, `daily_YYYYMMDD.html`, and files under `meetings/`. Rerun the first missing stage with explicit `--data-dir`, `--source`, and `--date` arguments.

## Command Reference

```bash
python -m intake_skill doctor [--source PATH] [--data-dir PATH] [--repo-root PATH]
python -m intake_skill sync [--source PATH] [--data-dir PATH] [--date YYYYMMDD] [--dry-run]
python -m intake_skill asr [--data-dir PATH] [--date YYYYMMDD] [--engine mock|mlx] [--mock-text TEXT] [--vad] [--vad-threshold FLOAT] [--vad-speech-pad-ms N]
python -m intake_skill postprocess [--data-dir PATH] [--date YYYYMMDD] [--engine mock|codex]
python -m intake_skill run-day [--source PATH] [--data-dir PATH] [--date YYYYMMDD] [--asr-engine mock|mlx] [--postprocess-engine mock|codex] [--mock-text TEXT] [--dry-run-sync] [--vad] [--vad-threshold FLOAT] [--vad-speech-pad-ms N]
python -m intake_skill install-cron [--repo-root PATH] [--dry-run]
python -m intake_skill make-sample-audio --output PATH [--seconds N]
```

All commands print JSON summaries. Prefer parsing JSON fields instead of scraping prose.

## Artifact Contract

Sync reads only `.qta` and `.m4a` files. It writes normalized audio files under:

```text
data/YYYYMMDD/YYYYMMDD_HHMM_watch.m4a
```

ASR writes one CSV per day:

```text
data/YYYYMMDD/transcript_YYYYMMDD.csv
```

The CSV header is exactly:

```csv
speaker,content
```

The `speaker` column is blank in current engines. This skill does not infer identity.

Postprocess writes:

```text
data/YYYYMMDD/daily_YYYYMMDD.md
data/YYYYMMDD/daily_YYYYMMDD.html
data/YYYYMMDD/meetings/*.md
```

Existing synced audio destinations are skipped rather than overwritten. Nightly-run schedule backups live under `logs/crontab_backup_*.txt`; nightly-run output goes to `logs/intake_cron.log`.
