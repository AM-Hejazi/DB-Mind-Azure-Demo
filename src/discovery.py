"""Explicit discovery/refresh and optional preflight; never creates database data."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import time

from synthetic_demo.schema import TABLES
from .catalog import Reader, discover_catalog, quote, target
from .database import DatabaseError
from .settings import load_settings, SettingsError
from .snapshots import (FORMAT_VERSION, SnapshotError, scalar, encoded, digest, identity, policy,
                        metadata_fingerprint, atomic_write, read_snapshot)


def sample_table(reader, table):
    s = reader.settings
    if s.sample_rows == 0:
        return {'status': 'disabled', 'rows': []}
    qualified = target(table, s.dialect)
    cap = s.sample_scan_rows + 1
    probe = (f'SELECT COUNT(*) AS n FROM (SELECT TOP ({cap}) 1 AS v FROM {qualified}) q'
             if s.dialect == 'sqlserver' else f'SELECT COUNT(*) AS n FROM (SELECT 1 FROM {qualified} LIMIT {cap}) q')
    count = reader.query(probe)[0][0][0]
    if count > s.sample_scan_rows:
        return {'status': 'skipped_large', 'rows': [], 'population_at_least': count}
    expressions = []
    for column in table['columns']:
        name = quote(column['name'], s.dialect)
        limit = s.sample_value_chars
        if s.dialect == 'sqlite':
            expression = f"CASE WHEN typeof({name})='text' THEN substr({name},1,{limit+1}) WHEN typeof({name})='blob' THEN substr({name},1,{limit//2+1}) ELSE {name} END"
        elif column['type'] in {'varchar', 'nvarchar', 'text', 'ntext', 'char', 'nchar', 'xml'}:
            expression = f'LEFT(CONVERT(nvarchar(max),{name}),{limit+1})'
        elif column['type'] in {'binary', 'varbinary', 'image'}:
            expression = f'SUBSTRING(CONVERT(varbinary(max),{name}),1,{limit//2+1})'
        else:
            expression = name
        expressions.append(expression + ' AS ' + name)
    fields = ', '.join(expressions)
    sql = (f'SELECT TOP ({s.sample_rows}) {fields} FROM {qualified} ORDER BY NEWID()'
           if s.dialect == 'sqlserver' else f'SELECT {fields} FROM {qualified} ORDER BY random() LIMIT {s.sample_rows}')
    rows, columns = reader.query(sql)
    values = [{name: scalar(value, s.sample_value_chars) for name, value in zip(columns, row)} for row in rows]
    return {'status': 'ok' if values else 'empty', 'rows': values, 'population_at_probe': count}


def refresh(settings, reader=None):
    reader = reader or Reader(settings)
    tables, views = discover_catalog(reader)
    if not tables:
        raise SnapshotError('No accessible tables in the configured synthetic scope')
    expected = {settings.schemas[0]+'.'+t.name for t in TABLES}
    missing = sorted(expected - tables.keys())
    samples, used = {}, 0
    for name, table in sorted(tables.items()):
        try:
            entry = sample_table(reader, table)
            bounded = []
            for row in entry['rows']:
                cost = len(encoded(row)) + 1
                if used + cost > settings.sample_total_bytes:
                    entry['status'] = 'byte_limit'
                    break
                bounded.append(row)
                used += cost
            entry['rows'] = bounded
            samples[name] = entry
        except DatabaseError as error:
            samples[name] = {'status': error.code, 'rows': []}
        except SnapshotError:
            samples[name] = {'status': 'unsupported_value', 'rows': []}
    errors = [name for name, sample in samples.items() if sample['status'] not in
              {'ok', 'empty', 'disabled', 'skipped_large', 'byte_limit'}]
    snapshot = {'format': 'dbmind.snapshot', 'version': FORMAT_VERSION, 'synthetic_only': True,
        'identity': identity(settings), 'dialect': settings.dialect,
        'extracted_at': datetime.now(timezone.utc).isoformat(),
        'metadata': {'tables': tables, 'views': views}, 'samples': samples,
        'sample_policy': policy(settings), 'sample_bytes': used,
        'schema_fingerprint': metadata_fingerprint(tables, views),
        'visibility': 'accessible_objects_only', 'missing_expected_tables': missing,
        'complete': not missing and not errors,
        'sampling_complete': all(s['status'] in {'ok', 'empty', 'disabled'} for s in samples.values()),
        'limitations': ['Hidden metadata cannot be enumerated.', 'Views are listed but not sampled/supported.',
                        'SQLite computed expressions are retained in table DDL; index expressions in index DDL, not parsed.']}
    snapshot['revision'] = digest(snapshot)
    if not snapshot['complete']:
        atomic_write(settings.schema_json.with_suffix('.failed.json'), snapshot)
        raise SnapshotError('Discovery incomplete; prior cache preserved, inspect the .failed.json report')
    atomic_write(settings.schema_json, snapshot)
    # Source revision binds derived retrieval; rebuilding is cheap and local.
    from .schema_context import ensure_index
    ensure_index(snapshot, settings)
    return snapshot


def preflight(settings, reader=None):
    """Explicit startup check: detect schema change, expiry, mismatch, or missing cache."""
    reader = reader or Reader(settings)
    try:
        snapshot = read_snapshot(settings)
    except SnapshotError:
        return refresh(settings, reader)
    tables, views = discover_catalog(reader)
    if snapshot['schema_fingerprint'] != metadata_fingerprint(tables, views):
        return refresh(settings, reader)
    from .schema_context import ensure_index
    ensure_index(snapshot, settings)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['refresh', 'preflight', 'inspect'])
    args = parser.parse_args()
    try:
        settings = load_settings()
        snapshot = read_snapshot(settings) if args.operation == 'inspect' else (
            refresh(settings) if args.operation == 'refresh' else preflight(settings))
        print(json.dumps({'format_version': snapshot['version'], 'tables': len(snapshot['metadata']['tables']),
                         'complete': snapshot['complete'], 'sampling_complete': snapshot['sampling_complete'],
                         'statuses': {k: v['status'] for k,v in snapshot['samples'].items()},
                         'schema_fingerprint': snapshot['schema_fingerprint']}, indent=2))
    except (SettingsError, SnapshotError, DatabaseError) as error:
        parser.exit(1, f'Discovery failed: {error}\n')


if __name__ == '__main__':
    main()
