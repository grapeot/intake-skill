from __future__ import annotations

import json
import csv
import os
import secrets
import shutil
import signal
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, cast
from urllib.parse import urlparse

from .asr import transcript_path
from .config import IntakeConfig, load_config
from .cron import CRON_MARKER, cron_line, legacy_unmanaged_cron_lines, managed_cron_lines, read_current_crontab, remove_managed_cron, replace_managed_cron
from .sync import build_sync_plan


@dataclass(frozen=True)
class DashboardServer:
    host: str
    port: int
    url: str


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _read_tail(path: Path, max_lines: int = 120) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]


def _text_chars(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8", errors="replace").strip())


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float_value(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _audio_duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _next_daily_run(hour: int, minute: int, now: datetime | None = None) -> str:
    current = now or datetime.now()
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate.isoformat(timespec="seconds")


def _current_processes() -> list[dict[str, str]]:
    try:
        result = subprocess.run(["pgrep", "-fl", "intake_skill (run-day|asr|postprocess)|codex exec|mlx_whisper"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        return []
    processes: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            processes.append({"pid": parts[0], "command": parts[1]})
    return processes


def _pid_is_running(pid: int) -> bool:
    try:
        subprocess.run(["kill", "-0", str(pid)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        return False


def _process_command(pid: int) -> str | None:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    command = result.stdout.strip()
    return command or None


def _awake_pid_path(config: IntakeConfig) -> Path:
    return config.repo_root / "logs" / "intake_awake.pid"


def _read_awake_metadata(config: IntakeConfig) -> dict[str, object] | None:
    path = _awake_pid_path(config)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return {"pid": int(raw), "command": ["caffeinate", "-i", "-s"], "legacy": True}
        except ValueError:
            path.unlink(missing_ok=True)
            return None
    if isinstance(metadata, dict):
        return dict(metadata)
    path.unlink(missing_ok=True)
    return None


def _is_expected_caffeinate(pid: int) -> bool:
    command = _process_command(pid)
    if command is None:
        return False
    return "caffeinate" in command and " -i" in f" {command}" and " -s" in f" {command}"


def _awake_status(config: IntakeConfig) -> dict[str, object]:
    path = _awake_pid_path(config)
    mode = "display may sleep, system stays awake"
    metadata = _read_awake_metadata(config)
    if metadata is None:
        return {"active": False, "pid": None, "mode": mode}
    pid_value = metadata.get("pid")
    if not isinstance(pid_value, int | str):
        path.unlink(missing_ok=True)
        return {"active": False, "pid": None, "mode": mode}
    pid = int(pid_value)
    if not _pid_is_running(pid) or not _is_expected_caffeinate(pid):
        path.unlink(missing_ok=True)
        return {"active": False, "pid": None, "mode": mode}
    return {"active": True, "pid": pid, "mode": mode}


def start_awake(config: IntakeConfig) -> dict[str, object]:
    current = _awake_status(config)
    if current["active"]:
        return {"ok": True, "changed": False, "awake": current}
    caffeinate = shutil.which("caffeinate")
    if not caffeinate:
        return {"ok": False, "error": "caffeinate command not found"}
    (config.repo_root / "logs").mkdir(parents=True, exist_ok=True)
    command = [caffeinate, "-i", "-s"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    metadata = {
        "pid": process.pid,
        "command": command,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    _awake_pid_path(config).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return {"ok": True, "changed": True, "awake": _awake_status(config)}


def stop_awake(config: IntakeConfig) -> dict[str, object]:
    current = _awake_status(config)
    if not current["active"]:
        return {"ok": True, "changed": False, "awake": current}
    pid = _int_value(current.get("pid"))
    if not _is_expected_caffeinate(pid):
        _awake_pid_path(config).unlink(missing_ok=True)
        return {"ok": False, "error": "recorded pid is not the expected caffeinate process", "awake": _awake_status(config)}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    _awake_pid_path(config).unlink(missing_ok=True)
    return {"ok": True, "changed": True, "awake": _awake_status(config)}


def _validate_day(day: str) -> str:
    if len(day) != 8 or not day.isdigit():
        raise ValueError("day must be YYYYMMDD")
    return day


def run_now(config: IntakeConfig) -> dict[str, object]:
    if _current_processes():
        return {"ok": False, "error": "intake is already running"}
    (config.repo_root / "logs").mkdir(parents=True, exist_ok=True)
    log_path = config.repo_root / "logs" / "intake_cron.log"
    python_path = config.repo_root / ".venv" / "bin" / "python"
    command = [str(python_path), "-m", "intake_skill", "run-day", "--asr-engine", "mlx", "--postprocess-engine", "codex"]
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== manual run started {datetime.now().isoformat(timespec='seconds')} day=today ===\n")
        process = subprocess.Popen(command, cwd=config.repo_root, stdout=log, stderr=subprocess.STDOUT)
    return {"ok": True, "pid": process.pid, "day": "today", "log_path": str(log_path)}


def cleanup_tmp(config: IntakeConfig) -> dict[str, object]:
    tmp_dir = config.repo_root / "tmp"
    if not tmp_dir.exists():
        return {"ok": True, "removed_entries": 0, "removed_bytes": 0}
    removed_entries = 0
    removed_bytes = 0
    for child in tmp_dir.iterdir():
        if child.is_dir():
            removed_bytes += sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
            shutil.rmtree(child)
        else:
            removed_bytes += child.stat().st_size
            child.unlink()
        removed_entries += 1
    return {"ok": True, "removed_entries": removed_entries, "removed_bytes": removed_bytes}


def _tmp_status(config: IntakeConfig) -> dict[str, object]:
    tmp_dir = config.repo_root / "tmp"
    if not tmp_dir.exists():
        return {"exists": False, "entries": 0, "bytes": 0}
    entries = list(tmp_dir.iterdir())
    total_bytes = 0
    for entry in entries:
        if entry.is_dir():
            total_bytes += sum(path.stat().st_size for path in entry.rglob("*") if path.is_file())
        elif entry.is_file():
            total_bytes += entry.stat().st_size
    return {"exists": True, "entries": len(entries), "bytes": total_bytes}


def _log_summary(lines: list[str], running: bool) -> dict[str, object]:
    if running:
        status = "running"
    elif not lines:
        status = "not run yet"
    else:
        marker_index = next((index for index in range(len(lines) - 1, -1, -1) if lines[index].startswith("===")), -1)
        recent = lines[marker_index + 1 :] if marker_index >= 0 else lines[-80:]
        status = "needs review" if any(_looks_like_error(line) for line in recent) else "last log ok"
    last_marker = next((line for line in reversed(lines) if line.startswith("===")), None)
    if last_marker:
        marker_index = max(index for index, line in enumerate(lines) if line == last_marker)
        error_lines = lines[marker_index + 1 :]
    else:
        error_lines = lines
    last_error = next((line for line in reversed(error_lines) if _looks_like_error(line)), None)
    return {"status": status, "last_error": last_error, "last_marker": last_marker}


def _looks_like_error(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ["error", "traceback", "exception", "failed", "status\": \"error"])


def _transcript_stats(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False, "rows": 0, "non_empty_rows": 0, "content_chars": 0}
    rows = 0
    non_empty = 0
    content_chars = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            content = row.get("content", "").strip()
            rows += 1
            content_chars += len(content)
            if content:
                non_empty += 1
    return {"exists": True, "rows": rows, "non_empty_rows": non_empty, "content_chars": content_chars}


def _generated_text_stats(directory: Path, day: str) -> dict[str, object]:
    daily_md = directory / f"daily_{day}.md"
    meetings_dir = directory / "meetings"
    meeting_files = sorted(meetings_dir.glob("*.md")) if meetings_dir.exists() else []
    meeting_chars = sum(_text_chars(path) for path in meeting_files)
    daily_chars = _text_chars(daily_md)
    return {
        "daily_md_chars": daily_chars,
        "meeting_md_chars": meeting_chars,
        "total_chars": daily_chars + meeting_chars,
    }


def _day_summaries(data_dir: Path, limit: int = 14) -> list[dict[str, object]]:
    if not data_dir.exists():
        return []
    days = sorted((path for path in data_dir.iterdir() if path.is_dir() and path.name.isdigit()), key=lambda item: item.name, reverse=True)
    summaries: list[dict[str, object]] = []
    for directory in days[:limit]:
        day = directory.name
        audio_files = sorted(directory.glob("*.m4a"))
        durations = [_audio_duration_seconds(path) for path in audio_files]
        known_duration = [duration for duration in durations if duration is not None]
        meetings_dir = directory / "meetings"
        meeting_files = sorted(meetings_dir.glob("*.md")) if meetings_dir.exists() else []
        mtimes = [path.stat().st_mtime for path in directory.rglob("*") if path.is_file()]
        transcript = transcript_path(data_dir, day)
        transcript_stats = _transcript_stats(transcript)
        generated_text = _generated_text_stats(directory, day)
        transcript_chars = _int_value(transcript_stats.get("content_chars"))
        generated_chars = _int_value(generated_text.get("total_chars"))
        summaries.append(
            {
                "day": day,
                "audio_count": len(audio_files),
                "audio_duration_seconds": round(sum(known_duration), 3) if known_duration else None,
                "audio_duration_known": len(known_duration),
                "transcript": transcript_stats,
                "generated_text": generated_text,
                "total_text_chars": transcript_chars + generated_chars,
                "daily_md_exists": (directory / f"daily_{day}.md").exists(),
                "daily_html_exists": (directory / f"daily_{day}.html").exists(),
                "meeting_count": len(meeting_files),
                "latest_update": datetime.fromtimestamp(max(mtimes)).isoformat(timespec="seconds") if mtimes else None,
            }
        )
    return summaries


def _cron_status(config: IntakeConfig) -> dict[str, object]:
    try:
        current = read_current_crontab()
        error = None
    except Exception as exc:
        current = ""
        error = str(exc)
    expected = cron_line(config.repo_root)
    matching = managed_cron_lines(current, config.repo_root)
    legacy = legacy_unmanaged_cron_lines(current, config.repo_root)
    active = bool(matching)
    schedule_time = None
    next_run = None
    target_day = None
    if matching:
        line = matching[0]
        target_day = "previous calendar day" if "date -v-1d" in line else "current calendar day"
        fields = line.split()
        if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
            minute = int(fields[0])
            hour = int(fields[1])
            schedule_time = f"{hour:02d}:{minute:02d}"
            next_run = _next_daily_run(hour, minute)
    return {
        "active": active,
        "error": error,
        "marker": CRON_MARKER,
        "expected_line": expected,
        "matching_lines": matching,
        "legacy_unmanaged_lines": legacy,
        "schedule_time": schedule_time,
        "next_run": next_run,
        "target_day": target_day,
    }


def _sync_preview(config: IntakeConfig, day: str) -> dict[str, object]:
    try:
        plan = build_sync_plan(config.source_dir, config.data_dir, day=day)
        items = [asdict(item) for item in plan]
        counts = {"copy": 0, "convert": 0, "skip": 0}
        for item in plan:
            counts[item.action] = counts.get(item.action, 0) + 1
        return {"ok": True, "error": None, "counts": counts, "items": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "counts": {"copy": 0, "convert": 0, "skip": 0}, "items": []}


def collect_status(config: IntakeConfig | None = None, now: datetime | None = None) -> dict[str, object]:
    config = config or load_config()
    current = now or datetime.now()
    today = current.strftime("%Y%m%d")
    log_path = config.repo_root / "logs" / "intake_cron.log"
    log_tail = _read_tail(log_path)
    processes = _current_processes()
    cron = _cron_status(config)
    days = _day_summaries(config.data_dir)
    totals = {
        "days": len(days),
        "audio_count": sum(_int_value(day.get("audio_count")) for day in days),
        "audio_duration_seconds": round(sum(_float_value(day.get("audio_duration_seconds")) for day in days), 3),
        "transcript_rows": sum(_int_value(cast(dict[str, object], day.get("transcript", {})).get("rows")) for day in days),
        "transcript_chars": sum(_int_value(cast(dict[str, object], day.get("transcript", {})).get("content_chars")) for day in days),
        "generated_text_chars": sum(_int_value(cast(dict[str, object], day.get("generated_text", {})).get("total_chars")) for day in days),
        "total_text_chars": sum(_int_value(day.get("total_text_chars")) for day in days),
    }
    return {
        "generated_at": current.isoformat(timespec="seconds"),
        "today": today,
        "paths": {
            "repo_root": str(config.repo_root),
            "source_dir": str(config.source_dir),
            "data_dir": str(config.data_dir),
            "log_path": str(log_path),
        },
        "source": {
            "exists": config.source_dir.exists(),
            "sync_preview": _sync_preview(config, today),
        },
        "cron": cron,
        "runtime": {
            "running": bool(processes),
            "processes": processes,
        },
        "awake": _awake_status(config),
        "totals": totals,
        "log": {
            "exists": log_path.exists(),
            "latest_update": _mtime_iso(log_path),
            "tail": log_tail,
            "summary": _log_summary(log_tail, bool(processes)),
        },
        "tmp": _tmp_status(config),
        "days": days,
    }


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Intake Skill Dashboard</title>
  <meta name="intake-dashboard-token" content="__DASHBOARD_TOKEN__">
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --line: #d9e1ea;
      --text: #17212f;
      --muted: #627186;
      --ok: #18794e;
      --warn: #a15c00;
      --bad: #b42318;
      --blue: #1d5fd0;
      --shadow: 0 8px 28px rgba(23, 33, 47, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    header {
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1 { margin: 0; font-size: 28px; line-height: 1.2; }
    .subhead { margin-top: 8px; color: var(--muted); font-size: 14px; }
    main { padding: 24px 32px 36px; max-width: 1320px; margin: 0 auto; }
    .grid { display: grid; gap: 16px; }
    .metrics { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
    .columns { grid-template-columns: minmax(0, 1.2fr) minmax(360px, 0.8fr); align-items: start; margin-top: 16px; }
    section, .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .metric { padding: 18px; min-height: 118px; }
    .label { color: var(--muted); font-size: 13px; }
    .value { margin-top: 8px; font-size: 26px; line-height: 1.15; font-weight: 700; }
    .detail { margin-top: 10px; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    section { padding: 18px; }
    h2 { margin: 0 0 14px; font-size: 17px; }
    .status { display: inline-flex; align-items: center; gap: 8px; font-weight: 700; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); flex: 0 0 auto; }
    .ok .dot { background: var(--ok); }
    .warn .dot { background: var(--warn); }
    .bad .dot { background: var(--bad); }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    code { color: #29364a; overflow-wrap: anywhere; }
    pre {
      margin: 0;
      padding: 14px;
      max-height: 360px;
      overflow: auto;
      background: #101828;
      color: #e4e7ec;
      border-radius: 6px;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .stack { display: grid; gap: 16px; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
      background: #f9fbfd;
    }
    .paths { display: grid; gap: 8px; font-size: 13px; color: var(--muted); }
    .path-row { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 8px; }
    .button {
      appearance: none;
      border: 1px solid var(--line);
      background: #ffffff;
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      color: var(--text);
    }
    .button.primary {
      background: var(--blue);
      border-color: var(--blue);
      color: #ffffff;
    }
    .button.danger:hover { border-color: var(--bad); color: var(--bad); }
    .button:hover { border-color: var(--blue); }
    input[type="time"], input[type="text"] {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 8px;
      font: inherit;
    }
    .controls { display: grid; gap: 14px; }
    .control-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .control-label { min-width: 96px; color: var(--muted); font-size: 13px; }
    .message { color: var(--muted); font-size: 13px; min-height: 18px; }
    .section-note { color: var(--muted); font-size: 13px; margin: -6px 0 12px; }
    .mini { color: var(--muted); font-size: 12px; }
    a { color: var(--blue); text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 980px) {
      header { padding: 22px 18px 16px; }
      main { padding: 18px; }
      .metrics, .columns { grid-template-columns: 1fr; }
      .path-row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Intake Skill Dashboard</h1>
    <div class="subhead">Local monitor for Voice Memos sync, cron, ASR output, reports, and logs. <button class="button" id="refresh">Refresh</button></div>
  </header>
  <main>
    <div class="grid metrics">
      <div class="metric"><div class="label">Automation</div><div class="value" id="cronValue">...</div><div class="detail" id="cronDetail"></div></div>
      <div class="metric"><div class="label">Last Run</div><div class="value" id="lastRunValue">...</div><div class="detail" id="lastRunDetail"></div></div>
      <div class="metric"><div class="label">Processed Audio</div><div class="value" id="audioValue">...</div><div class="detail" id="audioDetail"></div></div>
      <div class="metric"><div class="label">Generated Text</div><div class="value" id="textValue">...</div><div class="detail" id="textDetail"></div></div>
      <div class="metric"><div class="label">Today Queue</div><div class="value" id="syncValue">...</div><div class="detail" id="syncDetail"></div></div>
    </div>
    <div class="grid columns">
      <div class="stack">
        <section>
          <h2>Recent Days</h2>
          <div class="section-note">Processed data in <code>data/YYYYMMDD</code>. Reports open only when Codex postprocess has generated HTML.</div>
          <table>
            <thead><tr><th>Day</th><th>Audio</th><th>Duration</th><th>Transcript</th><th>Generated Text</th><th>Reports</th><th>Updated</th></tr></thead>
            <tbody id="daysBody"></tbody>
          </table>
        </section>
        <section>
          <h2>Today Queue</h2>
          <div class="section-note">What the next run would do with today's synced Voice Memos.</div>
          <table>
            <thead><tr><th>Action</th><th>Reason</th><th>Source</th></tr></thead>
            <tbody id="queueBody"></tbody>
          </table>
        </section>
        <section>
          <h2>Paths</h2>
          <div class="paths" id="paths"></div>
        </section>
      </div>
      <div class="stack">
        <section>
          <h2>Controls</h2>
          <div class="controls">
            <div class="control-row">
              <span class="control-label">Run today</span>
              <button class="button primary" id="runNow">Generate now</button>
            </div>
            <div class="control-row">
              <span class="control-label">Schedule</span>
              <input type="time" id="scheduleTime" value="00:00">
              <button class="button" id="setSchedule">Set schedule</button>
              <button class="button danger" id="disableCron">Disable cron</button>
            </div>
            <div class="control-row">
              <span class="control-label">Awake</span>
              <button class="button" id="awakeStart">Keep Mac awake</button>
              <button class="button" id="awakeStop">Stop awake mode</button>
              <span class="pill" id="awakeStatus">...</span>
            </div>
            <div class="control-row">
              <span class="control-label">Cleanup</span>
              <button class="button" id="cleanupTmp">Clean tmp</button>
              <span class="pill" id="tmpStatus">...</span>
            </div>
            <div class="message" id="actionMessage"></div>
          </div>
        </section>
        <section>
          <h2>Operations</h2>
          <div class="paths" id="operations"></div>
        </section>
        <section>
          <h2>Process Snapshot</h2>
          <div id="processes"></div>
        </section>
        <section>
          <h2>Cron Log Tail</h2>
          <pre id="logTail">Loading...</pre>
        </section>
      </div>
    </div>
  </main>
  <script>
    const el = (id) => document.getElementById(id);
    const token = document.querySelector('meta[name="intake-dashboard-token"]').content;
    const fmt = (value) => value || "none";
    const plural = (count, label) => `${count} ${label}${count === 1 ? "" : "s"}`;
    const fmtDuration = (seconds) => {
      if (!seconds) return "0m";
      const total = Math.round(seconds);
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (hours) return `${hours}h ${minutes}m`;
      if (minutes) return `${minutes}m ${secs}s`;
      return `${secs}s`;
    };
    const fmtChars = (chars) => chars >= 10000 ? `${Math.round(chars / 1000)}k chars` : `${chars} chars`;
    const clear = (node) => node.replaceChildren();
    const textNode = (tag, value, className = "") => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = value;
      return node;
    };
    const codeNode = (value) => textNode("code", value);
    const cell = (value, tag = "td") => textNode(tag, value);
    const statusNode = (ok, value) => {
      const node = document.createElement("span");
      node.className = `status ${ok ? "ok" : "bad"}`;
      node.appendChild(textNode("span", "", "dot"));
      node.appendChild(document.createTextNode(value));
      return node;
    };
    const pill = (value) => textNode("span", value, "pill");
    const appendRow = (tbody, cells) => {
      const tr = document.createElement("tr");
      for (const item of cells) tr.appendChild(item);
      tbody.appendChild(tr);
    };
    async function postAction(path, body = {}) {
      const options = {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Intake-Dashboard-Token": token,
        },
        body: JSON.stringify(body),
      };
      const response = await fetch(path, options);
      const payload = await response.json();
      el("actionMessage").textContent = payload.ok ? "Action completed." : `Action failed: ${payload.error}`;
      await loadStatus();
      return payload;
    }
    async function loadStatus() {
      const response = await fetch("/api/status", { cache: "no-store" });
      const data = await response.json();
      el("cronValue").replaceChildren(statusNode(data.cron.active, data.cron.active ? "Installed" : "Not installed"));
      const targetDay = data.cron.target_day ? `, processes ${data.cron.target_day}` : "";
      el("cronDetail").textContent = data.cron.active ? `At ${data.cron.schedule_time || "unknown"}, next ${data.cron.next_run}${targetDay}` : "No automation is installed.";
      if (data.cron.schedule_time) el("scheduleTime").value = data.cron.schedule_time;
      el("lastRunValue").textContent = data.runtime.running ? "Running" : data.log.summary.status;
      el("lastRunDetail").textContent = data.log.summary.last_marker || data.log.latest_update || "No run log yet";
      el("audioValue").textContent = `${data.totals.audio_count}`;
      el("audioDetail").textContent = `${fmtDuration(data.totals.audio_duration_seconds)} across ${plural(data.totals.days, "day")}`;
      el("textValue").textContent = fmtChars(data.totals.total_text_chars);
      el("textDetail").textContent = `${data.totals.transcript_rows} transcript row(s), ${fmtChars(data.totals.generated_text_chars)} reports`;
      const counts = data.source.sync_preview.counts;
      el("syncValue").textContent = data.source.sync_preview.ok ? `${counts.copy + counts.convert} new` : "Blocked";
      el("syncDetail").textContent = data.source.sync_preview.ok ? `copy ${counts.copy}, convert ${counts.convert}, skip ${counts.skip}` : data.source.sync_preview.error;
      const daysBody = el("daysBody");
      clear(daysBody);
      if (data.days.length) {
        for (const day of data.days) {
          const dayCell = document.createElement("td");
          dayCell.appendChild(textNode("strong", day.day));
          const reportCell = document.createElement("td");
          if (day.daily_md_exists) reportCell.appendChild(document.createTextNode("Markdown "));
          if (day.daily_html_exists) {
            const link = document.createElement("a");
            link.href = `/reports/${encodeURIComponent(day.day)}/daily.html`;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "HTML";
            reportCell.appendChild(link);
          }
          appendRow(daysBody, [
            dayCell,
            cell(String(day.audio_count)),
            cell(day.audio_duration_seconds ? fmtDuration(day.audio_duration_seconds) : "unknown"),
            cell(day.transcript.exists ? `${day.transcript.non_empty_rows}/${day.transcript.rows} rows, ${fmtChars(day.transcript.content_chars)}` : "missing"),
            cell(fmtChars(day.generated_text.total_chars)),
            reportCell,
            cell(fmt(day.latest_update)),
          ]);
        }
      } else {
        const empty = cell("No processed data yet.");
        empty.colSpan = 7;
        appendRow(daysBody, [empty]);
      }
      const queueBody = el("queueBody");
      clear(queueBody);
      if (data.source.sync_preview.items.length) {
        for (const item of data.source.sync_preview.items) {
          const actionCell = document.createElement("td");
          actionCell.appendChild(pill(item.action));
          const sourceCell = document.createElement("td");
          sourceCell.appendChild(codeNode(item.source.split("/").pop() || item.source));
          appendRow(queueBody, [actionCell, cell(item.reason), sourceCell]);
        }
      } else {
        const empty = cell("No Voice Memos found for today.");
        empty.colSpan = 3;
        appendRow(queueBody, [empty]);
      }
      const renderPairs = (target, pairs) => {
        clear(target);
        for (const [key, value] of pairs) {
          const row = textNode("div", "", "path-row");
          row.appendChild(textNode("span", key));
          row.appendChild(codeNode(value));
          target.appendChild(row);
        }
      };
      renderPairs(el("paths"), Object.entries(data.paths));
      renderPairs(el("operations"), [
        ["runtime", data.runtime.running ? "running" : "idle"],
        ["last_error", data.log.summary.last_error || "none"],
        ["awake", data.awake.active ? `on, pid ${data.awake.pid}` : "off"],
        ["tmp", `${data.tmp.entries} entries, ${data.tmp.bytes} bytes`],
      ]);
      const processBox = el("processes");
      clear(processBox);
      if (data.runtime.processes.length) {
        for (const proc of data.runtime.processes) {
          processBox.appendChild(pill(`${proc.pid} ${proc.command}`));
          processBox.appendChild(document.createTextNode(" "));
        }
      } else {
        processBox.appendChild(pill("Idle: no intake, Codex postprocess, or MLX process is currently running."));
      }
      el("awakeStatus").textContent = data.awake.active ? `Awake on, pid ${data.awake.pid}` : "Awake off";
      el("tmpStatus").textContent = `${data.tmp.entries} tmp entries`;
      el("logTail").textContent = data.log.tail.length ? data.log.tail.join("\\n") : "No cron log exists yet. It will appear after the first scheduled run.";
    }
    el("refresh").addEventListener("click", loadStatus);
    el("runNow").addEventListener("click", () => postAction("/api/run-now"));
    el("setSchedule").addEventListener("click", () => postAction("/api/cron/schedule", { time: el("scheduleTime").value }));
    el("disableCron").addEventListener("click", () => postAction("/api/cron/disable"));
    el("awakeStart").addEventListener("click", () => postAction("/api/awake/start"));
    el("awakeStop").addEventListener("click", () => postAction("/api/awake/stop"));
    el("cleanupTmp").addEventListener("click", () => postAction("/api/tmp/cleanup"));
    loadStatus();
    setInterval(loadStatus, 15000);
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    dashboard_config: ClassVar[IntakeConfig | None] = None
    dashboard_token: ClassVar[str] = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: str, extra_headers: dict[str, str] | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            html = DASHBOARD_HTML.replace("__DASHBOARD_TOKEN__", self.dashboard_token)
            self._send(200, "text/html; charset=utf-8", html)
            return
        if path == "/api/status":
            self._send(200, "application/json; charset=utf-8", json.dumps(self.get_status(), indent=2, sort_keys=True))
            return
        if path == "/favicon.ico":
            self._send(204, "image/x-icon", "")
            return
        if path.startswith("/reports/") and path.endswith("/daily.html"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3:
                day = _validate_day(parts[1])
                report = self._config().data_dir / day / f"daily_{day}.html"
                if report.exists():
                    self._send(
                        200,
                        "text/html; charset=utf-8",
                        report.read_text(encoding="utf-8", errors="replace"),
                        {
                            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'",
                            "X-Content-Type-Options": "nosniff",
                        },
                    )
                    return
            self._send(404, "text/plain; charset=utf-8", "report not found\n")
            return
        self._send(404, "text/plain; charset=utf-8", "not found\n")

    def _json_payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if not raw:
            return {}
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return dict(payload)
        raise ValueError("request body must be a JSON object")

    def _config(self) -> IntakeConfig:
        return self.dashboard_config or load_config()

    def get_status(self) -> dict[str, object]:
        return collect_status(self.dashboard_config)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host")
        if not host:
            return False
        return origin == f"http://{host}"

    def _authorized_post(self) -> bool:
        if not self._same_origin():
            return False
        token = self.headers.get("X-Intake-Dashboard-Token")
        return bool(token) and secrets.compare_digest(token, self.dashboard_token)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._send(404, "application/json; charset=utf-8", json.dumps({"ok": False, "error": "not found"}))
            return
        if not self._authorized_post():
            self._send(403, "application/json; charset=utf-8", json.dumps({"ok": False, "error": "forbidden"}))
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type and "application/json" not in content_type:
            self._send(415, "application/json; charset=utf-8", json.dumps({"ok": False, "error": "application/json required"}))
            return
        config = self._config()
        try:
            if path == "/api/run-now":
                payload = run_now(config)
            elif path == "/api/awake/start":
                payload = start_awake(config)
            elif path == "/api/awake/stop":
                payload = stop_awake(config)
            elif path == "/api/cron/schedule":
                form = self._json_payload()
                schedule_time = str(form.get("time", "00:00"))
                payload = {"ok": True, **replace_managed_cron(config.repo_root, schedule_time=schedule_time)}
            elif path == "/api/cron/disable":
                payload = {"ok": True, **remove_managed_cron(config.repo_root)}
            elif path == "/api/tmp/cleanup":
                payload = cleanup_tmp(config)
            else:
                self._send(404, "application/json; charset=utf-8", json.dumps({"ok": False, "error": "not found"}))
                return
        except Exception as exc:
            self._send(400, "application/json; charset=utf-8", json.dumps({"ok": False, "error": str(exc)}))
            return
        self._send(200, "application/json; charset=utf-8", json.dumps(payload, indent=2, sort_keys=True))

    def do_OPTIONS(self) -> None:
        self._send(403, "application/json; charset=utf-8", json.dumps({"ok": False, "error": "forbidden"}))


def serve_dashboard(host: str = "127.0.0.1", port: int = 8765, config: IntakeConfig | None = None) -> DashboardServer:
    status_provider = (lambda: collect_status(config)) if config else collect_status

    class Handler(DashboardHandler):
        dashboard_config = config
        dashboard_token = secrets.token_urlsafe(32)

        def get_status(self) -> dict[str, object]:
            return status_provider()
    server = ThreadingHTTPServer((host, port), Handler)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}"
    print(f"Intake Skill Dashboard: {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return DashboardServer(host=str(actual_host), port=int(actual_port), url=url)
