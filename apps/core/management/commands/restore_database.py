import subprocess

from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import record
from apps.core import backups


class Command(BaseCommand):
    help = (
        "List database backups, or restore one over the CURRENT database. "
        "See docs/RESTORE.md before using this."
    )

    def add_arguments(self, parser):
        parser.add_argument("key", nargs="?", help="Backup key, e.g. backups/db/daily/20261106T0200Z.dump")
        parser.add_argument(
            "--yes-replace-the-database",
            action="store_true",
            help="Required to restore: confirms you mean to replace the current data.",
        )

    def handle(self, *args, key=None, yes_replace_the_database=False, **options):
        if not key:
            for kind, names in backups.list_backups().items():
                self.stdout.write(f"{kind}:")
                for name in names:
                    self.stdout.write(f"  {backups.PREFIX}/{kind}/{name}")
            return
        if not yes_replace_the_database:
            raise CommandError("This replaces the current database. Re-run with --yes-replace-the-database.")
        try:
            backups.restore(key)
        except (ValueError, FileNotFoundError) as exc:
            raise CommandError(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
            raise CommandError(f"pg_restore failed: {message}") from exc
        record(None, "system.restore", None, file=key)
        self.stdout.write(self.style.SUCCESS(f"Restored {key}."))
