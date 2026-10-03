"""End-to-end fixture answers, frozen configuration, probes and real SDK serialization."""
import asyncio
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from synthetic_demo.seed import seed_sqlite
from synthetic_demo.questions import reference_cases
from src.access import AccessPolicy,load_access_settings,operator_scope,SecureASGI
from src.discovery import refresh
from src.service import SessionService
from src.settings import load_settings
from src.llm_settings import load_llm_settings


class FinishedDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory())
        root=Path(self.temp)
        self.settings=load_settings({'DB_SQLITE_PATH':str(root/'fixture.sqlite3'),'DB_SCHEMA_JSON':str(root/'schema.json')})
        seed_sqlite(self.settings.sqlite_path)
        refresh(self.settings)
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password','APP_QUESTIONS_PER_HOUR':'10','APP_QUESTIONS_PER_DAY':'30'}))
        self.service=SessionService(self.policy,settings=self.settings,llm_settings=load_llm_settings({}),mode='mock')
        self.expected={c['id']:c for c in json.loads(Path('data/synthetic/v1/reference-cases.json').read_text())['cases']}
        self.request=SimpleNamespace(username='demo',session_hash='test')

    def test_bilingual_reference_answers_use_actual_gated_sqlite_and_zero_model_calls(self):
        with patch('src.llm_client.LLMClient._factory',side_effect=AssertionError('No model in mock mode')):
            for case in reference_cases():
                for language in ('question_en','question_de'):
                    # Reset quota only for this independent test case, never in application code.
                    self.policy._events.clear()
                    self.service.reset(self.request)
                    self.service.submit(case[language],self.request)
                    stages=[]
                    session=self.service.advance(self.request,progress=stages.append)
                    self.assertTrue(stages)
                    if case['behavior'] in {'query','empty_result'}:
                        self.assertTrue(session.state['finished'],case['id'])
                        self.assertEqual(session.state['columns'],self.expected[case['id']]['expected_columns'])
                        self.assertEqual([list(r) for r in session.state['rows']],self.expected[case['id']]['expected_rows'])
                        self.assertGreater(session.state['query_attempts'],0)
                        if case['behavior']=='empty_result':self.assertEqual(session.state['rows'],[])
                    else:self.assertNotIn('sql',session.state)
                    self.assertEqual(session.state['llm_budget'].calls,0)
                    self.assertNotIn('_progress',session.state)

    def test_settings_are_frozen_for_service_operation(self):
        with patch.dict('os.environ',{'DB_SQLITE_PATH':'/nonexistent','DB_PROFILE':'local_synthetic'}):
            self.service.submit(reference_cases()[0]['question_en'],self.request)
            session=self.service.advance(self.request)
        self.assertTrue(session.state['finished'])

    def test_generation_uses_each_injected_dialect_after_module_import(self):
        from src.query_generator import generate_sql_query
        from src.settings import configured
        captured=[]
        class FakeClient:
            model_name='offline-fixture'
            def __init__(self,**kwargs):pass
            def chat_json(self,**kwargs):
                captured.append(kwargs['messages'])
                return {'language':'en','explanation':'fixture','sql':'SELECT 1'}
        with patch('src.query_generator.LLMClient',FakeClient),patch('src.query_generator.build_schema_struct_from_json',return_value={}):
            for dialect in ('sqlite','sqlserver'):
                with configured(replace(self.settings,dialect=dialect)):
                    generate_sql_query('Count assets',{'tables':['equipment'],'columns':[]},'Easy')
        self.assertIn('SQLite',captured[0][0]['content'])
        self.assertIn('SQL Server',captured[1][0]['content'])

    @unittest.skipUnless(importlib.util.find_spec('openai'),'Compatibility SDK not installed')
    def test_real_sdk_serializes_deepseek_extensions_without_network(self):
        import httpx
        from openai import OpenAI
        captured=[]
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':0,'model':'deepseek-flash',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'fixture'}}],
                'usage':{'prompt_tokens':3,'completion_tokens':1,'total_tokens':4}})
        client=OpenAI(api_key='offline-fixture',base_url='https://api.deepseek.com',max_retries=0,
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        try:
            result=client.chat.completions.create(model='deepseek-flash',messages=[{'role':'user','content':'fixture'}],
                max_tokens=16,extra_body={'thinking':{'type':'disabled'}})
            self.assertEqual(result.choices[0].message.content,'fixture')
            self.assertEqual(captured[0]['thinking'],{'type':'disabled'})
        finally:client.close()


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_probes_do_not_call_app_or_charge_account_and_gate_startup(self):
        policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}))
        readiness=Event()
        async def forbidden(scope,receive,send):raise AssertionError('Probe contacted serving app')
        app=SecureASGI(forbidden,policy,readiness=readiness)
        async def get(path):
            messages=[]
            async def receive():return {'type':'http.request','body':b''}
            async def send(message):messages.append(message)
            await app({'type':'http','method':'GET','path':path,'headers':[]},receive,send)
            return messages[0]['status']
        self.assertEqual(await get('/health/live'),200)
        self.assertEqual(await get('/health/ready'),503)
        self.assertEqual(await get('/config'),503)
        readiness.set()
        self.assertEqual(await get('/health/ready'),200)
        self.assertEqual(await get('/config'),401)
        self.assertFalse(policy._events)
