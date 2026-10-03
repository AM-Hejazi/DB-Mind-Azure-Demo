"""Actual Gradio 6.29.1 ASGI routes; SDK/SQL Server are never contacted."""
import asyncio
import base64
from pathlib import Path
import re
import importlib.util
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HAS_GRADIO=importlib.util.find_spec('gradio') is not None


@unittest.skipUnless(HAS_GRADIO,'Install the isolated documented Gradio test runtime for HTTP checks')
class PrivateHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.dict(os.environ,{'GRADIO_ANALYTICS_ENABLED':'False','HF_HUB_OFFLINE':'1','APP_LLM_MODE':'live'}))
        import httpx
        from app import create_secure_app
        from src.access import AccessPolicy,load_access_settings
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'alice:offline-password-a,bob:offline-password-b'}))
        self.app=create_secure_app(self.policy)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='https://private-test')
        self.addAsyncCleanup(self.client.aclose)
        self.enterContext(patch('src.llm_client.LLMClient._factory',side_effect=AssertionError('Unexpected provider call')))
        self.enterContext(patch('src.db_connection.connect_sqlserver',side_effect=AssertionError('Unexpected SQL Server call')))

    async def test_anonymous_all_ui_api_queue_file_routes_rejected(self):
        for path in ['/','/config','/gradio_api/info','/gradio_api/queue/data?session_hash=unknown',
                     '/gradio_api/file=/etc/passwd','/gradio_api/call/predict/event','/gradio_api/stream/event']:
            response=await self.client.get(path)
            self.assertEqual(response.status_code,401,path)
            self.assertIn('www-authenticate',response.headers)
        for path in ['/gradio_api/run/predict','/gradio_api/queue/join','/gradio_api/upload','/login']:
            response=await self.client.post(path,json={'fn_index':1,'session_hash':'forged','data':[]})
            self.assertEqual(response.status_code,401,path)
        self.assertEqual(self.policy._sessions,{})

    async def test_authenticated_config_and_direct_run_cannot_bypass_queue(self):
        self.client.auth=('alice','offline-password-a')
        response=await self.client.get('/config')
        self.assertEqual(response.status_code,200)
        config=response.json()
        header=next(c['props']['value'] for c in config['components']
                    if c['type']=='html' and 'dbmind-header' in c['props'].get('value',''))
        self.assertIn('alt="DB-Mind"',header)
        embedded=re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)',header)
        self.assertIsNotNone(embedded)
        self.assertEqual(base64.b64decode(embedded.group(1)),Path('data/icon.png').read_bytes())
        self.assertTrue(any(c['type']=='button' and c['props']['value']=='Send' for c in config['components']))
        self.assertFalse(self.app.app.routes[-1].app.get_blocks().api_open)
        self.assertTrue(all(d['api_visibility']=='private' for d in config['dependencies']))
        self.assertEqual(config['dependencies'][0]['inputs'].__len__(),1)
        self.assertEqual(config['dependencies'][1]['inputs'],[])
        for path in ['/gradio_api/run/predict','/gradio_api/api/predict']:
            response=await self.client.post(path,json={'fn_index':0,'session_hash':'owned','data':['question']})
            self.assertEqual(response.status_code,404)
        self.assertFalse(self.policy._sessions['owned'].state.get('question_started'))
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')

    async def test_authenticated_file_upload_stream_and_proxy_routes_blocked(self):
        self.client.auth=('alice','offline-password-a')
        for path in ['/gradio_api/file=/etc/passwd','/gradio_api/file=%2Fworkspaces%2FDB-Mind%2F.env',
                     '/gradio_api/file=/app/data/icon.png','/gradio_api/file=/workspaces/DB-Mind/data/icon.png',
                     '/gradio_api/file%253D/etc/passwd','/gradio_api/proxy=https://example.test',
                     '/gradio_api/stream/session/1/1/file','/gradio_api/call/predict/event']:
            self.assertEqual((await self.client.get(path)).status_code,404,path)
        self.assertEqual((await self.client.post('/gradio_api/upload',content=b'private')).status_code,404)

    async def test_cross_user_queue_replay_and_oversized_body_stopped_before_gradio(self):
        self.policy.session('alice','owned')
        self.client.auth=('bob','offline-password-b')
        response=await self.client.post('/gradio_api/queue/join',json={'fn_index':0,'session_hash':'owned','data':['x']})
        self.assertEqual(response.status_code,403)
        response=await self.client.get('/gradio_api/queue/data?session_hash=owned')
        self.assertEqual(response.status_code,403)
        self.assertEqual((await self.client.post('/gradio_api/queue/join',content=b'x'*65537)).status_code,413)
        self.assertFalse(self.policy._sessions['owned'].state)

    async def test_actual_queue_injects_authenticated_username_into_callback(self):
        self.client.auth=('alice','offline-password-a')
        # Start the mounted Gradio lifespan: queue worker and native Request injection.
        async with self.app.app.router.lifespan_context(self.app.app):
            response=await self.client.post('/gradio_api/queue/join',json={'fn_index':0,'session_hash':'owned','data':['A synthetic question']})
            self.assertEqual(response.status_code,200,response.text)
            for _ in range(100):
                session=self.policy._sessions['owned']
                if session.state.get('question_started'):break
                await asyncio.sleep(.02)
            self.assertTrue(session.state.get('question_started'))
            self.assertEqual(session.owner,'alice')
            self.assertEqual(session.state['question'],'A synthetic question')
            self.assertEqual(len(self.policy._events[('question_hour',('alice',self.policy._sessions['owned'].visitor))]),1)

    async def test_queued_model_work_uses_trusted_user_and_ignores_forged_body_username(self):
        from src.access import charge_model_attempt
        def answer(service,session,message):
            charge_model_attempt()
            session.state['finished']=True
            session.history.append({'role':'assistant','content':'Mocked synthetic answer'})
        self.client.auth=('alice','offline-password-a')
        with patch('src.service.SessionService._answer',answer):
            async with self.app.app.router.lifespan_context(self.app.app):
                response=await self.client.post('/gradio_api/queue/join',json={
                    'fn_index':0,'session_hash':'owned','data':['A synthetic question'],'username':'bob'})
                self.assertEqual(response.status_code,200)
                for _ in range(100):
                    if self.policy._sessions['owned'].state.get('question_started'):break
                    await asyncio.sleep(.02)
                response=await self.client.post('/gradio_api/queue/join',json={
                    'fn_index':1,'session_hash':'owned','data':[],'username':'bob'})
                self.assertEqual(response.status_code,200)
                for _ in range(100):
                    if self.policy._sessions['owned'].state.get('finished'):break
                    await asyncio.sleep(.02)
                self.assertTrue(self.policy._sessions['owned'].state.get('finished'))
                self.assertEqual(len(self.policy._events[('calls',('alice',self.policy._sessions['owned'].visitor))]),1)
                self.assertEqual(len(self.policy._events[('calls','bob')]),0)

    async def test_cross_origin_request_rejected_before_queue_or_quota(self):
        self.client.auth=('alice','offline-password-a')
        response=await self.client.post('/gradio_api/queue/join',headers={'Origin':'https://untrusted.example'},
            json={'fn_index':0,'session_hash':'forged','data':['question']})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.policy._sessions,{})
        self.assertEqual(len(self.policy._events[('question_hour','alice')]),0)
        self.assertEqual((await self.client.get('/config',headers={'Origin':'http://private-test'})).status_code,200)


if __name__=='__main__':unittest.main()
