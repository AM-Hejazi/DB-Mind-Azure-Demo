"""Read-only reference check CLI; never calls a model or connects remotely."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .schema import APPLICATION_ID, TABLES, VERSION
from .generator import fingerprint

REFERENCE_PATH = Path(__file__).resolve().parent.parent / "data/synthetic/v1/reference-cases.json"


def check_database(path, cases_path=REFERENCE_PATH):
    reference = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Not a DB-Mind synthetic database")
        if connection.execute("PRAGMA user_version").fetchone()[0] != VERSION:
            raise ValueError("Unsupported synthetic schema version")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Foreign-key violations")
        counts = {t.name: connection.execute(f"SELECT COUNT(*) FROM {t.name}").fetchone()[0] for t in TABLES}
        if counts != reference["row_counts"]:
            raise ValueError(f"Row counts differ: {counts}")
        data = {t.name: connection.execute(f"SELECT * FROM {t.name} ORDER BY 1, 2").fetchall() for t in TABLES}
        if fingerprint(data) != reference["dataset_sha256"]:
            raise ValueError("Dataset differs from the fixed reference seed")
        checked = 0
        for case in reference["cases"]:
            if case["sql_sqlite"] is None:
                continue
            cursor = connection.execute(case["sql_sqlite"])
            columns = [column[0] for column in cursor.description]
            rows = [list(row) for row in cursor.fetchall()]
            if columns != case["expected_columns"] or rows != case["expected_rows"]:
                raise ValueError(f"Reference result differs: {case['id']}")
            checked += 1
    return {"reference_queries_passed": checked, "row_counts": counts,
            "note": "SQLite only; clarification/rejection are specified, not UI-validated"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check_database(args.database), indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Reference check failed: {error}\n")


if __name__ == "__main__":
    main()
