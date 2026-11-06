import subprocess

from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import record
from apps.core import backups


class Command(BaseCommand):
    help = (
        "Back up the database to the private bucket (2 daily + 1 weekly kept). Run nightly by Railway cron."
    )

    def handle(self, *args, **options):
        try:
            result = backups.run_backup()
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
            raise CommandError(f"pg_dump failed: {message}") from exc
        record(
            None, "system.backup", None, file=result["name"], size=result["size"], removed=result["removed"]
        )
        self.stdout.write(
            self.style.SUCCESS(f"Backed up {result['size']:,} bytes to {', '.join(result['saved'])}.")
            + (f" Removed old backups: {', '.join(result['removed'])}." if result["removed"] else "")
        )
