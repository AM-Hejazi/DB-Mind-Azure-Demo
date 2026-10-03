"""Configured application admin access; no provider or database connections."""
import base64
import unittest
from src.access import AccessPolicy, AccessError, SecureASGI, load_access_settings
from src.settings import SettingsError

ENV={'APP_AUTH':'demo:offline-password','APP_ADMIN_USERNAME':'admin',
     'APP_ADMIN_PASSWORD':'offline-admin-password'}

class AdminAccessTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.policy=AccessPolicy(load_access_settings(ENV),clock=lambda:self.now)

    def test_explicit_admin_survives_deadline_and_session_cleanup(self):
        admin=self.policy.visitor('admin');demo=self.policy.visitor('demo')
        session=self.policy.session('admin','owned',admin)
        self.now=86400*7
        self.assertEqual(self.policy.visitor('admin',admin),admin)
        self.assertIs(self.policy.session('admin','owned',admin),session)
        self.policy.session('demo','new')
        self.assertIs(self.policy.session('admin','owned',admin),session)
        with self.assertRaises(AccessError):self.policy.visitor('demo',demo)

    def test_admin_repeated_questions_keep_global_model_cap(self):
        visitor=self.policy.visitor('admin')
        for _ in range(100):self.policy.begin_question('admin',visitor)
        for _ in range(self.policy.settings.global_calls_day):self.policy.charge('admin',visitor)
        with self.assertRaises(AccessError):self.policy.charge('admin',visitor)
        # Technical HTTP rate limits still apply.
        for _ in range(self.policy.settings.requests_hour):self.policy.request('admin',visitor)
        with self.assertRaises(AccessError):self.policy.request('admin',visitor)

    def test_role_requires_configuration_not_username_or_cookie(self):
        ordinary=AccessPolicy(load_access_settings({'APP_AUTH':'admin:offline-password'}))
        self.assertFalse(ordinary.is_admin('admin'))
        with self.assertRaises(AccessError):self.policy.visitor('admin','forged')
        with self.assertRaises(AccessError):self.policy.visitor('intruder')
        demo=self.policy.visitor('demo')
        with self.assertRaises(AccessError):self.policy.visitor('admin',demo)
        admin=self.policy.visitor('admin');self.policy.session('admin','owned',admin)
        with self.assertRaises(AccessError):self.policy.session('demo','owned',demo)

    def test_admin_password_validation_and_repr_redaction(self):
        for extra in [{'APP_ADMIN_PASSWORD':''},{'APP_ADMIN_USERNAME':''},
                      {'APP_ADMIN_PASSWORD':'short'},{'APP_ADMIN_USERNAME':'demo'}]:
            with self.assertRaises(SettingsError):load_access_settings({**ENV,**extra})
        self.assertNotIn(ENV['APP_ADMIN_PASSWORD'],repr(self.policy.settings))
        def auth(password):
            return 'Basic '+base64.b64encode(('admin:'+password).encode()).decode()
        self.assertEqual(self.policy.authenticate(auth(ENV['APP_ADMIN_PASSWORD'])),'admin')
        self.assertIsNone(self.policy.authenticate(auth('wrong-password')))

class AdminHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_deadline_null_and_demo_expiry_with_route_guards(self):
        import httpx
        now=[0];policy=AccessPolicy(load_access_settings(ENV),clock=lambda:now[0])
        async def inner(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        transport=httpx.ASGITransport(app=SecureASGI(inner,policy))
        async with httpx.AsyncClient(transport=transport,base_url='https://demo.test',auth=('admin',ENV['APP_ADMIN_PASSWORD'])) as admin, httpx.AsyncClient(transport=transport,base_url='https://demo.test',auth=('demo','offline-password')) as demo:
            response=await admin.get('/')
            self.assertIn('HttpOnly',response.headers['set-cookie'])
            self.assertIn('Secure',response.headers['set-cookie'])
            await demo.get('/')
            now[0]=86400
            self.assertIsNone((await admin.get('/demo/session')).json()['remaining_seconds'])
            self.assertEqual((await admin.get('/config')).status_code,200)
            self.assertEqual((await demo.get('/demo/session')).status_code,403)
            self.assertEqual((await admin.get('/gradio_api/file=/etc/passwd')).status_code,404)
            self.assertEqual((await admin.post('/gradio_api/upload')).status_code,404)
            self.assertEqual((await admin.get('/config',headers={'Origin':'https://evil.test'})).status_code,403)
            admin.auth=('admin','wrong-password')
            self.assertEqual((await admin.get('/config')).status_code,401)
