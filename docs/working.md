# Working Log

## Changelog

### 2026-05-12

- Created the public `intake_skill` CLI repo with package code, docs, prompts, skill instructions, scripts, and offline pytest coverage.
- Added deterministic mock ASR and mock postprocessing so unit tests and explicit offline debug flows can verify file wiring without live integrations.
- Added dry-run sync and dry-run cron paths before any operation touches user-owned audio or crontab state.
- Added sync `--date` filtering, ASR `--mock-text`, external Codex prompt-template loading, and explicit prompt-injection guardrails.
- Validation update: `python -m pytest -q` passed with 14 tests; `doctor` returned status `ok`; sample audio was generated as `examples/sample_audio/sample.wav` and `examples/sample_audio/sample.m4a`; the mock `run-day` flow copied one sample `.m4a` into `tmp/validation_data`, wrote `transcript_YYYYMMDD.csv`, `daily_YYYYMMDD.md`, `daily_YYYYMMDD.html`, and `meetings/meeting_YYYYMMDD.md`; Codex and MLX live integrations were not invoked.
- Reframed `README.md` as a human handoff page that tells users to give the GitHub URL to an AI agent, moved the detailed installer, operation, and debug playbook into `skills/skill_intake.md`, and documented that first-run setup must verify real MLX ASR on synthetic sample audio rather than stopping at mock validation.
- Updated Codex postprocessing to omit the hardcoded model flag so the Codex CLI uses the user's configured default model while keeping the existing `--full-auto -c model_reasoning_effort=low` invocation.
- Updated the installer guidance so agents install the repo as a project-local skill under `skills/intake-skill`, validate synthetic sample audio through real MLX ASR and Codex postprocessing by default, and offer cron only after explaining the Voice Memos sync and Mac wake/power requirements.
- Polished installer/user-facing copy to describe the optional nightly schedule in plain language, warn that the first speech-model run may take time, and point users to the generated output folder.
- Validation update: `uv pip install -e '.[dev]'` refreshed editable metadata; `python -m pytest -q` passed with 15 tests; LSP diagnostics were clean for changed Python files; CLI manual QA covered `--help`, mock postprocess happy path, invalid engine handling, and Codex command construction without a model flag.
- Added a read-only local dashboard served by `python -m intake_skill dashboard`, covering cron installation state, matching runtime processes, today's sync preview, recent day artifacts, key paths, and cron log tail.
- Expanded dashboard throughput metrics to show processed audio count, audio duration via `ffprobe`, transcript rows and characters, and generated Markdown report characters.
- Added local dashboard controls for manual same-day generation, cron schedule changes, cron disable, and macOS `caffeinate -i -s` awake mode that lets the display sleep while preventing system idle sleep.
- Added report links, last-run/error summary, today queue details, and safe `tmp/` cleanup controls to make the dashboard more operator-facing.
- Hardened the dashboard control boundary with per-server CSRF tokens, same-origin JSON POSTs, DOM/textContent rendering for API data, CSP/nosniff report responses, safer dashboard-managed `caffeinate` PID handling, and narrower cron ownership.
- Updated managed cron semantics so before-noon schedules process the previous calendar day and noon-or-later schedules process the current calendar day.
- Validation update: `python -m pytest -q` passed with 28 tests after adding dashboard status coverage, CLI parser coverage, cron schedule/remove coverage, CSRF coverage, log summary coverage, tmp cleanup coverage, and scheduled target-day coverage.

## Lessons Learned

- Keep the sync source restricted to Voice Memos. Expanding to other audio sources changes privacy and consent assumptions.
- The transcript CSV schema is an external contract: exactly `speaker,content`, with blank speaker values unless a future explicit requirement changes that boundary.
- Codex postprocessing is the default functional summarization path; validate it on synthetic sample audio before running real Voice Memos.
- Treat transcript text as untrusted source data inside any AI postprocessing prompt; spoken words can accidentally contain instruction-like phrases.
- A human-facing README should stop before operational detail; the AI-facing skill file is the right place for install gates, validation commands, artifact contracts, and concrete debugging branches.
- Monitoring needs a single human-facing surface. Cron, logs, data files, and process state are separate Unix primitives, so a small local dashboard reduces operator cognitive load without adding a resident background worker.
