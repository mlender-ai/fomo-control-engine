# Operations

## SQLite Backup

The backend creates an online SQLite backup at 04:30 in `FCE_DB_MAINTENANCE_TIMEZONE` when `FCE_DB_BACKUP_ENABLED=true`.
The default maintenance timezone is `Asia/Seoul`.

Backup files are written as:

```text
backups/fce_YYYYMMDD.db.gz
```

Each backup is smoke-tested by decompressing it to a temporary database and reading table counts. The result is recorded in `GET /api/system/database`.

## Restore Procedure

1. Stop the backend process.
2. Pick the backup file to restore.
3. Decompress it to a temporary DB.

```bash
gunzip -c backups/fce_YYYYMMDD.db.gz > /tmp/fce_restore.db
sqlite3 /tmp/fce_restore.db ".tables"
sqlite3 /tmp/fce_restore.db "select count(*) from positions;"
```

4. Replace the active DB only after the smoke check succeeds.

```bash
cp /tmp/fce_restore.db backend/fomo_control_engine.db
```

5. Start the backend and verify:

```bash
curl -s http://127.0.0.1:8875/health
curl -s http://127.0.0.1:8875/api/system/database
```

Do not restore while the backend is running. Judgment ledger, reviews, trades, and calibration data are considered permanent records.
