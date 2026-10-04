"""Custom Azure profiles: catalogs/permissions simulated, SQL reads executed locally."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.access import AccessPolicy, load_access_settings
from src.catalog import OBJECTS_SQL, COLUMNS_SQL, FOREIGN_KEYS_SQL, INDEXES_SQL, sqlserver_catalog
from src.database import Database
from src.db_connection import require_custom_read_principal
from src.discovery import refresh, preflight
from src.frontdesk import FeedbackDecision
from src.service import SessionService
from src.settings import load_settings, configured, SettingsError
from src.snapshots import read_snapshot, SnapshotError
from src.sql_gate import prepare_read, SQLGateError
from src.setup_database import setup, private_environment
from tests.test_database import READ_PRINCIPAL

CUSTOM={'DB_PROFILE':'azure_sql_custom','DB_DIALECT':'sqlserver',
        'DB_SERVER':'offline.database.windows.net','DB_NAME':'RetailAnalytics',
        'DB_SCHEMA_SCOPE':'sales,hr','DB_SCHEMA_JSON':'var/custom/schema.json',
        'DB_AUTH':'sql_password','DB_USER':'app_reader','DB_PASSWORD':'offline-password'}
OBJECTS=[('sales','customers','U',1,0,0,0,0,0,0),
         ('sales','orders','U',1,0,0,0,0,0,0),
         ('hr','customers','U',1,0,0,0,0,0,0)]


def permission_cursor(settings,objects=OBJECTS,modules=()):
    cursor=MagicMock()
    cursor.fetchone.side_effect=[READ_PRINCIPAL,(settings.database,0)]+[(0,)*6]*len(settings.schemas)
    cursor.fetchall.return_value=[('guest',0)]
    cursor.fetchmany.side_effect=[list(objects),list(modules)]
    return cursor


class CustomPermissionTests(unittest.TestCase):
    def setUp(self):self.settings=load_settings(CUSTOM)

    def test_restricted_custom_role_reader_accepted_without_demo_role_or_tables(self):
        cursor=permission_cursor(self.settings);require_custom_read_principal(cursor,self.settings)
        first=cursor.execute.call_args_list[0]
        self.assertEqual(first.args[1],('sales','sales'))
        self.assertNotIn('demo.',str(cursor.execute.call_args_list))
        self.assertIn("name, 'COLUMN'",str(cursor.execute.call_args_list))

    def test_every_object_write_control_unknown_and_column_write_rejected(self):
        for index in range(4,10):
            for value in [1,None]:
                with self.subTest(index=index,value=value):
                    obj=list(OBJECTS[0]);obj[index]=value
                    with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(self.settings,[tuple(obj)]),self.settings)
        outside=('private','ledger','U',0,0,1,0,0,0,0)
        with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(self.settings,OBJECTS+[outside]),self.settings)

    def test_elevation_impersonation_missing_select_and_bad_inventory_rejected(self):
        for index in range(1,17):
            cursor=permission_cursor(self.settings);principal=list(READ_PRINCIPAL);principal[index]=1
            cursor.fetchone.side_effect=[tuple(principal)]
            with self.subTest(index=index),self.assertRaises(PermissionError):require_custom_read_principal(cursor,self.settings)
        for value in [1,None]:
            cursor=permission_cursor(self.settings);cursor.fetchall.return_value=[('another_user',value)]
            with self.assertRaises(PermissionError):require_custom_read_principal(cursor,self.settings)
        for objects in [[],OBJECTS*334,[OBJECTS[0][:-1]],[('sales','customers','U',0,0,0,0,0,0,0)]]:
            with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(self.settings,objects),self.settings)

    def test_schema_module_and_database_unknowns_fail_closed(self):
        for row in [(1,0,0,0,0,0),(0,None,0,0,0,0)]:
            cursor=permission_cursor(self.settings);cursor.fetchone.side_effect=[READ_PRINCIPAL,(self.settings.database,0),row]
            with self.assertRaises(PermissionError):require_custom_read_principal(cursor,self.settings)
        for modules in [[(1,0,0)],[(0,1,0)],[(0,0,None)]]:
            with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(self.settings,modules=modules),self.settings)
        for row in [('WrongDatabase',0),(self.settings.database,None),(self.settings.database,1)]:
            cursor=permission_cursor(self.settings);cursor.fetchone.side_effect=[READ_PRINCIPAL,row]
            with self.assertRaises(PermissionError):require_custom_read_principal(cursor,self.settings)

    def test_explicit_table_scope_missing_or_read_only_views_not_substituted(self):
        settings=replace(self.settings,table_scope=('sales.orders',))
        require_custom_read_principal(permission_cursor(settings),settings)
        with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(settings,OBJECTS[:1]),settings)
        with self.assertRaises(PermissionError):require_custom_read_principal(permission_cursor(settings,[('sales','orders','V',1,0,0,0,0,0,0)]),settings)


class LocalAzureCursor:
    """ODBC-shaped test double: real SQLite reads after T-SQL rendering/guarding."""
    def __init__(self,connection):self.connection=connection;self.rows=[];self.description=[]
    def close(self):pass
    def cancel(self):pass
    def execute(self,sql,parameters=()):
        import sqlglot
        if sql.startswith('SELECT CURRENT_USER'):self.rows=[READ_PRINCIPAL]
        elif 'FROM sys.database_principals' in sql:self.rows=[('guest',0)]
        elif sql.startswith('SELECT DB_NAME()'):self.rows=[('RetailAnalytics',0)]
        elif "'SCHEMA'" in sql and 'sys.' not in sql:self.rows=[(0,)*6]
        elif "o.type IN ('U','V')" in sql:self.rows=OBJECTS.copy()
        elif "o.type IN ('P','PC'" in sql:self.rows=[]
        else:
            sql=sql.replace('NEWID()','random()').replace('nvarchar(max)','varchar(10000)')
            rendered=sqlglot.transpile(sql,read='tsql',write='sqlite')[0]
            result=self.connection.execute(rendered,parameters)
            self.rows=result.fetchall();self.description=result.description
        return self
    def fetchone(self):return self.rows.pop(0) if self.rows else None
    def fetchall(self):rows=self.rows;self.rows=[];return rows
    def fetchmany(self,size=100):rows=self.rows[:size];self.rows=self.rows[size:];return rows


class LocalAzureConnection:
    def __init__(self,root):
        self.connection=sqlite3.connect(':memory:')
        self.connection.create_function('LEFT',2,lambda value,n:value[:n] if value is not None else None)
        for schema in ('sales','hr'):
            self.connection.execute(f'ATTACH DATABASE ? AS {schema}',((root/f'{schema}.sqlite3').as_uri()+'?mode=ro',))
        self.connection.execute('PRAGMA query_only=ON')
    def cursor(self):return LocalAzureCursor(self.connection)
    def close(self):self.connection.close()


def table(schema,name,columns,parent=None):
    result={'schema':schema,'name':name,'description':'Retail records','primary_key':['id'],'indexes':[],
            'columns':[{'name':c,'type':'int' if c!='name' else 'nvarchar','length':160,'nullable':False} for c in columns],
            'foreign_keys':[]}
    if parent:result['foreign_keys']=[{'name':'customer_fk','from_table':schema+'.'+name,'from_columns':['customer_id'],
        'to_table':'sales.customers','to_columns':['id']}]
    return result


class CustomPipelineTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.settings=load_settings({**CUSTOM,'DB_SCHEMA_JSON':str(self.root/'snapshot.json')})
        with sqlite3.connect(self.root/'sales.sqlite3') as db:
            db.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO customers VALUES(1,'Ada'),(2,'Sam'); CREATE TABLE orders(id INTEGER PRIMARY KEY,customer_id INTEGER,total INTEGER,FOREIGN KEY(customer_id) REFERENCES customers(id)); INSERT INTO orders VALUES(1,1,50),(2,1,80),(3,2,20);")
        with sqlite3.connect(self.root/'hr.sqlite3') as db:db.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO customers VALUES(9,'Internal');")
        self.tables={'sales.customers':table('sales','customers',['id','name']),
            'sales.orders':table('sales','orders',['id','customer_id','total'],True),
            'hr.customers':table('hr','customers',['id','name'])}
        self.connector=lambda settings:LocalAzureConnection(self.root)
        self.enterContext(patch('src.database.connect_sqlserver',self.connector))
        self.catalog=self.enterContext(patch('src.discovery.discover_catalog',return_value=(self.tables,[])))
        refresh(self.settings)

    def test_setup_metadata_samples_snapshot_and_real_join_read(self):
        report=setup(self.settings)
        self.assertEqual(report['tables'],3);self.assertTrue(report['sampling_complete'])
        snapshot=read_snapshot(self.settings)
        self.assertIs(snapshot['synthetic_only'],False)
        self.assertEqual(len(snapshot['samples']['sales.orders']['rows']),3)
        self.assertEqual(snapshot['metadata']['tables']['sales.orders']['foreign_keys'][0]['to_table'],'sales.customers')
        self.assertEqual(snapshot['identity']['profile'],'azure_sql_custom')
        rows,columns=Database(self.settings).query('SELECT c.name,SUM(o.total) AS amount FROM sales.customers c JOIN sales.orders o ON o.customer_id=c.id GROUP BY c.name ORDER BY c.name')
        self.assertEqual(rows,[('Ada',130),('Sam',20)])
        self.assertEqual(columns,['name','amount'])
        self.assertTrue(self.settings.schema_json.with_suffix('.index.json').exists())

    def test_read_gate_unknown_writes_scope_and_ambiguous_names(self):
        for sql in ['SELECT * FROM customers','SELECT * FROM private.payroll','UPDATE sales.orders SET total=0',
                    'SELECT * FROM sales.unknown','SELECT * FROM RetailAnalytics.sales.orders','SELECT missing FROM sales.orders']:
            with self.subTest(sql=sql),self.assertRaises(SQLGateError):prepare_read(sql,self.settings)
        self.assertIn('[sales].[orders]',prepare_read('SELECT total FROM orders',self.settings))
        with self.assertRaises(SnapshotError):read_snapshot(replace(self.settings,database='AnotherDatabase'))
        with self.assertRaises(SnapshotError):read_snapshot(replace(self.settings,table_scope=('sales.orders',)))
        with self.assertRaises(SnapshotError):read_snapshot(replace(self.settings,profile='azure_sql'))

    def test_live_pipeline_initial_and_reviewed_followup_use_custom_tables(self):
        policy=AccessPolicy(load_access_settings({'APP_AUTH':'tester:offline-password'}))
        service=SessionService(policy,settings=self.settings,mode='live')
        request=SimpleNamespace(username='tester',session_hash='retail')
        sql='SELECT id,total FROM sales.orders WHERE total>=50 ORDER BY id'
        with patch('src.frontdesk.fd_chat_step',return_value=('Confirmed','Show orders of at least 50',[])), \
             patch('src.retrieval.retrieve_schema_with_llm',return_value={'selected':{'tables':['sales.orders'],'columns':[]},'complexity':'Easy'}), \
             patch('src.query_generator.generate_sql_query',return_value='<FINAL_ANSWER>'+sql+'</FINAL_ANSWER>') as generator:
            service.submit('Orders of at least 50',request);session=service.advance(request)
            self.assertEqual(session.state['rows'],[(1,50),(2,80)])
            new_sql=sql.replace('>=50','>=80')
            generator.return_value='<FINAL_ANSWER>'+new_sql+'</FINAL_ANSWER>'
            with patch('src.frontdesk.fd_feedback',return_value=(FeedbackDecision('propose','Use at least 80','Show orders of at least 80'),[])):
                service.submit('How about at least 80?',request);service.advance(request)
            self.assertEqual(session.state['rows'],[(1,50),(2,80)])
            self.assertEqual(session.state['query_attempts'],1)
            service.submit('/execute',request);service.advance(request)
            self.assertEqual(session.state['rows'],[(2,80)])
            self.assertEqual(session.state['final_q'],'Show orders of at least 80')
        with self.assertRaises(SettingsError):SessionService(policy,settings=self.settings,mode='mock')

    def test_refresh_failure_preserves_cache_and_disabled_samples_supported(self):
        prior=self.settings.schema_json.read_bytes()
        self.catalog.return_value=({},[])
        with self.assertRaises(SnapshotError):refresh(self.settings)
        self.assertEqual(self.settings.schema_json.read_bytes(),prior)
        self.catalog.return_value=(self.tables,[])
        disabled=replace(self.settings,sample_rows=0)
        self.assertEqual(set(s['status'] for s in refresh(disabled)['samples'].values()),{'disabled'})
        self.assertTrue(preflight(disabled)['complete'])

    def test_custom_ui_has_no_maintenance_examples_or_fixed_date(self):
        from app import build_demo
        policy=AccessPolicy(load_access_settings({'APP_AUTH':'tester:offline-password'}))
        demo=build_demo(policy,settings=self.settings,mode='live')
        public=json.dumps(demo.config)
        self.assertNotIn('1 October 2026',public)
        self.assertNotIn('Grounded English and German examples',public)
        self.assertIn('configured database',public)
        self.assertIn('DB-Mind',public)
        demo.close()


class CustomConfigurationTests(unittest.TestCase):
    def test_arbitrary_database_schema_table_config_and_old_profile_stays_strict(self):
        settings=load_settings({**CUSTOM,'DB_TABLE_SCOPE':'sales.orders'})
        self.assertEqual(settings.schemas,('sales','hr'));self.assertEqual(settings.table_scope,('sales.orders',))
        for changes in [{'DB_PROFILE':'azure_sql'},{'DB_SCHEMA_SCOPE':'sys'}, {'DB_SCHEMA_SCOPE':'sales,sales'},
                        {'DB_SCHEMA_SCOPE':''},{'DB_TABLE_SCOPE':','},{'DB_TABLE_SCOPE':'sales.orders,'},{'DB_TABLE_SCOPE':'private.payroll'}, {'DB_TABLE_SCOPE':'orders'},
                        {'DB_NAME':'master'}, {'DB_SERVER':'untrusted.example.com'}, {'DB_TRUST_LOCAL_CERTIFICATE':'true'}]:
            with self.subTest(changes=changes),self.assertRaises(SettingsError):load_settings({**CUSTOM,**changes})

    def test_env_file_literal_secrets_no_shell_expansion_or_errors_leaking_values(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'private.env';path.write_text('DB_PASSWORD=literal$secret!\\nvalue\nDB_NAME=RetailAnalytics\n')
            values=private_environment(path)
            self.assertEqual(values['DB_PASSWORD'],'literal$secret!\\nvalue')
            path.write_text('not-an-env-line-with-private-material')
            with self.assertRaises(SettingsError) as caught:private_environment(path)
            self.assertNotIn('private-material',str(caught.exception))


class CustomCatalogTests(unittest.TestCase):
    def test_catalog_scope_subset_keys_and_parent_metadata_are_derived_from_catalogs(self):
        settings=load_settings({**CUSTOM,'DB_TABLE_SCOPE':'sales.orders'})
        seen=[]
        def records(sql,parameters=()):
            seen.append((sql,parameters))
            if sql==OBJECTS_SQL:
                return [dict(object_id=1,schema_name=parameters[0],table_name='orders',description='Sales orders'),
                        dict(object_id=2,schema_name=parameters[0],table_name='private_notes',description='Do not sample')]
            if sql==COLUMNS_SQL:
                return [dict(name='customer_id',ordinal=1,type='int',type_schema='sys',length=4,precision=10,scale=0,
                        nullable=0,identity=0,computed=0,default=None,computed_definition=None,description='Join key')]
            if sql==FOREIGN_KEYS_SQL:
                return [dict(name='customer_fk',ordinal=1,from_column='customer_id',to_schema='sales',to_name='customers',to_column='id',
                             on_delete='NO_ACTION',on_update='NO_ACTION',disabled=0,untrusted=0)]
            if sql==INDEXES_SQL:return []
            return []
        tables,views=sqlserver_catalog(SimpleNamespace(settings=settings,records=records))
        self.assertEqual(set(tables),{'sales.orders'})
        self.assertEqual(tables['sales.orders']['foreign_keys'][0]['to_table'],'sales.customers')
        self.assertEqual([args for sql,args in seen if sql==OBJECTS_SQL],[('sales',),('hr',)])
        self.assertEqual([args for sql,args in seen if sql==COLUMNS_SQL],[(1,)])

    def test_custom_samples_use_bounded_top_without_population_scan_or_random_sort(self):
        from src.discovery import sample_table
        settings=load_settings(CUSTOM)
        reader=SimpleNamespace(settings=settings,query=MagicMock(return_value=([(1,90)],['id','total'])))
        sample=sample_table(reader,table('sales','orders',['id','total']))
        sql=reader.query.call_args.args[0]
        self.assertIn('TOP (5)',sql);self.assertIn('[sales].[orders]',sql)
        self.assertNotIn('COUNT',sql);self.assertNotIn('ORDER BY',sql)
        self.assertEqual(reader.query.call_count,1)
        self.assertEqual(sample['rows'],[{'id':1,'total':90}])
