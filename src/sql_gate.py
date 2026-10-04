"""Conservative AST gate for one read query over approved discovered fixture objects."""
from synthetic_demo.schema import TABLES
from src.snapshots import read_snapshot, SnapshotError


class SQLGateError(ValueError):
    pass


# Explicit supported grammar: future parser constructs fail closed by default.
NODES = frozenset('''Select With CTE Subquery Table TableAlias From Join Where Group Having Order Ordered
Limit Distinct Alias Column Identifier Star Literal Null Boolean Placeholder Paren
And Or Not EQ NEQ GT GTE LT LTE Is In Between Exists Like ILike
Add Sub Mul Div Mod Neg Case If Cast DataType DataTypeParam Var
Count Sum Avg Min Max Round Abs Coalesce Nullif Lower Upper Length Substring Trim
Date DateAdd DateDiff CurrentDate CurrentTimestamp TimeToStr StrToTime TsOrDsToDate
'''.split())
ANONYMOUS = {'sqlite': {'date', 'datetime', 'strftime', 'julianday', 'substr', 'instr'}, 'sqlserver': set()}
DATE_UNITS = {'DAY', 'MONTH', 'YEAR', 'HOUR', 'MINUTE', 'SECOND', 'WEEK', 'QUARTER', 'MILLISECOND'}


def approved_metadata(settings):
    try:
        tables = read_snapshot(settings)['metadata']['tables']
    except (SnapshotError, OSError, KeyError):
        raise SQLGateError(('A valid schema snapshot' if settings.profile == 'azure_sql_custom' else 'A valid synthetic schema snapshot')+' is required; run explicit preflight') from None
    custom = settings.profile == 'azure_sql_custom'
    fixture = {table.name.casefold() for table in TABLES}
    approved = {}
    for full, table in tables.items():
        if table['schema'].casefold() in {s.casefold() for s in settings.schemas} and (custom or table['name'].casefold() in fixture):
            key = full.casefold()
            if key in approved:
                raise SQLGateError('Ambiguous discovered object names')
            approved[key] = table
    if not approved or (not custom and len(approved) != len(fixture)):
        raise SQLGateError('The complete approved synthetic fixture must be visible')
    return approved


def prepare_read(sql, settings, parameters=()):
    """Return validated, qualified, dialect-rendered SQL capped at max_rows+1."""
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 12000 or '\x00' in sql:
        raise SQLGateError('Expected one bounded SQL read query')
    try:
        import sqlglot
        import logging
        logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope, Scope
        from sqlglot.optimizer.qualify import qualify
    except ImportError:
        raise SQLGateError('Install the pinned SQLGlot dependency; query execution is disabled') from None
    dialect = 'tsql' if settings.dialect == 'sqlserver' else 'sqlite'
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level='RAISE')
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise SQLGateError('Only a single supported SELECT/CTE read is allowed')
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > 1000 or max((node.depth for node in nodes), default=0) > 40:
            raise SQLGateError('SQL expression complexity exceeds the demo limit')
        for node in nodes:
            name = type(node).__name__
            if name == 'Anonymous':
                if node.name.lower() not in ANONYMOUS[settings.dialect]:
                    raise SQLGateError('Unsupported SQL function')
            elif name not in NODES:
                raise SQLGateError('Unsupported SQL construct or function')
            if isinstance(node, exp.Select) and any(node.args.get(k) for k in (
                'into', 'locks', 'hint', 'operation_modifiers', 'options', 'offset', 'connect', 'match', 'laterals')):
                raise SQLGateError('Read queries cannot include setup, locking or extended options')
            if isinstance(node, exp.With) and node.args.get('recursive'):
                raise SQLGateError('Recursive CTEs are unsupported')
            if isinstance(node, exp.Table) and any(node.args.get(k) for k in ('catalog', 'hints', 'pivots', 'version', 'sample')):
                raise SQLGateError('External objects, hints and extended table syntax are unsupported')
            if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
                raise SQLGateError('Table functions and external access are unsupported')
            if isinstance(node, exp.Column) and (node.args.get('db') or node.args.get('catalog')):
                raise SQLGateError('Use a local table alias for qualified column references')
            if isinstance(node, exp.Var) and str(node.this).upper() not in DATE_UNITS:
                raise SQLGateError('Unsupported date unit')
            if isinstance(node, exp.Identifier) and (not node.name or len(node.name) > 128 or any(ord(c)<32 for c in node.name)):
                raise SQLGateError('Unsupported identifier')
            if isinstance(node, exp.Limit):
                value = node.expression
                if (not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit()
                        or not 1 <= int(value.this) <= 100000 or node.args.get('limit_options')):
                    raise SQLGateError('Use a positive constant TOP/LIMIT without percent/ties')
            if isinstance(node, exp.DataType) and str(node.this).endswith('USERDEFINED'):
                raise SQLGateError('User-defined types are unsupported')
        placeholders = list(tree.find_all(exp.Placeholder))
        if any(p.this for p in placeholders) or len(placeholders) != len(parameters):
            raise SQLGateError('Application parameters must use matching positional placeholders')
        allowed = approved_metadata(settings)
        schema = {}
        for table in allowed.values():
            schema.setdefault(table['schema'], {})[table['name']] = {c['name']: 'UNKNOWN' for c in table['columns']}
        # Scope resolution distinguishes CTE/derived sources from physical table reads.
        for scope in traverse_scope(tree):
            for _, (_, source) in scope.selected_sources.items():
                if isinstance(source, Scope):
                    continue
                if not isinstance(source, exp.Table):
                    raise SQLGateError('Unresolved SQL source')
                if settings.profile == 'azure_sql_custom' and not source.db:
                    candidates=[t for t in allowed.values() if t['name'].casefold()==source.name.casefold()]
                    if len(candidates)!=1:raise SQLGateError('Qualify an unknown or ambiguous table with its schema')
                    owner=candidates[0]['schema']
                else:
                    owner = source.db or settings.schemas[0]
                full = f'{owner}.{source.name}'.casefold()
                if full not in allowed:
                    raise SQLGateError('Query references an object outside the discovered runtime allowlist')
                verified = allowed[full]
                source.set('db', exp.to_identifier(verified['schema'], quoted=True))
                source.set('this', exp.to_identifier(verified['name'], quoted=True))
        # Reject uncertain/unknown/ambiguous columns, including correlated references.
        tree = qualify(tree, dialect=dialect, schema=schema, validate_qualify_columns=True,
                       allow_partial_qualification=False, infer_schema=False)
        limit = tree.args.get('limit')
        cap = settings.max_rows + 1
        if limit is None or int(limit.expression.this) > cap:
            tree = tree.limit(cap)
        # Render the AST, not original text: comments/unsupported lexical fragments don't reach SQL.
        return tree.sql(dialect=dialect, unsupported_level='RAISE', comments=False)
    except SQLGateError:
        raise
    except Exception:
        raise SQLGateError('SQL could not be resolved safely for the configured dialect') from None
