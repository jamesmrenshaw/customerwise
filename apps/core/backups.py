"""Nightly database backups to the private bucket (pilot; Railway Hobby has no scheduled backups).

Each run writes a pg_dump (custom format) to backups/db/daily/, keeps the newest DAILY_KEEP, and
when the newest weekly copy is older than 7 days also writes one to backups/db/weekly/, keeping
WEEKLY_KEEP. Retention agreed 6 Nov 2026 for the trial; review afterwards.

Evidence files are in the same bucket under evidence/ and are not part of these backups.
"""

import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.utils import timezone

PREFIX = "backups/db"
DAILY_KEEP = 2
WEEKLY_KEEP = 1
WEEKLY_EVERY = timedelta(days=7)


def _pg_env():
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    env.update(
        PGHOST=str(db.get("HOST") or "localhost"),
        PGPORT=str(db.get("PORT") or "5432"),
        PGUSER=str(db.get("USER") or ""),
        PGPASSWORD=str(db.get("PASSWORD") or ""),
        PGDATABASE=str(db["NAME"]),
    )
    return env


def _list(kind):
    """Backup names in a folder, oldest first (names start with a sortable UTC timestamp)."""
    try:
        _, files = default_storage.listdir(f"{PREFIX}/{kind}")
    except FileNotFoundError:
        return []
    return sorted(f for f in files if f.endswith(".dump"))


def _stamp(name):
    return datetime.strptime(name.removesuffix(".dump"), "%Y%m%dT%H%MZ").replace(tzinfo=UTC)


def list_backups():
    return {kind: _list(kind) for kind in ("daily", "weekly")}


def _prune(kind, keep):
    removed = []
    names = _list(kind)
    for name in names[: max(0, len(names) - keep)]:
        default_storage.delete(f"{PREFIX}/{kind}/{name}")
        removed.append(name)
    return removed


def _tool(name):
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} is not installed (it's in the Docker image).")
    return path


# The commands below have fixed arguments plus our own temp-file paths and database settings,
# never user input, so the subprocess warnings don't apply.


def dump_to(path):
    subprocess.run(  # noqa: S603
        [_tool("pg_dump"), "--format=custom", "--no-owner", "--no-acl", f"--file={path}"],
        env=_pg_env(),
        check=True,
        capture_output=True,
    )


def run_backup(now=None):
    """Make today's backup, a weekly copy when due, and prune. Returns a summary dict."""
    now = now or timezone.now()
    name = now.astimezone(UTC).strftime("%Y%m%dT%H%MZ") + ".dump"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        dump_to(path)
        size = os.path.getsize(path)
        saved = []
        weeklies = _list("weekly")
        weekly_due = not weeklies or now - _stamp(weeklies[-1]) >= WEEKLY_EVERY
        for kind in ["daily"] + (["weekly"] if weekly_due else []):
            with open(path, "rb") as f:
                saved.append(default_storage.save(f"{PREFIX}/{kind}/{name}", File(f)))
    removed = _prune("daily", DAILY_KEEP) + _prune("weekly", WEEKLY_KEEP)
    return {"name": name, "size": size, "saved": saved, "removed": removed}


def restore(key):
    """Restore a backup over the current database (drops and recreates the dumped objects)."""
    if not key.startswith(f"{PREFIX}/") or ".." in key:
        raise ValueError("Not a database backup key.")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore.dump")
        with default_storage.open(key, "rb") as src, open(path, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
        subprocess.run(  # noqa: S603
            [
                _tool("pg_restore"),
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-acl",
                "--single-transaction",
                f"--dbname={_pg_env()['PGDATABASE']}",
                path,
            ],
            env=_pg_env(),
            check=True,
            capture_output=True,
        )
