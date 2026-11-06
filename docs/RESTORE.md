# Restoring the database

The pilot is on Railway's Hobby plan, which can't create scheduled backups. Instead, a Railway
cron service runs `python manage.py backup_database` every night. It writes a `pg_dump` to the
environment's private bucket:

- `backups/db/daily/` keeps the **2** newest nightly dumps.
- `backups/db/weekly/` keeps **1** dump, refreshed when the last one is over 7 days old.

Each backup and restore is recorded in the audit log ("System" filter). Retention was agreed on
6 Nov 2026 for the trial and should be reviewed afterwards.

**Not covered:** evidence files. They live in the bucket under `evidence/`, are never overwritten,
and deleted files are kept, but losing the bucket itself would lose them.

## Before you restore

- A restore **replaces all data** in that environment's database with the backup, including the
  audit log, which goes back to the backup's point in time. The restore itself is then recorded.
- Anything done after the backup is lost. Tell staff and shoppers to stop working first.
- Try it on **dev** first if you can.
- Make a fresh backup first (step 2), so the restore can itself be undone.

## Steps

Run these from the repo, logged in with the Railway CLI. Use the web service's ID: production is
`3da32486-d0f7-4f78-81e9-67be9f61e155`, dev is `6b88ec93-1903-4480-818f-83419ec8b8bf`.

1. List the backups:

   ```sh
   railway ssh --service <service-id> --environment production -- python manage.py restore_database
   ```

2. Take a backup of the current state, as a safety net:

   ```sh
   railway ssh --service <service-id> --environment production -- python manage.py backup_database
   ```

3. Restore the chosen backup (copy its key from step 1):

   ```sh
   railway ssh --service <service-id> --environment production -- \
     python manage.py restore_database backups/db/daily/20261106T0200Z.dump --yes-replace-the-database
   ```

4. Check the site, and the audit log's System filter, which should show "Database restored from …".

If `pg_restore` reports an error, the restore runs in a single transaction, so the database is left
as it was.
