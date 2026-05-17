# Working Log

## Changelog

### 2026-05-17

- Added optional VAD (Voice Activity Detection) preprocessing for the MLX ASR engine. The new `--vad` flag on `asr` and `run-day` commands enables Silero VAD-based silence removal before transcription. Tuning flags `--vad-threshold` (default 0.5) and `--vad-speech-pad-ms` (default 1000) control VAD sensitivity and segment padding.
- Created `src/intake_skill/vad.py` as a standalone VAD utility module. It uses ffmpeg to resample to 16 kHz mono WAV, loads Silero VAD through the lockfile-covered `silero-vad` package at runtime, concatenates speech segments into a temporary WAV, and returns metadata when no speech is detected. All lazy imports ensure the module is importable without optional VAD dependencies.
- Added `[vad]` optional dependency group in `pyproject.toml` with `silero-vad>=6.0,<7`, `torch>=2.0`, and `torchaudio>=2.0`.
- VAD is disabled by default (`--vad` is a store-true flag). Existing default behavior (`python -m intake_skill asr --engine mlx`) produces identical output to before.
- Mock ASR accepts VAD flags but does not load torch, Silero, or ffmpeg.
- The transcript CSV contract (`speaker,content` with blank speakers) is unchanged.
- Added offline test coverage: `test_vad.py` tests VAD module with monkeypatched torch/hub dependencies (speech detection, no-speech skip, temp cleanup). `test_asr_postprocess.py` tests VAD+MLX integration (silent file skip, VAD-processed path routing, vad=False unchanged). `test_cli.py` tests VAD flag parsing and `run-day` flag propagation.
- Updated README, test notes, and the AI-facing skill guide to document optional VAD installation and command flags.
- Validation update: `uv lock` refreshed the optional VAD dependency graph; `python -m pytest -q` passed with 39 tests; LSP diagnostics were clean for changed Python files; CLI manual QA covered `python -m intake_skill asr --help`, `python -m intake_skill run-day --help`, and `python -m intake_skill asr --engine mock --vad`.

### 2026-05-13

- Replaced the live `mlx` ASR backend from `mlx-whisper` to `mlx-qwen3-asr`, matching the local life_record pipeline and explicitly using `Qwen/Qwen3-ASR-1.7B`.
- Kept the public CLI contract as `--engine mlx` / `--asr-engine mlx`, while updating runtime docs, installer prompts, README, PRD, RFC, and test plan to describe MLX Qwen3 ASR.
- Added offline test coverage that monkeypatches the Qwen module, verifies the exact transcribe call shape, and confirms chunk text is written to the existing `speaker,content` CSV contract.
- Validation update: `uv lock` refreshed the optional Qwen ASR dependency graph; `python -m pytest -q` passed with 28 tests; LSP diagnostics were clean for changed Python files; CLI manual QA covered `python -m intake_skill --help`.
- Live validation update: installed `.[dev,qwen-asr]`, generated synthetic `.m4a` audio, ran `sync`, ran real `asr --engine mlx`, triggered Qwen model initialization, and verified the generated transcript CSV contained `This is synthetic sample audio for intake skill.`.

### 2026-05-12

- Created the public `intake_skill` CLI repo with package code, docs, prompts, skill instructions, scripts, and offline pytest coverage.
- Added deterministic mock ASR and mock postprocessing so unit tests and explicit offline debug flows can verify file wiring without live integrations.
- Added dry-run sync and dry-run cron paths before any operation touches user-owned audio or crontab state.
- Added sync `--date` filtering, ASR `--mock-text`, external Codex prompt-template loading, and explicit prompt-injection guardrails.
- Validation update: `python -m pytest -q` passed with 14 tests; `doctor` returned status `ok`; sample audio was generated as `examples/sample_audio/sample.wav` and `examples/sample_audio/sample.m4a`; the mock `run-day` flow copied one sample `.m4a` into `tmp/validation_data`, wrote `transcript_YYYYMMDD.csv`, `daily_YYYYMMDD.md`, `daily_YYYYMMDD.html`, and `meetings/meeting_YYYYMMDD.md`; Codex and MLX live integrations were not invoked.
- Reframed `README.md` as a human handoff page that tells users to give the GitHub URL to an AI agent, moved the detailed installer, operation, and debug playbook into `skills/skill_intake.md`, and documented that first-run setup must verify real MLX Qwen3 ASR on synthetic sample audio rather than stopping at mock validation.
- Updated Codex postprocessing to omit the hardcoded model flag so the Codex CLI uses the user's configured default model while keeping the existing `--full-auto -c model_reasoning_effort=low` invocation.
- Updated the installer guidance so agents install the repo as a project-local skill under `skills/intake-skill`, validate synthetic sample audio through real MLX Qwen3 ASR and Codex postprocessing by default, and offer cron only after explaining the Voice Memos sync and Mac wake/power requirements.
- Polished installer/user-facing copy to describe the optional nightly schedule in plain language, warn that the first speech-model run may take time, and point users to the generated output folder.
- Validation update: `uv pip install -e '.[dev]'` refreshed editable metadata; `python -m pytest -q` passed with 15 tests; LSP diagnostics were clean for changed Python files; CLI manual QA covered `--help`, mock postprocess happy path, invalid engine handling, and Codex command construction without a model flag.
- Added a read-only local dashboard served by `python -m intake_skill dashboard`, covering cron installation state, matching runtime processes, today's sync preview, recent day artifacts, key paths, and cron log tail.
- Expanded dashboard throughput metrics to show processed audio count, audio duration via `ffprobe`, transcript rows and characters, and generated Markdown report characters.
- Added local dashboard controls for manual `run-day`, cron schedule changes, cron disable, and macOS `caffeinate -i -s` awake mode that lets the display sleep while preventing system idle sleep.
- Added selected-day processing, local report opening, last-run/error summary, today queue details, and safe `tmp/` cleanup controls to make the dashboard more operator-facing.
- Validation update: `python -m pytest -q` passed with 23 tests after adding dashboard status coverage, CLI parser coverage, cron schedule/remove coverage, selected-day run coverage, log summary coverage, and tmp cleanup coverage.

## Lessons Learned

- When importing a function with `from .module import func`, monkeypatching the source module does not affect the importing module's local name binding. Tests must patch the target namespace (`cli.func`) not the source namespace (`module.func`).
- VAD preprocessing introduces a cross-module dependency pattern: the ASR pipeline imports VAD functions at module level, which makes them straightforward to monkeypatch in tests but means they must not import optional VAD deps at the module level either. Lazy imports inside the VAD functions solve this cleanly.
- Test monkeypatches of private module functions (`_load_silero_vad`, `_detect_speech`, etc.) need to cover the entire call chain. Testing `preprocess_with_vad` requires mocking all three private functions it calls, not just the leaf functions.
- Protocol types for test modules need explicit update when function signatures change. The `AsrModule` Protocol in test fixtures must match the runtime API to keep type checkers satisfied.

- Keep the sync source restricted to Voice Memos. Expanding to other audio sources changes privacy and consent assumptions.
- The transcript CSV schema is an external contract: exactly `speaker,content`, with blank speaker values unless a future explicit requirement changes that boundary.
- Codex postprocessing is the default functional summarization path; validate it on synthetic sample audio before running real Voice Memos.
- Treat transcript text as untrusted source data inside any AI postprocessing prompt; spoken words can accidentally contain instruction-like phrases.
- A human-facing README should stop before operational detail; the AI-facing skill file is the right place for install gates, validation commands, artifact contracts, and concrete debugging branches.
- Monitoring needs a single human-facing surface. Cron, logs, data files, and process state are separate Unix primitives, so a small read-only dashboard reduces operator cognitive load without adding a resident background worker.
- The `mlx` engine name is a local-backend contract, not a model-family contract. Keep it stable while changing the concrete ASR library underneath.
- Live ASR validation needs an artifact check, not just a successful process exit. Keep transcript inspection in the same shell as the validation variables, or use the exact `output_path` printed by the ASR JSON.
