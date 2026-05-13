from __future__ import annotations

import importlib
from datetime import datetime


cron = importlib.import_module("intake_skill.cron")


def test_cron_dry_run_appends_midnight_line(tmp_path) -> None:
    summary = cron.append_cron(tmp_path, dry_run=True, current="MAILTO=me@example.com\n")
    assert summary["dry_run"] is True
    assert "0 0 * * *" in summary["cron_line"]
    assert "--date $(date -v-1d +\\%Y\\%m\\%d)" in summary["cron_line"]
    assert "--asr-engine mlx --postprocess-engine codex" in summary["cron_line"]
    assert summary["schedule_label"] == "Every day at 00:00"
    assert summary["target_day"] == "previous calendar day"
    assert cron.CRON_MARKER in summary["new_crontab"]
    assert "MAILTO=me@example.com" in summary["new_crontab"]


def test_cron_dry_run_does_not_duplicate_marker(tmp_path) -> None:
    line = cron.cron_line(tmp_path)
    summary = cron.append_cron(tmp_path, dry_run=True, current=f"{line}\n")
    assert summary["already_present"] is True
    assert summary["new_crontab"].count(cron.CRON_MARKER) == 1


def test_replace_managed_cron_updates_time_and_preserves_other_lines(tmp_path) -> None:
    old_line = cron.cron_line(tmp_path)
    current = f"MAILTO=me@example.com\n{old_line}\n"
    summary = cron.replace_managed_cron(tmp_path, schedule_time="07:30", dry_run=True, current=current)

    assert summary["schedule_time"] == "07:30"
    assert "30 7 * * *" in summary["cron_line"]
    assert summary["target_day"] == "previous calendar day"
    assert "MAILTO=me@example.com" in summary["new_crontab"]
    assert old_line not in summary["new_crontab"]


def test_afternoon_cron_processes_current_calendar_day(tmp_path) -> None:
    summary = cron.replace_managed_cron(tmp_path, schedule_time="16:30", dry_run=True, current="")

    assert summary["target_day"] == "current calendar day"
    assert "--date $(date -v-1d +\\%Y\\%m\\%d)" not in summary["cron_line"]


def test_remove_managed_cron_preserves_unrelated_crontab(tmp_path) -> None:
    old_line = cron.cron_line(tmp_path)
    current = f"MAILTO=me@example.com\n{old_line}\n15 9 * * * echo hello\n"
    summary = cron.remove_managed_cron(tmp_path, dry_run=True, current=current)

    assert summary["removed"] == [old_line]
    assert "MAILTO=me@example.com" in summary["new_crontab"]
    assert "echo hello" in summary["new_crontab"]
    assert cron.CRON_MARKER not in summary["new_crontab"]


def test_cron_management_preserves_unmarked_run_day_lines(tmp_path) -> None:
    legacy = "5 8 * * * cd /somewhere && python -m intake_skill run-day"
    current = f"{legacy}\n{cron.cron_line(tmp_path)}\n"

    summary = cron.remove_managed_cron(tmp_path, dry_run=True, current=current)

    assert summary["removed"] == [cron.cron_line(tmp_path)]
    assert legacy in summary["new_crontab"]


def test_cron_management_replaces_legacy_marked_line(tmp_path) -> None:
    legacy_line = cron.cron_line(tmp_path).replace(cron.CRON_MARKER, cron.LEGACY_CRON_MARKERS[0])
    summary = cron.replace_managed_cron(tmp_path, schedule_time="16:30", dry_run=True, current=f"{legacy_line}\n")

    assert legacy_line not in summary["new_crontab"]
    assert cron.CRON_MARKER in summary["new_crontab"]
    assert "30 16 * * *" in summary["new_crontab"]


def test_cron_management_preserves_other_repo_marked_lines(tmp_path) -> None:
    other_repo = tmp_path / "other"
    other_line = cron.cron_line(other_repo)
    current = f"{other_line}\n{cron.cron_line(tmp_path)}\n"

    summary = cron.replace_managed_cron(tmp_path, schedule_time="16:30", dry_run=True, current=current)

    assert other_line in summary["new_crontab"]
    assert cron.cron_line(tmp_path) not in summary["new_crontab"]
    assert "30 16 * * *" in summary["new_crontab"]


def test_schedule_time_validation() -> None:
    assert cron.validate_schedule_time("23:59") == (23, 59)
    try:
        cron.validate_schedule_time("24:00")
    except ValueError as exc:
        assert "00:00 and 23:59" in str(exc)
    else:
        raise AssertionError("invalid schedule time should fail")


def test_backup_path_never_overwrites(tmp_path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    now = datetime(2026, 5, 12, 1, 2, 3)
    first = cron.backup_path(tmp_path, now=now)
    first.write_text("old", encoding="utf-8")
    second = cron.backup_path(tmp_path, now=now)
    assert first != second
    assert not second.exists()
