"""Explicit offline seed commands; never imports the existing application."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .generator import REFERENCE_DATE, SEED, fingerprint, generate
from .schema import APPLICATION_ID, TABLES, VERSION, render_schema


def populate_sqlite(connection, data):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(render_schema("sqlite"))
    with connection:
        for table in TABLES:
            fields = ", ".join(c.name for c in table.columns)
            marks = ", ".join("?" for _ in table.columns)
            connection.executemany(f"INSERT INTO {table.name} ({fields}) VALUES ({marks})", data[table.name])
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={VERSION}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Synthetic foreign-key validation failed")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Synthetic database integrity validation failed")


def seed_sqlite(path):
    """Publish a fully validated DB atomically without replacing any existing file."""
    path = Path(path)
    if os.path.lexists(path):
        raise FileExistsError(f"Refusing existing target: {path}; choose a new synthetic path")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = generate()
    descriptor, temporary = tempfile.mkstemp(prefix=".dbmind-seed-", dir=path.parent)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            populate_sqlite(connection, data)
        os.link(temporary, path)  # Exclusive publication, including a concurrent creator.
    finally:
        os.unlink(temporary)
    return data


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "N'" + value.replace("'", "''") + "'"


def sqlserver_bundle(database):
    """A transactional script for an explicit, dedicated synthetic target only."""
    if not re.fullmatch(r"dbmind_synthetic_[A-Za-z0-9_]+", database):
        raise ValueError("Target name must match dbmind_synthetic_[A-Za-z0-9_]+")
    data = generate()
    lines = [f"-- Fictional records only. Schema v{VERSION}; seed {SEED}; reference {REFERENCE_DATE}",
             f"-- Dataset SHA256: {fingerprint(data)}", "SET NOCOUNT ON;", "SET XACT_ABORT ON;",
             f"IF DB_NAME() <> N'{database}' THROW 51000, 'Wrong synthetic database target', 1;",
             "BEGIN TRY", "BEGIN TRANSACTION;",
             "DECLARE @lock_result int;",
             "EXEC @lock_result = sys.sp_getapplock @Resource=N'dbmind_synthetic_seed_v1', @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=0;",
             "IF @lock_result < 0 THROW 51001, 'Another seed is running', 1;",
             "IF SCHEMA_ID(N'demo') IS NOT NULL THROW 51002, 'Demo schema exists; refusing seed rerun', 1;",
             "IF EXISTS (SELECT 1 FROM sys.tables WHERE is_ms_shipped=0) THROW 51003, 'Target is not empty; refusing seed', 1;",
             "EXEC(N'CREATE SCHEMA [demo]');"]
    # CREATE SCHEMA must be executed in its own batch.
    ddl = render_schema("sqlserver").replace("CREATE SCHEMA [demo];\n", "")
    lines.append(ddl)
    for table in TABLES:
        fields = ", ".join(c.name for c in table.columns)
        for row in data[table.name]:
            lines.append(f"INSERT INTO [demo].[{table.name}] ({fields}) VALUES ("
                         + ", ".join(sql_literal(v) for v in row) + ");")
        lines.append(f"IF (SELECT COUNT_BIG(*) FROM [demo].[{table.name}]) <> {len(data[table.name])} THROW 51004, 'Seed row count mismatch', 1;")
    lines.extend(["COMMIT TRANSACTION;", "END TRY", "BEGIN CATCH",
                  "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;", "THROW;", "END CATCH;"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sqlite = commands.add_parser("sqlite", help="Create a new SQLite file; refuse any existing path")
    sqlite.add_argument("--output", type=Path, required=True)
    sqlserver = commands.add_parser("sqlserver-script", help="Write SQL only; never connect")
    sqlserver.add_argument("--database", required=True)
    sqlserver.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "sqlite":
            data = seed_sqlite(args.output)
            print(json.dumps({"schema_version": VERSION, "seed": SEED,
                              "reference_date_utc": REFERENCE_DATE + "Z", "sha256": fingerprint(data),
                              "rows": {name: len(rows) for name, rows in data.items()}}, indent=2))
        else:
            script = sqlserver_bundle(args.database)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(script)
            print(f"Wrote {args.output}; no database connection made")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Seed refused/failed: {error}\n")


if __name__ == "__main__":
    main()
