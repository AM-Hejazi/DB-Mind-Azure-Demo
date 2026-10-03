"""Offline tests for relational fixtures and non-destructive seeding."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from synthetic_demo.check import REFERENCE_PATH, check_database
from synthetic_demo.generator import REFERENCE_DATE, SEED, fingerprint, generate
from synthetic_demo.questions import reference_cases
from synthetic_demo.schema import TABLES, render_schema
from synthetic_demo.seed import populate_sqlite, seed_sqlite, sqlserver_bundle

ROOT = Path(__file__).resolve().parent.parent


class SyntheticDemoTests(unittest.TestCase):
    def test_imports_have_no_connections_or_file_writes(self):
        code = """
from unittest.mock import patch
import sqlite3, socket
with patch.object(sqlite3, 'connect', side_effect=AssertionError('database import side effect')), \
     patch.object(socket, 'socket', side_effect=AssertionError('network import side effect')), \
     patch('pathlib.Path.mkdir', side_effect=AssertionError('filesystem import side effect')):
    import synthetic_demo.seed, synthetic_demo.check, synthetic_demo.questions
"""
        subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, check=True, capture_output=True)

    def test_fixed_seed_and_versioned_artifacts(self):
        reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        data = generate()
        self.assertEqual(fingerprint(data), reference["dataset_sha256"])
        self.assertEqual(fingerprint(generate()), fingerprint(data))
        self.assertEqual((SEED, REFERENCE_DATE + "Z"), (reference["seed"], reference["reference_date_utc"]))
        self.assertEqual(reference["row_counts"], {k: len(v) for k, v in data.items()})
        for case, saved in zip(reference_cases(), reference["cases"], strict=True):
            for key, value in case.items():
                self.assertEqual(value, saved[key])
        self.assertEqual((ROOT / "data/synthetic/v1/schema.sqlite.sql").read_text(), render_schema("sqlite"))
        self.assertEqual((ROOT / "data/synthetic/v1/schema.tsql.sql").read_text(),
                         render_schema("sqlserver").replace("CREATE SCHEMA [demo];", "EXEC(N'CREATE SCHEMA [demo]');"))

    def test_reference_results_and_independent_aggregates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            data = seed_sqlite(path)
            self.assertEqual(check_database(path)["reference_queries_passed"], 11)
            with closing(sqlite3.connect(path)) as db:
                # Separate generator-side calculations check join and duration semantics.
                expected_cost = sum(row[2] * row[3] for row in data["work_order_parts"])
                self.assertEqual(db.execute("SELECT SUM(quantity*unit_cost_eur_cents) FROM work_order_parts").fetchone()[0], expected_cost)
                resolved = [row for row in data["failure_events"] if row[6] is not None]
                expected_repair = sum(row[6] for row in resolved) / len(resolved)
                self.assertAlmostEqual(db.execute("SELECT AVG(repair_minutes) FROM failure_events").fetchone()[0], expected_repair)
                self.assertTrue(all(row[5] >= row[6] for row in resolved))
                self.assertGreater(len(resolved), 0)
                self.assertGreater(len(data["failure_events"]) - len(resolved), 0)
                self.assertGreater(db.execute("SELECT COUNT(*) FROM work_orders WHERE technician_id IS NULL").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM failure_events WHERE equipment_id>56").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM safety_incidents").fetchone()[0], 0)
                self.assertIn("Fictional Möbius", [r[1] for r in data["sites"]])
                self.assertEqual(len({r[5] for r in data["work_orders"]}), 4)

    def test_seed_refuses_existing_empty_populated_and_symlink_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / "existing.sqlite3"
            existing.write_bytes(b"")
            with self.assertRaises(FileExistsError):
                seed_sqlite(existing)
            self.assertEqual(existing.read_bytes(), b"")
            existing.unlink()
            seed_sqlite(existing)
            original = existing.read_bytes()
            with self.assertRaises(FileExistsError):
                seed_sqlite(existing)
            self.assertEqual(existing.read_bytes(), original)
            link = Path(folder) / "link.sqlite3"
            link.symlink_to(existing)
            with self.assertRaises(FileExistsError):
                seed_sqlite(link)
            self.assertEqual(existing.read_bytes(), original)

    def test_constraints_reject_orphans_wrong_equipment_and_invalid_values(self):
        with closing(sqlite3.connect(":memory:")) as db:
            data = generate()
            populate_sqlite(db, data)
            order = next(row for row in data["work_orders"] if row[1] != 60)
            mutations = [
                "INSERT INTO equipment VALUES (999,999,'SYN-BAD','pump','2026-01-01T00:00:00','active')",
                f"INSERT INTO failure_events VALUES (9999,60,{order[0]},'2026-01-01T00:00:00','sensor',10,5)",
                "UPDATE work_orders SET completed_at=NULL WHERE status='completed'",
                "UPDATE work_order_parts SET quantity=0",
                "UPDATE failure_events SET downtime_minutes=0 WHERE repair_minutes>0",
                "UPDATE failure_events SET repair_minutes=NULL WHERE downtime_minutes IS NOT NULL",
            ]
            for query in mutations:
                with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(query)
                db.rollback()
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_seed_failure_and_publication_race_leave_existing_files_intact(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            with patch("synthetic_demo.seed.populate_sqlite", side_effect=sqlite3.Error("interrupted")):
                with self.assertRaises(sqlite3.Error):
                    seed_sqlite(path)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])
            real_link = os.link

            def concurrent_creator(source, destination):
                Path(destination).write_bytes(b"owner-created-file")
                real_link(source, destination)

            with patch("synthetic_demo.seed.os.link", side_effect=concurrent_creator):
                with self.assertRaises(FileExistsError):
                    seed_sqlite(path)
            self.assertEqual(path.read_bytes(), b"owner-created-file")
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_reference_checker_detects_same_count_data_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            seed_sqlite(path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE sites SET region='Changed' WHERE site_id=1")
                db.commit()
            with self.assertRaisesRegex(ValueError, "Dataset differs"):
                check_database(path)

    def test_sqlserver_guarded_bundle_and_parameter_rejection(self):
        for name in ["DataWarehouse", "master", "dbmind_synthetic_x'; DROP TABLE x;--", "dbmind_synthetic_"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                sqlserver_bundle(name)
        sql = sqlserver_bundle("dbmind_synthetic_local")
        self.assertIn("DB_NAME() <> N'dbmind_synthetic_local'", sql)
        self.assertIn("SCHEMA_ID(N'demo') IS NOT NULL", sql)
        self.assertIn("FROM sys.tables WHERE is_ms_shipped=0", sql)
        self.assertIn("sys.sp_getapplock", sql)
        self.assertIn("ROLLBACK TRANSACTION", sql)
        self.assertNotIn("DROP ", sql)
        self.assertNotIn("TRUNCATE ", sql)
        self.assertIn("N'Fictional Möbius'", sql)
        self.assertIn("FOREIGN KEY (work_order_id, equipment_id)", sql)
        self.assertEqual(sql.count("INSERT INTO [demo]."), sum(len(v) for v in generate().values()))
        self.assertEqual(sql.count("CREATE TABLE [demo]."), len(TABLES))
        verification = (ROOT / "data/synthetic/v1/verify.tsql.sql").read_text(encoding="utf-8")
        self.assertIn("DB_NAME() NOT LIKE", verification)
        self.assertIn("is_not_trusted=1", verification)
        for case in reference_cases():
            if case["sql_sqlserver"]:
                self.assertIn(case["sql_sqlserver"] + ";", verification)


if __name__ == "__main__":
    unittest.main()
