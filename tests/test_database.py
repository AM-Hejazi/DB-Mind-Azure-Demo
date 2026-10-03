"""Offline database/settings tests: real SQLite, mocked SQL Server/identity."""
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from synthetic_demo.seed import seed_sqlite
from src.database import Database, DatabaseError
from src.db_connection import access_token, connect_sqlserver, connection_string, require_read_principal
from src.settings import load_settings, SettingsError

AZURE = {"DB_PROFILE": "azure_sql", "DB_SERVER": "example.database.windows.net",
         "DB_NAME": "dbmind_synthetic_test", "DB_AUTH": "managed_identity", "DB_SCHEMA_SCOPE": "demo",
         "DB_SCHEMA_JSON": "var/synthetic/azure-schema-v1.json"}
READ_PRINCIPAL = ("runtime", 0, 0, 0, 0) + (0,) * 12


class SettingsTests(unittest.TestCase):
    def test_discovery_deadline_supports_bounded_remote_catalog_refresh(self):
        self.assertEqual(load_settings({}).discovery_timeout_seconds, 30)
        for seconds in (1, 120, 300):
            with self.subTest(seconds=seconds):
                settings = load_settings(dict(AZURE, DB_DISCOVERY_TIMEOUT_SECONDS=str(seconds)))
                self.assertEqual(settings.discovery_timeout_seconds, seconds)
        for seconds in (0, 301):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(SettingsError, 'DB_DISCOVERY_TIMEOUT_SECONDS'):
                load_settings(dict(AZURE, DB_DISCOVERY_TIMEOUT_SECONDS=str(seconds)))

    def test_defaults_and_imports_are_offline_without_autodetection(self):
        settings = load_settings({})
        self.assertEqual(settings.profile, "local_synthetic")
        self.assertTrue(str(settings.sqlite_path).endswith("var/synthetic/maintenance_v1.sqlite3"))
        self.assertNotIn("SQLSERVER", settings.public_database_config())
        code = """
from unittest.mock import patch
import sqlite3, socket
with patch.object(sqlite3, 'connect', side_effect=AssertionError('connection')), \
     patch.object(socket, 'socket', side_effect=AssertionError('network')), \
     patch('pathlib.Path.mkdir', side_effect=AssertionError('write')):
    import config, src.validator, src.database, src.db_check
"""
        subprocess.run([sys.executable, "-B", "-c", code], check=True, capture_output=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("DB_")})

    def test_required_fields_and_profile_conflicts_are_actionable(self):
        for key in ("DB_SERVER", "DB_NAME", "DB_AUTH", "DB_SCHEMA_SCOPE", "DB_SCHEMA_JSON"):
            env = dict(AZURE)
            del env[key]
            with self.subTest(key=key), self.assertRaisesRegex(SettingsError, key):
                load_settings(env)
        invalid = [dict(AZURE, DB_DIALECT="sqlite"), dict(AZURE, DB_SCHEMA_SCOPE="dbo"),
                   dict(AZURE, DB_TRUST_LOCAL_CERTIFICATE="true"), dict(AZURE, DB_TRUSTED_CONNECTION="yes"),
                   dict(AZURE, DB_NAME="arbitrary"), dict(AZURE, DB_SERVER="host;PWD=hidden"),
                   dict(AZURE, DB_PASSWORD="hidden"), {"DB_DIALECT": "sqlserver"},
                   {"DB_QUERY_TIMEOUT_SECONDS": "hidden"}, {"DB_CONNECT_RETRIES": "100"}]
        for env in invalid:
            with self.subTest(env=env), self.assertRaises(SettingsError) as context:
                load_settings(env)
            self.assertNotIn("hidden", str(context.exception))

    def test_credentials_are_absent_from_repr_and_legacy_config(self):
        settings = load_settings(dict(AZURE, DB_AUTH="sql_password", DB_USER="runtime_user", DB_PASSWORD="secret;}PWD=x"))
        self.assertNotIn("secret", repr(settings))
        self.assertNotIn("secret", str(settings.public_database_config()))
        self.assertIn("PWD={secret;}}PWD=x}", connection_string(settings))
        for user in ["sa", "dbo"]:
            with self.assertRaises(SettingsError):
                load_settings(dict(AZURE, DB_AUTH="sql_password", DB_USER=user, DB_PASSWORD="setup"))


class SQLiteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "fixture.sqlite3"
        seed_sqlite(self.path)
        self.settings = load_settings({"DB_SQLITE_PATH": str(self.path),"DB_SCHEMA_JSON":str(Path(self.folder.name)/"snapshot.json")})
        from src.discovery import refresh
        refresh(self.settings)

    def test_parameterized_read_and_legacy_wrapper(self):
        rows, columns = Database(self.settings).query("SELECT asset_code FROM equipment WHERE equipment_id=?", (1,))
        self.assertEqual((rows, columns), ([("SYN-EQ-001",)], ["asset_code"]))
        from src.validator import validate_sql
        with patch.dict(os.environ, {"DB_SQLITE_PATH": str(self.path),"DB_SCHEMA_JSON":str(self.settings.schema_json)}, clear=True):
            self.assertEqual(validate_sql("SELECT COUNT(*) AS n FROM equipment"), ([(60,)], ["n"], None))
        import json
        from synthetic_demo.check import REFERENCE_PATH
        for case in json.loads(REFERENCE_PATH.read_text())["cases"]:
            if case["sql_sqlite"]:
                rows, columns = Database(self.settings).query(case["sql_sqlite"])
                self.assertEqual([list(row) for row in rows], case["expected_rows"])
                self.assertEqual(columns, case["expected_columns"])

    def test_writes_attachment_and_unmarked_database_are_refused(self):
        attachment = Path(self.folder.name) / "outside.sqlite3"
        for query in ["DELETE FROM equipment", "PRAGMA query_only=OFF", f"ATTACH DATABASE '{attachment}' AS outside"]:
            with self.subTest(query=query), self.assertRaises(DatabaseError):
                Database(self.settings).query(query)
        self.assertFalse(attachment.exists())
        self.assertEqual(Database(self.settings).query("SELECT COUNT(*) FROM equipment")[0], [(60,)])
        empty = Path(self.folder.name) / "empty.sqlite3"
        from contextlib import closing
        with closing(sqlite3.connect(empty)) as connection:
            connection.execute("CREATE TABLE arbitrary (id INTEGER)")
            connection.commit()
        with self.assertRaisesRegex(DatabaseError, "synthetic"):
            Database(replace(self.settings, sqlite_path=empty)).query("SELECT 1")
        missing = Path(self.folder.name) / "missing.sqlite3"
        with self.assertRaises(DatabaseError):
            Database(replace(self.settings, sqlite_path=missing)).query("SELECT 1")
        self.assertFalse(missing.exists())

    def test_row_byte_limits_and_query_deadline(self):
        with self.assertRaisesRegex(DatabaseError, "limits"):
            Database(replace(self.settings, max_rows=3)).query("SELECT * FROM equipment")
        with self.assertRaisesRegex(DatabaseError, "limits"):
            Database(replace(self.settings,max_bytes=1024)).query("SELECT '" + "X"*2000 + "' FROM equipment LIMIT 1")
        import time
        started=time.monotonic()
        with self.assertRaisesRegex(DatabaseError, "timeout"):
            Database(replace(self.settings,query_timeout=.001)).query(
                "SELECT SUM(a.labor_minutes * b.labor_minutes) FROM work_orders a CROSS JOIN work_orders b")
        self.assertLess(time.monotonic()-started,1)



class RuntimePrincipalTests(unittest.TestCase):
    def reader_cursor(self, principal=READ_PRINCIPAL, targets=(("guest", 0),)):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [principal] + [(1, 0, 0, 0, 0, 0)] * 9
        cursor.fetchall.return_value = list(targets)
        return cursor

    def test_azure_reader_uses_valid_user_scope_without_invalid_database_probe(self):
        principal = ("dbmind_demo_runtime",) + READ_PRINCIPAL[1:]
        cursor = self.reader_cursor(principal)
        # Model the reported engine behavior: the old, invalid probe yields NULL.
        def execute(sql, *args):
            if "'DATABASE', 'IMPERSONATE ANY USER'" in sql:
                cursor.fetchone.side_effect = [principal[:13] + (None,) + principal[14:]]
        cursor.execute.side_effect = execute
        require_read_principal(cursor)
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("HAS_PERMS_BY_NAME('dbo', 'USER', 'IMPERSONATE')", queries[0])
        self.assertNotIn("IMPERSONATE ANY USER", " ".join(queries))
        self.assertEqual(len(queries), 11)  # Principal, user targets, nine tables.

    def test_other_users_are_checked_with_effective_permissions(self):
        cursor = self.reader_cursor(targets=(("guest", 0), ("setup_user", 0)))
        require_read_principal(cursor)
        query = cursor.execute.call_args_list[1].args[0]
        self.assertIn("HAS_PERMS_BY_NAME(name, 'USER', 'IMPERSONATE')", query)
        self.assertIn("FROM sys.database_principals", query)
        self.assertIn("principal_id <> USER_ID()", query)
        self.assertIn("'E'", query)  # Includes Entra users, as well as SQL users.
        self.assertNotIn("grantee_principal_id", query)  # No direct-grant-only filter.

    def test_direct_role_inherited_and_control_implied_impersonation_are_rejected(self):
        # SQL Server evaluates all three sources as effective IMPERSONATE=1.
        # Engine role inheritance itself still needs integration verification.
        for source in ("direct", "nested_role", "user_control"):
            for target in ("dbo", "setup_user"):
                with self.subTest(source=source, target=target):
                    principal = list(READ_PRINCIPAL)
                    targets = [("setup_user", 0)]
                    if target == "dbo":
                        principal[13] = 1
                    else:
                        targets = [("guest", 0), ("setup_user", 1)]
                    cursor = self.reader_cursor(tuple(principal), targets)
                    with self.assertRaises(PermissionError):
                        require_read_principal(cursor)
                    self.assertLessEqual(cursor.execute.call_count, 2)

    def test_unknown_impersonation_results_fail_closed(self):
        for result in (None, -1, 2):
            with self.subTest(result=result, target="dbo"):
                principal = list(READ_PRINCIPAL)
                principal[13] = result
                with self.assertRaises(PermissionError):
                    require_read_principal(self.reader_cursor(tuple(principal)))
            with self.subTest(result=result, target="setup_user"):
                with self.assertRaises(PermissionError):
                    require_read_principal(self.reader_cursor(targets=(("setup_user", result),)))

    def test_all_existing_elevation_checks_still_reject(self):
        for index in range(1, len(READ_PRINCIPAL)):
            with self.subTest(index=index):
                principal = list(READ_PRINCIPAL)
                principal[index] = 1
                with self.assertRaises(PermissionError):
                    require_read_principal(self.reader_cursor(tuple(principal)))
        with self.assertRaises(PermissionError):
            require_read_principal(self.reader_cursor(("dbo",) + READ_PRINCIPAL[1:]))

    def test_unknown_database_and_schema_checks_still_reject(self):
        for index in (*range(1, 4), *range(5, len(READ_PRINCIPAL))):
            with self.subTest(index=index):
                principal = list(READ_PRINCIPAL)
                principal[index] = None
                with self.assertRaises(PermissionError):
                    require_read_principal(self.reader_cursor(tuple(principal)))

    def test_every_table_write_or_unknown_permission_still_rejects(self):
        for table_index in range(9):
            for permission_index in range(1, 6):
                for result in (1, None):
                    with self.subTest(table=table_index, permission=permission_index, result=result):
                        cursor = self.reader_cursor()
                        permissions = [1, 0, 0, 0, 0, 0]
                        permissions[permission_index] = result
                        cursor.fetchone.side_effect = ([READ_PRINCIPAL]
                            + [(1, 0, 0, 0, 0, 0)] * table_index + [tuple(permissions)])
                        with self.assertRaises(PermissionError):
                            require_read_principal(cursor)

    def test_discovery_allows_absent_select_but_never_unknown_or_write_results(self):
        cursor = self.reader_cursor()
        cursor.fetchone.side_effect = [READ_PRINCIPAL] + [(0, 0, 0, 0, 0, 0)] * 9
        require_read_principal(cursor, require_all_tables=False)
        for permissions in ((None, 0, 0, 0, 0, 0), (0, None, 0, 0, 0, 0),
                            (0, 1, 0, 0, 0, 0), (0, -1, 0, 0, 0, 0)):
            with self.subTest(permissions=permissions):
                cursor = self.reader_cursor()
                cursor.fetchone.side_effect = [READ_PRINCIPAL, permissions]
                with self.assertRaises(PermissionError):
                    require_read_principal(cursor, require_all_tables=False)

    def test_missing_or_truncated_principal_results_fail_closed(self):
        for principal in (None, READ_PRINCIPAL[:13], READ_PRINCIPAL + (0,)):
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                require_read_principal(self.reader_cursor(principal))


class SQLServerTests(unittest.TestCase):
    def setUp(self):
        from synthetic_demo.schema import TABLES
        tables={f'demo.{t.name}':{'schema':'demo','name':t.name,'columns':[{'name':c.name} for c in t.columns]} for t in TABLES}
        patcher=patch('src.sql_gate.read_snapshot',return_value={'metadata':{'tables':tables}})
        patcher.start();self.addCleanup(patcher.stop)

    def test_select_only_principal_and_parameterized_query_success(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [READ_PRINCIPAL] + [(1, 0, 0, 0, 0, 0)] * 9
        cursor.fetchall.return_value = [("guest", 0)]
        cursor.description = [("asset_code",)]
        cursor.fetchmany.side_effect = [[("SYN-EQ-001",)], []]
        connector = MagicMock(return_value=connection)
        rows, columns = Database(load_settings(AZURE), connector=connector).query(
            "SELECT asset_code FROM [demo].equipment WHERE equipment_id=?", (1,))
        self.assertEqual((rows, columns), ([("SYN-EQ-001",)], ["asset_code"]))
        self.assertEqual(cursor.execute.call_args.args[1],(1,))
        self.assertIn("TOP 1001",cursor.execute.call_args.args[0])
        self.assertIn("[demo].[equipment]",cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_count, 12)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_tokens_tls_and_no_credential_chain_fallback(self):
        settings = load_settings(AZURE)
        connection = MagicMock()
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        with patch.dict(sys.modules, {"pyodbc": driver}), patch("src.db_connection.access_token", return_value=b"packed-token"):
            self.assertIs(connect_sqlserver(settings), connection)
        args, options = driver.connect.call_args
        self.assertIn("Encrypt={yes}", args[0])
        self.assertIn("TrustServerCertificate={no}", args[0])
        for forbidden in ["UID=", "PWD=", "Authentication=", "Trusted_Connection="]:
            self.assertNotIn(forbidden, args[0])
        self.assertEqual(options["attrs_before"], {1256: b"packed-token"})
        self.assertEqual(connection.timeout, settings.query_timeout)
        credential = MagicMock()
        credential.get_token.return_value = SimpleNamespace(token="abc")
        identity = SimpleNamespace(ManagedIdentityCredential=MagicMock(return_value=credential),
                                   AzureCliCredential=MagicMock(return_value=credential))
        with patch.dict(sys.modules, {"azure": SimpleNamespace(), "azure.identity": identity}):
            self.assertEqual(access_token(settings), struct.pack("<I", 6) + "abc".encode("utf-16-le"))
            identity.AzureCliCredential.assert_not_called()
            access_token(replace(settings, auth="azure_cli"))
        credential.get_token.assert_called_with("https://database.windows.net/.default")
        self.assertEqual(credential.close.call_count, 2)

    def test_privileged_principal_and_custom_write_grants_fail_closed(self):
        for values in [("dbo", 0, 0, 0, None, 0, 0, 0, 0),
                       ("runtime", 1, 0, 0, None, 0, 0, 0, 0),
                       ("runtime", 0, 0, 0, None, 1, 0, 0, 0)]:
            cursor = MagicMock()
            cursor.fetchone.return_value = values
            with self.assertRaises(PermissionError):
                require_read_principal(cursor)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [READ_PRINCIPAL, (1, 0, 1, 0, 0, 0)]
        with self.assertRaises(PermissionError):
            require_read_principal(cursor)

    def test_retry_bounds_fresh_connections_cleanup_and_safe_errors(self):
        connector = MagicMock(side_effect=[RuntimeError("08001", "sensitive host/PWD"),
                                          RuntimeError("08001", "sensitive host/PWD"),
                                          RuntimeError("08001", "sensitive host/PWD")])
        sleep = MagicMock()
        with self.assertRaises(DatabaseError) as context:
            Database(load_settings(AZURE), connector=connector, sleep=sleep).query("SELECT 1")
        self.assertEqual(connector.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn("sensitive", str(context.exception))
        connector.reset_mock()
        connector.side_effect = RuntimeError("28000", "secret")
        with self.assertRaises(DatabaseError):
            Database(load_settings(AZURE), connector=connector, sleep=sleep).query("SELECT 1")
        self.assertEqual(connector.call_count, 1)
        connections = [MagicMock(), MagicMock()]
        for connection in connections:
            connection.cursor.return_value.execute.side_effect = RuntimeError("42000", "secret SQL/host")
        connector = MagicMock(side_effect=connections)
        database = Database(load_settings(AZURE), connector=connector)
        for _ in range(2):
            with self.assertRaises(DatabaseError) as context:
                database.query("SELECT 1")
            self.assertNotIn("secret", str(context.exception))
        for connection in connections:
            connection.close.assert_called_once()
            connection.cursor.return_value.close.assert_called_once()
        self.assertEqual(connector.call_count, 2)


if __name__ == "__main__":
    unittest.main()
