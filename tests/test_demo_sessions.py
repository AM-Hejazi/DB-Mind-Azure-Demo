"""Absolute visit expiry and shared-login isolation, with no SQL/provider calls."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from src.access import AccessPolicy, AccessError, EXPIRED_MESSAGE, SecureASGI, load_access_settings, private_scope, charge_model_attempt


class DemoSessionTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}),clock=lambda:self.now)

    def test_absolute_expiry_survives_activity_new_questions_and_reload(self):
        visitor=self.policy.visitor('demo')
        session=self.policy.session('demo','first',visitor)
        self.now=1799
        self.assertIs(self.policy.session('demo','first',visitor),session)
        self.assertEqual(self.policy.visitor('demo',visitor),visitor)
        self.policy.session('demo','new-tab',visitor)
        self.now=1800
        for action in [lambda:self.policy.visitor('demo',visitor),
                       lambda:self.policy.session('demo','replacement',visitor),
                       lambda:self.policy.begin_question('demo',visitor),
                       lambda:self.policy.charge('demo',visitor)]:
            with self.assertRaisesRegex(AccessError,'contact the developer'):action()

    def test_shared_login_has_independent_request_question_and_model_limits(self):
        first=self.policy.visitor('demo');second=self.policy.visitor('demo')
        for _ in range(self.policy.settings.requests_hour):self.policy.request('demo',first)
        with self.assertRaises(AccessError):self.policy.request('demo',first)
        self.policy.request('demo',second)
        for _ in range(self.policy.settings.questions_hour):self.policy.begin_question('demo',first)
        with self.assertRaises(AccessError):self.policy.begin_question('demo',first)
        self.policy.begin_question('demo',second)
        self.policy.settings=replace(self.policy.settings,calls_hour=1,global_calls_day=2)
        with private_scope(self.policy,'demo',first):charge_model_attempt()
        with self.assertRaises(AccessError):self.policy.charge('demo',first)
        self.policy.charge('demo',second)
        third=self.policy.visitor('demo')
        with self.assertRaises(AccessError):self.policy.charge('demo',third)
        self.assertEqual(len(self.policy._events[('global_calls',)]),2)

    def test_queue_sessions_are_bound_to_the_browser_not_just_shared_username(self):
        first=self.policy.visitor('demo');second=self.policy.visitor('demo')
        self.policy.session('demo','owned',first)
        with self.assertRaises(AccessError):self.policy.session('demo','owned',second)
        with self.assertRaises(AccessError):self.policy.visitor('other',first)
        with self.assertRaises(AccessError):self.policy.visitor('demo','forged')

    def test_expiry_cancels_in_flight_budget(self):
        visitor=self.policy.visitor('demo')
        session=self.policy.session('demo','owned',visitor)
        cancelled=[]
        session.state['llm_budget']=SimpleNamespace(cancel=lambda:cancelled.append(True))
        self.now=1800
        with self.assertRaises(AccessError):self.policy.visitor('demo',visitor)
        self.assertTrue(cancelled)

    def test_work_queued_before_deadline_cannot_start_after_expiry(self):
        from src.service import SessionService
        visitor=self.policy.visitor('demo')
        request=SimpleNamespace(username='demo',session_hash='queued',request=SimpleNamespace(scope={'dbmind_visit':visitor}))
        def forbidden_database(*args):raise AssertionError('Expired work contacted the database')
        service=SessionService(self.policy,database_factory=forbidden_database,mode='mock')
        service.submit('A maintenance question',request)
        self.now=1800
        with self.assertRaisesRegex(AccessError,'contact the developer'):service.advance(request)

    def test_configured_timeout_cannot_exceed_30_minutes(self):
        from src.settings import SettingsError
        with self.assertRaises(SettingsError):load_access_settings({'APP_SESSION_TTL_SECONDS':'1801'})


class DemoSessionHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_https_ingress_with_http_backend_still_issues_secure_cookie(self):
        import httpx
        policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}))
        async def inner(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'demo'})
        secured=SecureASGI(inner,policy)
        async def proxy(scope,receive,send):
            await secured({**scope,'scheme':'http'},receive,send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy),base_url='https://demo.test',auth=('demo','offline-password')) as client:
            response=await client.get('/')
            self.assertIn('; Secure',response.headers['set-cookie'])
            self.assertEqual((await client.get('/demo/session')).status_code,200)
            self.assertEqual(len(policy._visitors),1)

    async def test_cookie_reload_expiry_and_second_recruiter(self):
        import httpx
        now=[0]
        policy=AccessPolicy(load_access_settings({'APP_AUTH':'demo:offline-password'}),clock=lambda:now[0])
        async def inner(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'demo'})
        app=SecureASGI(inner,policy)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test',auth=('demo','offline-password')) as first, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test',auth=('demo','offline-password')) as second:
            root=await first.get('/')
            cookie=root.headers['set-cookie']
            self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Strict',cookie)
            token=first.cookies.get('dbmind_visit')
            for _ in range(policy.settings.requests_hour+1):
                self.assertEqual((await first.get('/assets/app.js')).status_code,200)
            self.assertFalse(policy._events)
            now[0]=900
            self.assertEqual((await first.get('/')).status_code,200)
            self.assertEqual(first.cookies.get('dbmind_visit'),token)
            self.assertEqual((await second.get('/')).status_code,200)
            self.assertNotEqual(second.cookies.get('dbmind_visit'),token)
            before=sum(len(v) for v in policy._events.values())
            self.assertEqual((await first.get('/demo/session')).json()['remaining_seconds'],900)
            self.assertEqual(sum(len(v) for v in policy._events.values()),before)
            now[0]=1800
            for path in ['/','/config','/demo/session','/gradio_api/queue/data?session_hash=new']:
                response=await first.get(path)
                self.assertEqual(response.status_code,403)
                self.assertEqual(response.text,EXPIRED_MESSAGE)
            self.assertEqual((await second.get('/config')).status_code,200)
            self.assertEqual((await first.post('/gradio_api/queue/join',json={'session_hash':'fresh'})).status_code,403)
