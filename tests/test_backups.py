"""Nightly database backups: retention of 2 daily and 1 weekly copy, and restore safeguards."""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.audit.models import AuditEvent
from apps.core import backups

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_backup_folders():
    # The in-memory test storage lives for the whole test run, so start each test clean.
    from django.core.files.storage import default_storage

    for kind, names in backups.list_backups().items():
        for name in names:
            default_storage.delete(f"{backups.PREFIX}/{kind}/{name}")


@pytest.fixture(autouse=True)
def fake_pg_dump(monkeypatch):
    def dump_to(path):
        with open(path, "wb") as f:
            f.write(b"PGDMP fake dump")

    monkeypatch.setattr(backups, "dump_to", dump_to)


def night(day, hour=2):
    return datetime(2026, 11, day, hour, 0, tzinfo=UTC)


def test_keeps_two_dailies_and_one_weekly():
    for day in range(1, 12):  # 11 nights
        backups.run_backup(now=night(day))
    listed = backups.list_backups()
    assert listed["daily"] == ["20261110T0200Z.dump", "20261111T0200Z.dump"]
    # weekly copies were made on the 1st and 8th; only the newest is kept
    assert listed["weekly"] == ["20261108T0200Z.dump"]


def test_weekly_copy_only_when_due():
    first = backups.run_backup(now=night(1))
    second = backups.run_backup(now=night(2))
    assert len(first["saved"]) == 2 and len(second["saved"]) == 1
    assert backups.run_backup(now=night(1) + timedelta(days=7))["saved"][-1].startswith("backups/db/weekly/")


def test_command_records_an_audit_event(capsys):
    call_command("backup_database")
    assert "Backed up" in capsys.readouterr().out
    event = AuditEvent.objects.get(action="system.backup")
    assert event.actor is None and event.data["file"].endswith(".dump")


def test_restore_needs_confirmation_and_a_backup_key():
    backups.run_backup(now=night(1))
    with pytest.raises(CommandError, match="--yes-replace-the-database"):
        call_command("restore_database", "backups/db/daily/20261101T0200Z.dump")
    with pytest.raises(CommandError, match="Not a database backup key"):
        call_command("restore_database", "evidence/abc", "--yes-replace-the-database")


def test_listing_backups(capsys):
    backups.run_backup(now=night(1))
    call_command("restore_database")
    out = capsys.readouterr().out
    assert "backups/db/daily/20261101T0200Z.dump" in out and "backups/db/weekly/20261101T0200Z.dump" in out
