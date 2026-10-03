"""Explicit operator-only Azure SQL read verification; never exposed to visitors."""
from contextlib import closing
from dataclasses import replace
import argparse
import json
import os
import subprocess
import sys

from .db_connection import connect_sqlserver, require_read_principal, connection_string, access_token
from .database import Database, DatabaseError
from .settings import load_settings, SettingsError
from .snapshots import read_snapshot


def verify(settings):
    if settings.profile != 'azure_sql' or settings.dialect != 'sqlserver':
        raise SettingsError('Azure SQL verification requires an explicit Azure SQL profile')
    with closing(connect_sqlserver(settings)) as connection:
        with closing(connection.cursor()) as cursor:
            require_read_principal(cursor)
            cursor.execute("SELECT CURRENT_USER, DB_NAME(), "
                           "CAST(SERVERPROPERTY('EngineEdition') AS int), "
                           "IS_MEMBER('dbmind_demo_reader')")
            user, database, engine, reader = cursor.fetchone()
    if database != settings.database or engine != 5 or reader != 1:
        raise PermissionError('Azure SQL identity/engine/reader verification failed')
    snapshot = read_snapshot(settings)
    if len(snapshot['metadata']['tables']) != 9 or not snapshot['complete'] or not snapshot['sampling_complete']:
        raise PermissionError('A complete nine-table sampled snapshot is required')
    rows, _ = Database(settings).query('SELECT COUNT(*) AS equipment_count FROM [demo].equipment')
    if rows != [(60,)]:
        raise PermissionError('Synthetic reference equipment count differs')
    return {'profile': settings.profile, 'dialect': settings.dialect,
            'auth': settings.auth, 'database': database, 'current_user': user,
            'engine_edition': engine, 'reader_membership': reader, 'runtime_guard': 'passed',
            'tables': 9, 'complete': True, 'sampling_complete': True,
            'connected': True, 'equipment_count': rows[0][0],
            'tls_encrypt': True, 'trust_server_certificate': settings.trust_local_certificate}


def _tls_failure_check():
    """Fresh process and handshake with an intentionally empty trust store."""
    import pyodbc
    pyodbc.pooling = False
    settings = load_settings()
    certificate_rejected = []
    def connector(config):
        options = {'timeout': config.connect_timeout, 'autocommit': True}
        if config.auth != 'sql_password':
            options['attrs_before'] = {1256: access_token(config)}
        try:
            return pyodbc.connect(connection_string(config), **options)
        except pyodbc.Error as error:
            message = str(error).lower()
            certificate_rejected.append('certificate' in message and 'verify' in message)
            raise
    try:
        Database(replace(settings, connect_retries=0), connector=connector).query(
            'SELECT COUNT(*) FROM [demo].equipment')
    except DatabaseError as error:
        if error.code == 'connectivity' and certificate_rejected == [True]:
            return
    raise AssertionError('Expected an actual TLS certificate verification failure')


def negative_checks(settings):
    """Read-only SQL error, rejected write and isolated TLS failure, no fallback."""
    database = Database(settings)
    try:
        database.query("SELECT CAST('synthetic-not-an-integer' AS INT) FROM [demo].equipment")
    except DatabaseError as error:
        if error.code != 'query':
            raise AssertionError('Expected an actual SQL conversion error') from None
    else:
        raise AssertionError('SQL error unexpectedly succeeded')
    try:
        database.query('DELETE FROM [demo].equipment')
    except DatabaseError as error:
        if error.code != 'rejected':
            raise AssertionError('Write was not stopped by the SQL gate') from None
    else:
        raise AssertionError('Write gate failed')
    # Only the diagnostic child loses its CA store; production configuration,
    # system certificate files and the serving process are untouched.
    env = dict(os.environ, SSL_CERT_FILE='/dev/null', SSL_CERT_DIR='/nonexistent-dbmind-test-ca')
    child = subprocess.run([sys.executable, '-c',
                            'from src.runtime_diagnostics import _tls_failure_check; _tls_failure_check()'],
                           env=env, capture_output=True, timeout=60, check=False)
    if child.returncode:
        raise AssertionError('Isolated TLS failure verification failed')
    if database.query('SELECT COUNT(*) FROM [demo].equipment')[0] != [(60,)]:
        raise AssertionError('Recovery read differs')
    return {'sql_error': 'query', 'write_gate': 'rejected before execution',
            'tls_certificate_verification': 'connectivity', 'sqlite_fallback': False,
            'recovery_equipment_count': 60}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-checks', action='store_true')
    args = parser.parse_args()
    try:
        settings = load_settings()
        result = verify(settings)
        if args.negative_checks:
            result['negative_checks'] = negative_checks(settings)
        print(json.dumps(result, indent=2))
    except Exception as error:
        # Driver exceptions can contain connection details; never print their text.
        print('Azure runtime verification failed:', type(error).__name__)
        raise SystemExit(1)


if __name__ == '__main__': main()
