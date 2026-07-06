from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
SCHEMA_VERSION_TABLE = "schema_version"


class DatabaseMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    applied: list[str]
    skipped: list[str]


def run_migrations(connection: sqlite3.Connection) -> MigrationResult:
    """Run idempotent SQLite migrations from backend/app/db/migrations.

    Startup must fail if any migration fails. This avoids booting against a
    partially upgraded schema after the background worker has started writing.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied_rows = connection.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE}").fetchall()
    applied_versions = {str(row["version"] if isinstance(row, sqlite3.Row) else row[0]) for row in applied_rows}
    applied: list[str] = []
    skipped: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied_versions:
            skipped.append(version)
            continue
        script = path.read_text(encoding="utf-8")
        try:
            connection.executescript(
                "\n".join(
                    [
                        "BEGIN IMMEDIATE;",
                        script,
                        f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES ('{version}');",
                        "COMMIT;",
                    ]
                )
            )
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise DatabaseMigrationError(f"Failed to apply database migration {version}: {exc}") from exc
        else:
            applied.append(version)
    return MigrationResult(applied=applied, skipped=skipped)
