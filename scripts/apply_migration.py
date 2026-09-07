"""Apply one migration file to the project's Supabase Postgres.

    uv run python scripts/apply_migration.py supabase/migrations/0004_*.sql

Forward-only, one file per invocation, wrapped in a single transaction so a
half-applied migration is not a state the database can be left in. There is no
down path and no migration ledger: files are numbered, applied in order, and
the schema itself is the record.

The Supabase MCP server configured for this project is read-only, and the SQL
editor cannot be scripted. This is the write path.
"""

from __future__ import annotations

import pathlib
import sys


def dsn() -> str:
    """Read SUPABASE_DB_URL out of .env without importing the app config.

    The password is percent-encoded in the DSN; it is never printed, and no
    part of this file logs the connection string.
    """
    env = pathlib.Path(".env")
    if not env.exists():
        raise SystemExit("FAIL  .env not found")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("SUPABASE_DB_URL="):
            value = line.partition("=")[2].split("  #")[0].strip()
            # Strip surrounding quotes. The password is percent-encoded, so the
            # DSN is usually quoted in .env to keep `#` from reading as a
            # comment; psycopg wants the bare string.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    raise SystemExit("FAIL  SUPABASE_DB_URL is not set in .env")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <path/to/migration.sql>")

    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        raise SystemExit(f"FAIL  {path} not found")

    import psycopg

    sql = path.read_text(encoding="utf-8")
    try:
        with psycopg.connect(dsn()) as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql)
    except psycopg.OperationalError as exc:
        # psycopg puts the whole DSN in the message when it cannot parse or
        # connect, and the DSN carries the database password. Never surface it.
        raise SystemExit(f"FAIL  could not connect: {type(exc).__name__}") from None
    print(f"  OK  applied {path.name} ({len(sql.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
