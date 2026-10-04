"""Read catalog metadata in the configured scope; identifiers come from catalogs."""
from collections import defaultdict
from dataclasses import replace
import math
import time

from .database import Database, DatabaseError


def quote(name, dialect):
    return '[' + name.replace(']', ']]') + ']' if dialect == 'sqlserver' else '"' + name.replace('"', '""') + '"'


def target(table, dialect):
    return quote(table['schema'], dialect) + '.' + quote(table['name'], dialect)


class Reader:
    """Trusted catalog reader; bounds each operation by remaining refresh time."""
    def __init__(self, settings, deadline=None, database_factory=Database):
        self.settings = settings
        self.deadline = deadline if deadline is not None else time.monotonic() + settings.discovery_timeout_seconds
        self.database_factory = database_factory

    def query(self, sql, parameters=()):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DatabaseError('timeout', 'Discovery deadline reached.')
        seconds = max(1, math.ceil(remaining))
        settings = replace(self.settings, connect_retries=0,
                           connect_timeout=min(self.settings.connect_timeout, seconds),
                           query_timeout=min(self.settings.query_timeout, seconds), max_rows=10000,
                           max_bytes=2 * 1024 * 1024)
        return self.database_factory(settings).query(sql, parameters, metadata=True)

    def records(self, sql, parameters=()):
        rows, columns = self.query(sql, parameters)
        return [dict(zip(columns, row)) for row in rows]


def sqlite_catalog(reader):
    objects = reader.records("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables, views = {}, []
    for obj in objects:
        full = 'main.' + obj['name']
        if obj['type'] == 'view':
            views.append({'name': full, 'status': 'unsupported'})
            continue
        table = {'schema': 'main', 'name': obj['name'], 'description': '', 'columns': [],
                 'primary_key': [], 'foreign_keys': [], 'indexes': [], 'definition': obj['sql']}
        pragma_name = quote(obj['name'], 'sqlite')
        columns = reader.records(f'PRAGMA main.table_xinfo({pragma_name})')
        for c in columns:
            table['columns'].append({'name': c['name'], 'ordinal': c['cid'] + 1, 'type': c['type'],
                'nullable': not bool(c['notnull'] or c['pk']), 'default': c['dflt_value'],
                'identity': False, 'computed': c['hidden'] in (2, 3), 'computed_definition': None,
                'description': '', 'length': None, 'precision': None, 'scale': None})
        table['primary_key'] = [c['name'] for c in sorted(columns, key=lambda c: c['pk']) if c['pk']]
        groups = defaultdict(list)
        for fk in reader.records(f'PRAGMA main.foreign_key_list({pragma_name})'):
            groups[fk['id']].append(fk)
        for key, rows in sorted(groups.items()):
            rows.sort(key=lambda r: r['seq'])
            table['foreign_keys'].append({'name': f'fk_{key}', 'from_table': full,
                'from_columns': [r['from'] for r in rows], 'to_table': 'main.' + rows[0]['table'],
                'to_columns': [r['to'] for r in rows], 'on_delete': rows[0]['on_delete'],
                'on_update': rows[0]['on_update']})
        for index in reader.records(f'PRAGMA main.index_list({pragma_name})'):
            fields = reader.records(f"PRAGMA main.index_xinfo({quote(index['name'], 'sqlite')})")
            definitions = reader.records("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index['name'],))
            table['indexes'].append({'name': index['name'], 'unique': bool(index['unique']),
                'primary_key': index['origin'] == 'pk', 'unique_constraint': index['origin'] == 'u',
                'filter': None, 'partial': bool(index['partial']), 'disabled': False,
                'definition': definitions[0]['sql'] if definitions else None,
                'columns': [{'name': f['name'], 'ordinal': f['seqno'] + 1,
                             'descending': bool(f['desc']), 'included': not bool(f['key'])} for f in fields]})
        table['indexes'].sort(key=lambda i: i['name'])
        tables[full] = table
    for table in tables.values():
        for fk in table['foreign_keys']:
            if any(c is None for c in fk['to_columns']) and fk['to_table'] in tables:
                fk['to_columns'] = tables[fk['to_table']]['primary_key']
    return tables, views


# Parameterized scope; no arbitrary UI strings become identifiers.
OBJECTS_SQL = """SELECT t.object_id, s.name AS schema_name, t.name AS table_name,
 CAST(ep.value AS nvarchar(1000)) AS description
 FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
 LEFT JOIN sys.extended_properties ep ON ep.major_id=t.object_id AND ep.minor_id=0 AND ep.class=1 AND ep.name=N'MS_Description'
 WHERE t.is_ms_shipped=0 AND s.name=? ORDER BY s.name,t.name"""
COLUMNS_SQL = """SELECT c.name, c.column_id AS ordinal, ty.name AS type, ts.name AS type_schema,
 c.max_length AS length, c.precision, c.scale, c.is_nullable AS nullable,
 c.is_identity AS [identity], c.is_computed AS computed, dc.definition AS [default],
 cc.definition AS computed_definition, ic.seed_value AS identity_seed, ic.increment_value AS identity_increment,
 CAST(ep.value AS nvarchar(1000)) AS description
 FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id
 JOIN sys.schemas ts ON ts.schema_id=ty.schema_id
 LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
 LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
 LEFT JOIN sys.identity_columns ic ON ic.object_id=c.object_id AND ic.column_id=c.column_id
 LEFT JOIN sys.extended_properties ep ON ep.class=1 AND ep.major_id=c.object_id AND ep.minor_id=c.column_id AND ep.name=N'MS_Description'
 WHERE c.object_id=? ORDER BY c.column_id"""
FOREIGN_KEYS_SQL = """SELECT fk.name, fkc.constraint_column_id AS ordinal, pc.name AS from_column,
 rs.name AS to_schema, rt.name AS to_name, rc.name AS to_column,
 fk.delete_referential_action_desc AS on_delete, fk.update_referential_action_desc AS on_update,
 fk.is_disabled AS disabled, fk.is_not_trusted AS untrusted
 FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
 JOIN sys.columns pc ON pc.object_id=fkc.parent_object_id AND pc.column_id=fkc.parent_column_id
 JOIN sys.tables rt ON rt.object_id=fkc.referenced_object_id JOIN sys.schemas rs ON rs.schema_id=rt.schema_id
 JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id
 WHERE fk.parent_object_id=? ORDER BY fk.name,fkc.constraint_column_id"""
INDEXES_SQL = """SELECT i.name, i.is_unique AS [unique], i.is_primary_key AS primary_key,
 i.is_unique_constraint AS unique_constraint, i.filter_definition AS filter, i.is_disabled AS disabled,
 c.name AS column_name, ic.key_ordinal AS ordinal, ic.index_column_id AS position,
 ic.is_descending_key AS descending, ic.is_included_column AS included
 FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
 LEFT JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
 WHERE i.object_id=? AND i.index_id>0 ORDER BY i.name,ic.index_column_id"""


def sqlserver_catalog(reader):
    tables, views = {}, []
    for scope in reader.settings.schemas:
        views += reader.records("SELECT s.name+'.'+v.name AS name FROM sys.views v JOIN sys.schemas s ON s.schema_id=v.schema_id WHERE v.is_ms_shipped=0 AND s.name=? ORDER BY v.name", (scope,))
        for obj in reader.records(OBJECTS_SQL, (scope,)):
            full = obj['schema_name'] + '.' + obj['table_name']
            if reader.settings.table_scope and full.casefold() not in {n.casefold() for n in reader.settings.table_scope}:
                continue
            identifier = obj['object_id']
            columns = reader.records(COLUMNS_SQL, (identifier,))
            for c in columns:
                for flag in ('nullable', 'identity', 'computed'):
                    c[flag] = bool(c[flag])
                c['description'] = c['description'] or ''
                # SQL Server max_length is BYTES, including for Unicode types; preserve raw value.
            indexes = {}
            for row in reader.records(INDEXES_SQL, (identifier,)):
                index = indexes.setdefault(row['name'], {k: row[k] for k in
                    ('name', 'unique', 'primary_key', 'unique_constraint', 'filter', 'disabled')})
                index.setdefault('columns', []).append({'name': row['column_name'], 'ordinal': row['ordinal'],
                    'position': row['position'], 'descending': bool(row['descending']), 'included': bool(row['included'])})
            primary = [i for i in indexes.values() if i['primary_key']]
            pk = [c['name'] for c in sorted(primary[0]['columns'], key=lambda c: c['ordinal']) if not c['included']] if primary else []
            groups = defaultdict(list)
            for row in reader.records(FOREIGN_KEYS_SQL, (identifier,)):
                groups[row['name']].append(row)
            fks = []
            for name, rows in sorted(groups.items()):
                rows.sort(key=lambda r: r['ordinal'])
                first = rows[0]
                fks.append({'name': name, 'from_table': full, 'from_columns': [r['from_column'] for r in rows],
                    'to_table': first['to_schema']+'.'+first['to_name'], 'to_columns': [r['to_column'] for r in rows],
                    'on_delete': first['on_delete'], 'on_update': first['on_update'],
                    'disabled': bool(first['disabled']), 'untrusted': bool(first['untrusted'])})
            tables[full] = {'schema': obj['schema_name'], 'name': obj['table_name'],
                'description': obj['description'] or '', 'columns': columns, 'primary_key': pk,
                'indexes': list(indexes.values()), 'foreign_keys': fks}
    return tables, [dict(view, status='unsupported') for view in views]


def discover_catalog(reader):
    return sqlite_catalog(reader) if reader.settings.dialect == 'sqlite' else sqlserver_catalog(reader)
