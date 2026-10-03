"""AST attacks, real SQLite, cancellation, server quotas and session isolation."""
import asyncio
import base64
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock,patch

from synthetic_demo.seed import seed_sqlite
from synthetic_demo.schema import TABLES
from synthetic_demo.questions import reference_cases
from src.access import (AccessPolicy,AccessError,SecureASGI,load_access_settings,private_scope,
                        charge_model_attempt)
from src.database import Database,DatabaseError
from src.discovery import refresh
from src.llm_client import new_budget,LLMClient,LLMError
from src.logger import PipelineLogger,use_logger,global_logger,redact
from src.service import SessionService,display
from src.settings import load_settings,SettingsError
from src.sql_gate import prepare_read,SQLGateError


class GateTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.settings=load_settings({'DB_SQLITE_PATH':str(root/'fixture.sqlite3'),'DB_SCHEMA_JSON':str(root/'schema.json')})
        seed_sqlite(self.settings.sqlite_path);refresh(self.settings)

    def test_all_grounded_queries_execute_unchanged_results_with_gate(self):
        trusted=sqlite3.connect(self.settings.sqlite_path)
        self.addCleanup(trusted.close)
        for case in reference_cases():
            if case['sql_sqlite']:
                expected=trusted.execute(case['sql_sqlite']).fetchall()
                actual,_=Database(self.settings).query(case['sql_sqlite'])
                self.assertEqual(actual,expected,case['id'])

    def test_attack_matrix_refused_before_opening_database(self):
        attacks=["DELETE FROM equipment","SELECT 1; DELETE FROM equipment","SELECT 1 INTO equipment",
            "WITH x AS (DELETE FROM equipment RETURNING *) SELECT * FROM x","EXEC xp_cmdshell 'whoami'",
            "GRANT SELECT ON equipment TO public","DROP TABLE equipment","PRAGMA query_only=OFF",
            "ATTACH DATABASE 'outside.sqlite3' AS other","SELECT * FROM sqlite_master","SELECT * FROM temp.equipment",
            "SELECT * FROM db.main.equipment","SELECT * FROM OPENROWSET('x','y','z')",
            "SELECT load_extension('evil')","SELECT readfile('/etc/passwd')","SELECT randomblob(100000000)",
            "SELECT dbo.fn(1)","SELECT @@VERSION","SELECT * FROM equipment WITH (NOLOCK)",
            "SELECT * FROM equipment FOR UPDATE","SELECT * FROM equipment LIMIT 2 OFFSET 1",
            "SELECT * FROM equipment WHERE asset_code='x' UNION SELECT name FROM sqlite_master",
            "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x",
            "SELECT missing FROM equipment","SELECT equipment_id FROM equipment e JOIN work_orders w ON e.equipment_id=w.equipment_id",
            "SELECT * FROM [equipment; DROP TABLE sites]","SELECT (", "SELECT 1; ; SELECT 2"]
        for sql in attacks:
            db=Database(self.settings)
            with patch.object(db,'_open',side_effect=AssertionError('unsafe SQL reached connection')) as opened:
                with self.subTest(sql=sql),self.assertRaises(DatabaseError):db.query(sql)
                opened.assert_not_called()

    def test_cte_comments_bracket_identifiers_correlated_subquery_and_limits(self):
        sql="/* SELECT INTO is only a comment */ WITH x AS (SELECT asset_code FROM [main].[equipment]) SELECT * FROM x -- ignored\n"
        rows,columns=Database(replace(self.settings,max_rows=100)).query(sql)
        self.assertEqual(len(rows),60);self.assertEqual(columns,['asset_code'])
        prepared=prepare_read('SELECT COUNT(*) AS n FROM equipment',replace(self.settings,max_rows=3))
        self.assertIn('LIMIT 4',prepared)
        self.assertEqual(Database(replace(self.settings,max_rows=3)).query('SELECT COUNT(*) AS n FROM equipment')[0],[(60,)])
        self.assertEqual(len(Database(self.settings).query('SELECT asset_code FROM equipment LIMIT 2')[0]),2)
        params="x'; DELETE FROM equipment; --"
        self.assertEqual(Database(self.settings).query('SELECT asset_code FROM equipment WHERE asset_code=?',(params,))[0],[])
        with self.assertRaises(SQLGateError):prepare_read('SELECT ?',self.settings,())

    def test_sqlserver_reference_and_complex_patterns_only_mock_metadata(self):
        settings=load_settings({'DB_PROFILE':'azure_sql','DB_SERVER':'example.database.windows.net',
            'DB_NAME':'dbmind_synthetic_test','DB_AUTH':'managed_identity','DB_SCHEMA_SCOPE':'demo','DB_SCHEMA_JSON':'var/synthetic/mock.json'})
        metadata={f'demo.{t.name}':{'schema':'demo','name':t.name,'columns':[{'name':c.name} for c in t.columns]} for t in TABLES}
        with patch('src.sql_gate.read_snapshot',return_value={'metadata':{'tables':metadata}}):
            for case in reference_cases():
                if case['sql_sqlserver']:
                    rendered=prepare_read(case['sql_sqlserver'],settings)
                    self.assertIn('TOP ',rendered)
            rendered=prepare_read('WITH x AS (SELECT asset_code FROM [demo].[equipment]) SELECT * FROM x',settings)
            self.assertTrue(rendered.startswith('WITH '));self.assertIn('SELECT TOP 1001',rendered)
            for sql in ['SELECT TOP 2 PERCENT * FROM demo.equipment','SELECT TOP 2 WITH TIES * FROM demo.equipment',
                'SELECT * FROM [server].[db].[demo].[equipment]','SELECT * FROM [demo].[equipment] OPTION (MAXDOP 0)',
                'SELECT * FROM dbo.equipment','SELECT NEXT VALUE FOR demo.seq']:
                with self.subTest(sql=sql),self.assertRaises(SQLGateError):prepare_read(sql,settings)

    def test_missing_expired_snapshot_unknown_columns_and_parser_fail_closed(self):
        with self.assertRaises(SQLGateError):prepare_read('SELECT 1',replace(self.settings,schema_json=self.settings.schema_json.with_name('missing.json')))
        with patch('src.sql_gate.read_snapshot',side_effect=__import__('src.snapshots',fromlist=['SnapshotError']).SnapshotError('expired')):
            with self.assertRaises(SQLGateError):prepare_read('SELECT 1',self.settings)
        with self.assertRaises(SQLGateError):prepare_read('SELECT equipment_id FROM equipment WHERE 1=1; SELECT 2',self.settings)

    def test_sqlite_timeout_cancels_heavy_query_and_fresh_read_still_works(self):
        start=time.monotonic()
        with self.assertRaises(DatabaseError) as caught:
            Database(replace(self.settings,query_timeout=.005)).query('SELECT SUM(a.labor_minutes*b.labor_minutes) FROM work_orders a CROSS JOIN work_orders b')
        self.assertEqual(caught.exception.code,'timeout');self.assertLess(time.monotonic()-start,1)
        self.assertEqual(Database(self.settings).query('SELECT COUNT(*) FROM equipment')[0],[(60,)])

    def test_sqlserver_deadline_calls_cancel_and_closes_when_worker_finishes(self):
        connection=MagicMock();cursor=connection.cursor.return_value
        release=threading.Event();entered=threading.Event()
        cursor.execute.side_effect=lambda *args:(entered.set(),release.wait(1))
        cursor.description=[('n',)];cursor.fetchmany.return_value=[]
        settings=replace(self.settings,dialect='sqlserver',query_timeout=.05)
        database=Database(settings,connector=lambda _:connection)
        try:
            with patch('src.sql_gate.prepare_read',return_value='SELECT TOP 2 1 AS n'),patch('src.database.require_read_principal'):
                with self.assertRaises(DatabaseError) as caught:database.query('SELECT 1')
            self.assertEqual(caught.exception.code,'timeout')
            for _ in range(20):
                if cursor.cancel.called:break
                time.sleep(.005)
            cursor.cancel.assert_called_once()
        finally:release.set()
        for _ in range(30):
            if connection.close.called:break
            time.sleep(.005)
        connection.close.assert_called_once()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.now=100.
        self.settings=load_access_settings({'APP_AUTH':'alice:offline-password-a,bob:offline-password-b'})
        self.policy=AccessPolicy(self.settings,clock=lambda:self.now)

    def test_authentication_configuration_secret_repr_and_anonymous_denial(self):
        valid='Basic '+base64.b64encode(b'alice:offline-password-a').decode()
        self.assertEqual(self.policy.authenticate(valid),'alice')
        for header in ['', 'Bearer x','Basic !!!','Basic '+base64.b64encode(b'alice:wrong').decode()]:
            self.assertIsNone(self.policy.authenticate(header))
        self.assertNotIn('offline-password',repr(self.settings))
        with self.assertRaises(SettingsError):load_access_settings({'APP_USERNAME':'alice','APP_PASSWORD':'short'})
        with self.assertRaises(AccessError):self.policy.session(None,'session')
        with self.assertRaises(AccessError):charge_model_attempt()

    def test_account_quotas_survive_new_sessions_resets_and_expire(self):
        self.policy.begin_question('alice');self.policy.begin_question('alice')
        self.policy.session('alice','fresh-session')
        with self.assertRaises(AccessError):self.policy.begin_question('alice')
        self.now+=3601;self.policy.begin_question('alice')
        self.policy.begin_question('bob')
        self.policy.session('alice','one')
        with self.assertRaises(AccessError):self.policy.session('bob','one')

    def test_global_and_account_model_limits_charge_retries_atomically(self):
        policy=AccessPolicy(replace(self.settings,calls_hour=2,global_calls_day=3),clock=lambda:self.now)
        with private_scope(policy,'alice'):
            charge_model_attempt();charge_model_attempt()
            with self.assertRaises(AccessError):charge_model_attempt()
        with private_scope(policy,'bob'):
            charge_model_attempt()
            with self.assertRaises(AccessError):charge_model_attempt()
        self.assertEqual(len(policy._events[('global_calls',)]),3)

    def test_session_lifetime_capacity_and_unique_loggers(self):
        left=self.policy.session('alice','left');right=self.policy.session('bob','right')
        self.assertIsNot(left.logger,right.logger)
        left.state['question']='private';self.assertNotIn('question',right.state)
        with use_logger(left.logger):global_logger.log('Question','secret raw prompt')
        self.assertEqual(right.logger.lines,[])
        self.assertNotIn('secret',repr(left.logger.lines))
        for i in range(3):self.policy.session('alice',f'other{i}')
        with self.assertRaises(AccessError):self.policy.session('alice','over-cap')
        self.now+=3601;self.policy.session('alice','replacement')
        self.assertNotIn('left',self.policy._sessions)

    def test_safe_display_redaction_and_no_raw_diagnostics(self):
        raw='<script>alert(1)</script> ``` SERVER=company.internal;PWD=secret API_KEY=key https://private.local/data'
        text=display(raw)
        self.assertNotIn('<script>',text);self.assertNotIn('company.internal',text);self.assertNotIn('PWD=secret',text)
        logger=PipelineLogger();logger.log('Results',raw)
        self.assertNotIn('script',json.dumps(logger.lines));self.assertNotIn('secret',json.dumps(logger.lines))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'alice:offline-password-a,bob:offline-password-b'}))
        folder=self.enterContext(tempfile.TemporaryDirectory())
        root=Path(folder)
        settings=load_settings({'DB_SQLITE_PATH':str(root/'fixture.sqlite3'),'DB_SCHEMA_JSON':str(root/'schema.json')})
        seed_sqlite(settings.sqlite_path);refresh(settings)
        self.service=SessionService(self.policy,database_factory=MagicMock(),settings=settings,mode='live')
        self.request=NS(username='alice',session_hash='owned')

    def test_direct_calls_without_authenticated_request_never_reach_stages(self):
        with patch('src.frontdesk.fd_chat_step',side_effect=AssertionError('model called')):
            for request in [None,NS(username=None,session_hash='x'),NS(username='unknown',session_hash='x')]:
                with self.assertRaises(AccessError):self.service.submit('hello',request)
            with self.assertRaises(AccessError):self.service.advance(self.request)
        self.service.database_factory.assert_not_called()

    def test_no_recovery_for_empty_result_and_exactly_one_for_query_error(self):
        database=MagicMock();database.query.return_value=([],['incident_id'])
        self.service.database_factory.return_value=database
        for failure in [False,True]:
            if failure:
                self.service.reset(self.request)
                database.query.side_effect=[DatabaseError('query','safe syntax error'),([],['incident_id'])]
            session=self.service.submit('List safety incidents',self.request)
            with patch('src.frontdesk.fd_chat_step',return_value=('','List safety incidents',[])), \
                patch('src.retrieval.load_schema_text',return_value='synthetic'), \
                patch('src.retrieval.retrieve_schema_with_llm',return_value={'selected':{'tables':['main.safety_incidents']}}), \
                patch('src.query_generator.generate_sql_query',return_value='<FINAL_ANSWER>SELECT incident_id FROM safety_incidents</FINAL_ANSWER>'), \
                patch('src.analyzer.analyze_failure',return_value=('SELECT incident_id FROM safety_incidents','fixed')) as analyzer:
                self.service.advance(self.request)
                self.assertEqual(analyzer.call_count,int(failure))
            self.assertTrue(session.state['finished'])
            self.assertIn('successful empty result',repr(session.history))
            self.assertEqual(session.state['query_attempts'],1+int(failure))

    def test_feedback_proposal_needs_separate_confirmation_and_revalidation(self):
        session=self.service.submit('question',self.request)
        session.state.update(finished=True,final_q='question',sql='SELECT 1',rows=[],columns=['n'],selected={})
        with patch('src.frontdesk.fd_feedback',return_value=('<RUN_SQL>SELECT 1</RUN_SQL>',False,[])),patch('src.service.prepare_read',return_value='SELECT 1'):
            self.service.advance(self.request)
        self.service.database_factory.assert_not_called()
        self.assertEqual(session.state['pending_sql'],'SELECT 1')
        self.service.database_factory.return_value.query.return_value=([(1,)],['n'])
        self.service.submit('/execute',self.request);self.service.advance(self.request)
        self.service.database_factory.return_value.query.assert_called_once()
        self.assertIsNone(session.state.get('pending_sql'))
        self.service.submit('/execute',self.request);self.service.advance(self.request)
        self.assertEqual(self.service.database_factory.return_value.query.call_count,1)

    def test_cross_user_replay_reset_and_query_cap(self):
        session=self.service.submit('private',self.request)
        with self.assertRaises(AccessError):self.service.session(NS(username='bob',session_hash='owned'))
        budget=session.state['llm_budget']
        session.state['query_attempts']=3
        with self.assertRaises(AccessError):self.service._query(session,'SELECT 1')
        self.service.reset(self.request);self.assertTrue(budget.cancelled)
        self.service.submit('new',self.request);self.service.reset(self.request)
        with self.assertRaises(AccessError):self.service.submit('quota cannot be reset',self.request)
        self.service.database_factory.assert_not_called()


if __name__=='__main__':unittest.main()
