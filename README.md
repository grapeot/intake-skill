# Intake Skill

Intake Skill turns Apple Voice Memos that have already synced to a Mac into dated local intake artifacts: normalized audio files, transcript CSVs, daily Markdown and HTML reports, and meeting-note Markdown files.

This repo is meant to be installed by an AI agent as a project-local skill, not as a standard global Codex skill. Give the agent this URL from the workspace where you want the skill to live:

```text
https://github.com/grapeot/intake-skill
```

Ask it: "Install this repo for me: https://github.com/grapeot/intake-skill". The agent should clone the repo under the current folder, normally at `skills/intake-skill`, install Python dependencies, install `mlx-whisper`, tell the user that the first speech-model download may take a little while, transcribe synthetic sample audio, run sync, ASR, and Codex postprocessing end to end, show where the sample outputs were written, and then explain the optional nightly automatic run in plain language.

The detailed installer and operating guide lives in [`skills/skill_intake.md`](skills/skill_intake.md). Human readers normally do not need to run commands from this README; the skill file contains the exact playbook an AI agent should follow.

## What It Does

Intake Skill is intentionally narrow. It reads Apple Voice Memos files from the standard macOS Voice Memos container, copies or converts them into `data/YYYYMMDD/`, transcribes them into `transcript_YYYYMMDD.csv`, and generates daily artifacts from that transcript.

It does not record microphone audio. It does not add non-Voice-Memos intake. It does not perform speaker recognition, diarization, reference voice matching, or participant attribution.

The runtime pipeline is:

```text
Apple Voice Memos synced to this Mac
  -> sync local audio into data/YYYYMMDD/
  -> transcribe with MLX Whisper
  -> generate daily Markdown, HTML, and meeting notes with Codex
```

The main commands are:

```bash
python -m intake_skill doctor
python -m intake_skill sync --date YYYYMMDD --dry-run
python -m intake_skill run-day --date YYYYMMDD --asr-engine mlx --postprocess-engine codex
python -m intake_skill dashboard
```

Most operators use one of two modes:

- manual mode: run `run-day` when they want the latest Voice Memos processed
- scheduled mode: install cron once, then use the dashboard to monitor status and trigger manual catch-up runs when needed

Generated files live under:

```text
data/YYYYMMDD/
  YYYYMMDD_HHMM_watch.m4a
  transcript_YYYYMMDD.csv
  daily_YYYYMMDD.md
  daily_YYYYMMDD.html
  meetings/*.md
```

## Runtime Boundary

MLX ASR is intended to run locally after `mlx-whisper` and its model are installed. Codex postprocessing is the default AI summarization path for this workflow and uses the operator's configured local Codex CLI. Installer agents should validate Codex postprocessing on synthetic sample audio during setup.

The nightly automatic run is optional and should be enabled only after the operator confirms it. If enabled, the Mac must stay awake at the scheduled time, remain plugged in or otherwise powered, and Voice Memos must keep syncing often enough for new recordings to appear locally.

Cron runs with a minimal environment. The managed cron line sets a stable `PATH` that includes common Homebrew locations so tools such as `ffmpeg` and `ffprobe` can be found during scheduled runs.

Scheduled runs before noon process the previous calendar day. This makes a `00:01` run collect the Voice Memos from the day that just ended instead of creating an empty report for the new day. Scheduled runs at noon or later process the current calendar day.

## Local Dashboard

After installation, operators can start a local browser dashboard:

```bash
cd skills/intake-skill
source .venv/bin/activate
python -m intake_skill dashboard
```

The dashboard binds to `127.0.0.1:8765` by default. It is a local control surface for understanding how the pieces fit together:

- cron installation state, configured daily run time, and next scheduled run
- whether an intake, MLX ASR, or Codex postprocess process is currently running
- today's Voice Memos sync queue, including copy, convert, and skip actions
- recent processed days, audio counts, total audio duration, transcript rows and characters, generated report text, and report availability
- key local paths for source Voice Memos, generated data, and logs
- cron log tail plus a simple last-run status and last error summary
- temporary workspace size under `tmp/`

The dashboard also includes local controls:

- `Generate now`: run today's full `sync -> ASR -> Codex postprocess` pipeline immediately
- report links: open generated daily HTML reports from the recent-days table
- `Set schedule`: change the managed daily cron trigger time. Morning schedules process the previous calendar day; afternoon/evening schedules process the current calendar day.
- `Disable cron`: remove the managed Intake Skill cron entry while preserving unrelated crontab lines
- `Keep Mac awake`: start `caffeinate -i -s`, allowing the display to sleep while preventing system idle sleep
- `Stop awake mode`: stop the dashboard-managed `caffeinate` process
- `Clean tmp`: remove temporary files under this repo's `tmp/` directory without deleting `data/`

The dashboard is not a cloud service and does not run unless started. Its controls operate on local macOS primitives: `crontab`, `caffeinate`, local files, and the repo's Python CLI. Cron itself is separate from the dashboard; once installed, cron can run the pipeline even if the dashboard and Codex app are closed, provided the Mac is awake.

Dashboard binding guidance:

- `127.0.0.1` is the safe default and should be used on work or shared networks.
- Binding to a trusted home LAN or Tailscale address can be useful when the operator understands that anyone who can reach that address can try to access the dashboard.
- Do not expose this dashboard to the public internet.

The dashboard uses a per-server CSRF token for state-changing actions and serves generated report HTML with a restrictive Content Security Policy. Those protections reduce accidental cross-site control risk, but they do not turn the dashboard into an internet-facing application.

## For AI Agents

Use [`skills/skill_intake.md`](skills/skill_intake.md) as the source of truth for installation, verification, operation, debugging, command reference, and artifact contracts.
