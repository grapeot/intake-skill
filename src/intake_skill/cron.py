from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


CRON_MARKER = "# intake_skill nightly run"
LEGACY_CRON_MARKERS = ("# intake_skill midnight run",)
CRON_PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PREVIOUS_DAY_CUTOFF_HOUR = 12
PREVIOUS_DAY_DATE_ARG = "$(date -v-1d +\\%Y\\%m\\%d)"


def validate_schedule_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("schedule time must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("schedule time must be HH:MM") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("schedule time must be within 00:00 and 23:59")
    return hour, minute


def cron_target_day(hour: int) -> str:
    if hour < PREVIOUS_DAY_CUTOFF_HOUR:
        return "previous calendar day"
    return "current calendar day"


def cron_line(repo_root: Path, hour: int = 0, minute: int = 0) -> str:
    python_path = repo_root / ".venv" / "bin" / "python"
    log_path = repo_root / "logs" / "intake_cron.log"
    date_arg = f"--date {PREVIOUS_DAY_DATE_ARG} " if cron_target_day(hour).startswith("previous") else ""
    return (
        f"{minute} {hour} * * * "
        f"PATH={shlex.quote(CRON_PATH)}; "
        f"cd {shlex.quote(str(repo_root))} && "
        f"{shlex.quote(str(python_path))} -m intake_skill run-day "
        f"{date_arg}"
        "--asr-engine mlx --postprocess-engine codex "
        f">> {shlex.quote(str(log_path))} 2>&1 "
        f"{CRON_MARKER}"
    )


def backup_path(repo_root: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = repo_root / "logs" / f"crontab_backup_{stamp}.txt"
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = repo_root / "logs" / f"crontab_backup_{stamp}_{index}.txt"
        if not candidate.exists():
            return candidate
        index += 1


def read_current_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0:
        return result.stdout
    if "no crontab" in result.stderr.lower():
        return ""
    raise RuntimeError(result.stderr.strip() or "failed to read current crontab")


def managed_cron_lines(current: str, repo_root: Path | None = None) -> list[str]:
    lines: list[str] = []
    markers = (CRON_MARKER, *LEGACY_CRON_MARKERS)
    for line in current.splitlines():
        if not any(marker in line for marker in markers):
            continue
        if repo_root is not None:
            root = str(repo_root)
            expected_python = str(repo_root / ".venv" / "bin" / "python")
            if root not in line or expected_python not in line or "-m intake_skill run-day" not in line:
                continue
        lines.append(line)
    return lines


def legacy_unmanaged_cron_lines(current: str, repo_root: Path) -> list[str]:
    managed = set(managed_cron_lines(current, repo_root))
    return [
        line
        for line in current.splitlines()
        if "intake_skill run-day" in line and line not in managed
    ]


def replace_managed_cron(repo_root: Path, schedule_time: str = "00:00", dry_run: bool = False, current: str | None = None) -> dict[str, object]:
    hour, minute = validate_schedule_time(schedule_time)
    line = cron_line(repo_root, hour=hour, minute=minute)
    existing = read_current_crontab() if current is None and not dry_run else (current or "")
    preserved = [existing_line for existing_line in existing.splitlines() if existing_line not in managed_cron_lines(existing, repo_root)]
    new_crontab = "\n".join([*preserved, line]).strip("\n") + "\n"
    path = backup_path(repo_root)
    summary: dict[str, object] = {
        "command": "install-cron",
        "dry_run": dry_run,
        "already_present": line in existing.splitlines(),
        "schedule_time": f"{hour:02d}:{minute:02d}",
        "target_day": cron_target_day(hour),
        "cron_line": line,
        "backup_path": str(path),
        "schedule_label": f"Every day at {hour:02d}:{minute:02d}",
        "user_note": f"This sets up Intake Skill to run automatically when the Mac is awake and process the {cron_target_day(hour)}.",
    }
    if dry_run:
        summary["new_crontab"] = new_crontab
        return summary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing, encoding="utf-8")
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    return summary


def append_cron(repo_root: Path, dry_run: bool = False, current: str | None = None) -> dict[str, object]:
    return replace_managed_cron(repo_root, schedule_time="00:00", dry_run=dry_run, current=current)


def remove_managed_cron(repo_root: Path, dry_run: bool = False, current: str | None = None) -> dict[str, object]:
    existing = read_current_crontab() if current is None and not dry_run else (current or "")
    removed = managed_cron_lines(existing, repo_root)
    preserved = [line for line in existing.splitlines() if line not in removed]
    new_crontab = "\n".join(preserved).strip("\n")
    if new_crontab:
        new_crontab += "\n"
    path = backup_path(repo_root)
    summary: dict[str, object] = {
        "command": "remove-cron",
        "dry_run": dry_run,
        "removed": removed,
        "backup_path": str(path),
    }
    if dry_run:
        summary["new_crontab"] = new_crontab
        return summary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing, encoding="utf-8")
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    return summary


def dumps_summary(summary: dict[str, object]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)
