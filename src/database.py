"""Per-call read connections, bounded results, deadlines, and safe error messages."""
from contextlib import closing
import sqlite3
import time
import threading

from synthetic_demo.schema import APPLICATION_ID, VERSION, TABLES
from .db_connection import connect_sqlserver, require_read_principal, require_custom_read_principal, TokenAuthenticationError
from .settings import load_settings


class DatabaseError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def map_error(error):
    """Driver messages can contain secrets, hosts, SQL, or data; never forward them."""
    if isinstance(error, DatabaseError):
        return error
    if isinstance(error, ImportError):
        return DatabaseError("dependency", "Install the SQL Server Python/ODBC dependencies documented in docs/configuration.md.")
    if isinstance(error, PermissionError) or getattr(error, "sqlite_errorcode", None) in {
        sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY
    }:
        return DatabaseError("permissions", "Use a restricted SELECT-only runtime identity for the approved tables.")
    if isinstance(error, TokenAuthenticationError):
        return DatabaseError("authentication", "Token authentication failed; check the selected identity, Azure CLI login, or managed identity assignment.")
    state = str(error.args[0]) if error.args else ""
    if state == "28000":
        return DatabaseError("authentication", "SQL authentication failed; check the selected identity and database principal.")
    if state in {"HYT00", "HYT01"} or isinstance(error, TimeoutError) or (
            isinstance(error, sqlite3.OperationalError) and "interrupted" in str(error)):
        return DatabaseError("timeout", "Database operation exceeded its configured timeout.")
    if state.startswith("08"):
        return DatabaseError("connectivity", "Database connection failed; check networking, TLS, driver, and server availability.")
    return DatabaseError("query", "Database operation failed; check query syntax, schema scope, and permissions.")


class Database:
    def __init__(self, settings=None, *, connector=None, sleep=time.sleep):
        self.settings = settings or load_settings()
        self._connector = connector or connect_sqlserver
        self._sleep = sleep

    def _open(self, metadata=False, cancelled=None):
        s = self.settings
        if s.dialect == "sqlite":
            connection = None
            try:
                connection = sqlite3.connect(s.sqlite_path.absolute().as_uri() + "?mode=ro", uri=True,
                                             timeout=s.connect_timeout)
                connection.execute("PRAGMA query_only=ON")
                if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or
                        connection.execute("PRAGMA user_version").fetchone()[0] != VERSION):
                    connection.close()
                    raise DatabaseError("identity", "Use the versioned DB-Mind synthetic SQLite fixture; seed a new file explicitly.")
                allowed_tables = {t.name for t in TABLES}

                def authorize(action, first, second, database, trigger):
                    if action == sqlite3.SQLITE_READ:
                        # SQLite's optimized COUNT(*) reports an empty column and no database.
                        return sqlite3.SQLITE_OK if database in {"main", None} and (metadata or first in allowed_tables) else sqlite3.SQLITE_DENY
                    if metadata and action == sqlite3.SQLITE_PRAGMA and first in {
                        "table_xinfo", "foreign_key_list", "index_list", "index_xinfo"
                    }:
                        return sqlite3.SQLITE_OK
                    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() == "load_extension":
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK if action in {
                        sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE
                    } else sqlite3.SQLITE_DENY

                connection.set_authorizer(authorize)
                return connection
            except sqlite3.Error:
                if connection is not None:
                    connection.close()
                raise DatabaseError("unavailable", "Synthetic SQLite database unavailable; run the explicit seed command in docs/synthetic-data.md.") from None
        for attempt in range(s.connect_retries + 1):
            if cancelled is not None and cancelled.is_set():
                raise DatabaseError("timeout", "Database operation cancelled")
            try:
                return self._connector(s)
            except Exception as error:
                # Retry opening only; never replay SQL or retry authentication/permission failures.
                state = str(error.args[0]) if error.args else ""
                transient = state in {"08001", "08006", "08S01", "HYT00", "HYT01"}
                if not transient or attempt == s.connect_retries:
                    raise map_error(error) from None
                self._sleep(min(0.25 * 2 ** attempt, 1.0))

    def query(self, sql, parameters=(), *, metadata=False, cancelled=None):
        """All runtime SQL passes the AST gate; metadata is an internal discovery API."""
        if not isinstance(sql, str) or not sql.strip():
            raise DatabaseError("query", "A nonempty SQL query is required.")
        if not metadata:
            from src.sql_gate import prepare_read, SQLGateError
            try:
                sql = prepare_read(sql, self.settings, parameters)
            except SQLGateError as error:
                raise DatabaseError("rejected", str(error)) from None
        cancellation = cancelled or threading.Event()
        done = threading.Event()
        handles, result = {}, []
        deadline = time.monotonic() + self.settings.query_timeout

        def run():
            try:
                result.append((True, self._run_query(sql, parameters, metadata, cancellation, handles, deadline)))
            except Exception as error:
                result.append((False, map_error(error)))
            finally:
                done.set()
        worker = threading.Thread(target=run, daemon=True, name="dbmind-read-query")
        worker.start()
        while not done.is_set():
            if cancellation.is_set() or time.monotonic() >= deadline:
                cancellation.set()
                def cancel_driver():
                    try:
                        if self.settings.dialect == "sqlite" and handles.get("connection") is not None:
                            handles["connection"].interrupt()
                        elif handles.get("cursor") is not None:
                            handles["cursor"].cancel()
                    except Exception:
                        pass
                threading.Thread(target=cancel_driver, daemon=True, name="dbmind-cancel-query").start()
                raise DatabaseError("timeout", "Database query cancelled or exceeded its configured timeout/deadline.")
            done.wait(0.02)
        if cancellation.is_set() or time.monotonic() >= deadline:
            raise DatabaseError("timeout", "Database query cancelled or exceeded its configured timeout/deadline.")
        if not result[0][0]:
            raise result[0][1] from None
        return result[0][1]

    def _run_query(self, sql, parameters, metadata, cancelled, handles, deadline):
        s = self.settings
        with closing(self._open(metadata=metadata, cancelled=cancelled)) as connection:
            handles["connection"] = connection
            with closing(connection.cursor()) as cursor:
                handles["cursor"] = cursor
                if cancelled.is_set():
                    raise DatabaseError("timeout", "Database query cancelled")
                if s.dialect == "sqlserver":
                    if s.profile == 'azure_sql_custom':
                        require_custom_read_principal(cursor, s)
                    else:
                        require_read_principal(cursor, require_all_tables=not metadata)
                else:
                    connection.set_progress_handler(lambda: int(cancelled.is_set() or time.monotonic() >= deadline), 1000)
                if cancelled.is_set():
                    raise DatabaseError("timeout", "Database query cancelled")
                cursor.execute(sql, tuple(parameters))
                if not cursor.description:
                    raise DatabaseError("query", "Only queries returning a result table are supported.")
                columns = [column[0] for column in cursor.description]
                size = sum(len(c.encode("utf-8")) for c in columns)
                if size > s.max_bytes:
                    raise DatabaseError("result_limit", "Query column metadata exceeds configured byte limits.")
                rows = []
                while True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise DatabaseError("timeout", "Database operation exceeded its configured timeout.")
                    batch = cursor.fetchmany(min(100, s.max_rows + 1 - len(rows)))
                    if not batch:
                        break
                    for row in batch:
                        converted = tuple(row)
                        size += sum(len(v) if isinstance(v, bytes) else len(str(v).encode("utf-8")) for v in converted)
                        if len(rows) >= s.max_rows or size > s.max_bytes:
                            raise DatabaseError("result_limit", "Query result exceeds configured row/byte limits; narrow the question.")
                        rows.append(converted)
                return rows, columns
