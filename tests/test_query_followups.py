"""Exercise real adapter SQL and the structured model contract, without network."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import sqlite3
import unittest
from unittest.mock import patch

from src.access import AccessPolicy,load_access_settings
from src.frontdesk import fd_feedback
from src.llm_client import LLMError
from src.service import SessionService
from src.settings import load_settings,configured
from src.sql_gate import SQLGateError
from src.discovery import refresh
from src.database import Database
from synthetic_demo.seed import seed_sqlite

QUESTION12='Which assets have no recorded failure events in the last 12 months?'
QUESTION6='Which assets have no recorded failure events in the last 6 months?'
SQL12="""SELECT e.equipment_id, e.asset_code FROM equipment e
WHERE NOT EXISTS (SELECT 1 FROM failure_events f WHERE f.equipment_id=e.equipment_id
AND f.occurred_at >= date('2026-10-01','-12 months')) ORDER BY e.equipment_id"""
SQL6=SQL12.replace('-12 months','-6 months')


class FollowupTests(unittest.TestCase):
    def setUp(self):
        folder=self.enterContext(TemporaryDirectory());root=Path(folder)
        self.settings=load_settings({'DB_SQLITE_PATH':str(root/'fixture.sqlite3'),'DB_SCHEMA_JSON':str(root/'schema.json')})
        seed_sqlite(self.settings.sqlite_path)
        # Only the disposable local test fixture: one failure between 6 and 12
        # months makes stale results distinguishable from the revised query.
        with sqlite3.connect(self.settings.sqlite_path) as fixture:
            fixture.execute("INSERT INTO failure_events (failure_id,equipment_id,work_order_id,occurred_at,failure_mode) VALUES (1000,57,NULL,'2026-02-01T00:00:00','mechanical')")
        refresh(self.settings)
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}))
        self.service=SessionService(self.policy,settings=self.settings,mode='live')
        self.request=SimpleNamespace(username='demo',session_hash='owned')
        self.session=self.service.submit(QUESTION12,self.request)
        with self.service.operation(self.session):rows,columns=self.service._query(self.session,SQL12)
        self.initial_rows=rows
        self.session.state.update(finished=True,final_q=QUESTION12,sql=SQL12,rows=rows,columns=columns,
            selected={'tables':['main.equipment','main.failure_events'],'columns':[]})
        self.session.state.pop('pending_message')
        self.model=self.enterContext(patch('src.frontdesk.LLMClient'))
        self.client=self.model.return_value
        self.enterContext(patch('src.db_connection.connect_sqlserver',side_effect=AssertionError('Unexpected Azure call')))

    def response(self,action='propose',sql=None,question=QUESTION6,reply='Use a six-month window.'):
        self.client.chat_json.return_value={'action':action,'reply':reply,'sql':[SQL6] if sql is None and action=='propose' else sql or [],'question':question}

    def turn(self,text):
        self.service.submit(text,self.request)
        return self.service.advance(self.request)

    def test_exact_reported_short_and_explicit_requests_propose_then_execute_new_results(self):
        self.response()
        self.turn('how about last 6 months')
        state=self.session.state
        self.assertEqual(state['pending_sql'],SQL6)
        self.assertEqual(state['query_attempts'],1)
        self.assertEqual(state['rows'],self.initial_rows)
        self.assertEqual(state['final_q'],QUESTION12)
        self.assertIn('/execute',self.session.history[-1]['content'])
        self.assertEqual(state['stage'],'Query revision awaiting confirmation')
        self.turn('change the query so that It shows the results for the last 6 months')
        self.assertEqual(state['pending_question'],QUESTION6)
        self.assertEqual(state['query_attempts'],1)
        self.turn('/execute')
        with configured(self.settings):expected=Database(self.settings).query(SQL6)
        self.assertEqual(state['rows'],expected[0])
        self.assertGreater(len(state['rows']),len(self.initial_rows))
        self.assertEqual(state['sql'],SQL6)
        self.assertEqual(state['final_q'],QUESTION6)
        self.assertEqual(state['query_attempts'],2)
        self.assertEqual(state['stage'],'Result ready')
        self.assertNotIn('pending_sql',state)
        self.response('explain',question=QUESTION12,reply='These are assets without failures during the six-month window.')
        self.turn('What does this result mean?')
        data=json.loads(self.client.chat_json.call_args.args[0][1]['content'])
        self.assertEqual(data['final_question']['value'],QUESTION6)
        self.assertEqual(data['sql_query']['value'],SQL6)
        self.assertEqual(state['final_q'],QUESTION6)
        self.assertEqual(state['query_attempts'],2)

    def test_latest_request_is_separate_and_never_lost_to_old_history_truncation(self):
        history=[{'role':'assistant','content':'Old twelve-month explanation. '*1000},
                 {'role':'user','content':'und die letzten 6 Monate?'}]
        self.response(reply='Vorschlag für die letzten sechs Monate.')
        with configured(self.settings):decision,_=fd_feedback(history,QUESTION12,SQL12,[],self.session.state['selected'])
        data=json.loads(self.client.chat_json.call_args.args[0][1]['content'])
        self.assertEqual(data['latest_request']['value'],'und die letzten 6 Monate?')
        self.assertIn('und die letzten 6 Monate?',data['chat_history_text']['value'])
        self.assertEqual(decision.reply,'Vorschlag für die letzten sechs Monate.')
        self.assertEqual(decision.sql,SQL6)
        self.assertEqual(data['dialect']['value'],'sqlite')

    def test_clarification_or_failure_invalidates_old_proposal(self):
        self.response();self.turn('how about last 6 months')
        self.response('clarify',reply='Which time window should I use?')
        self.turn('Use another period')
        self.assertNotIn('pending_sql',self.session.state)
        self.turn('/execute')
        self.assertEqual(self.session.state['query_attempts'],1)
        self.assertIn('No pending query',self.session.history[-1]['content'])

    def test_write_unknown_table_and_malformed_actions_cannot_arm_a_query(self):
        for sql in ['DELETE FROM equipment','SELECT * FROM missing_table']:
            self.response(sql=[sql]);self.turn('change the query')
            self.assertNotIn('pending_sql',self.session.state)
            self.assertEqual(self.session.state['query_attempts'],1)
        for response in [dict(action='execute',reply='Run',sql=[SQL6],question=QUESTION6),
                         dict(action='propose',reply='Run',sql=[SQL6,SQL12],question=QUESTION6),
                         dict(action='propose',reply='Run',sql=[],question=QUESTION6),
                         dict(action='explain',reply='Info',sql=[SQL6],question=QUESTION6)]:
            self.client.chat_json.return_value=response
            with configured(self.settings),self.assertRaises(LLMError):
                fd_feedback([{'role':'user','content':'change'}],QUESTION12,SQL12,[],self.session.state['selected'])
        with configured(self.settings):
            self.assertEqual(Database(self.settings).query('SELECT COUNT(*) FROM equipment')[0],[(60,)])

    def test_invalid_or_superseded_proposal_cannot_execute_stale_sql(self):
        self.response();self.turn('how about last 6 months')
        self.client.chat_json.side_effect=LLMError('malformed','Invalid model response')
        self.turn('change it again')
        self.assertNotIn('pending_sql',self.session.state)
        self.turn('/execute')
        self.assertEqual(self.session.state['query_attempts'],1)

    def test_approved_query_is_revalidated_and_consumed_once_on_failure(self):
        self.response();self.turn('how about last 6 months')
        with patch('src.service.prepare_read',side_effect=SQLGateError('Blocked at confirmation')):
            self.turn('/execute')
        self.assertEqual(self.session.state['final_q'],QUESTION12)
        self.assertEqual(self.session.state['rows'],self.initial_rows)
        self.assertNotIn('pending_sql',self.session.state)
        self.turn('/execute')
        self.assertIn('No pending query',self.session.history[-1]['content'])
