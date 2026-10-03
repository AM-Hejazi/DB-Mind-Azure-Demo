"""Single-process private-demo authentication, quotas, and server-owned sessions."""
from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import asyncio
import base64
import hmac
import json
import os
import re
import threading
import time
from urllib.parse import unquote, parse_qs, urlsplit

from src.settings import SettingsError


class AccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccessSettings:
    users: tuple = field(repr=False)
    questions_hour: int = 2
    questions_day: int = 10
    calls_hour: int = 24
    global_calls_day: int = 120
    requests_hour: int = 240
    max_sessions: int = 4
    session_ttl: int = 3600
    queue_size: int = 8


def load_access_settings(environ=None):
    env = os.environ if environ is None else environ
    user, password = env.get('APP_USERNAME',''), env.get('APP_PASSWORD','')
    raw = env.get('APP_AUTH','')
    if raw and (user or password):
        raise SettingsError('Use APP_AUTH or APP_USERNAME/APP_PASSWORD, not both')
    users=[]
    if raw:
        for pair in raw.split(','):
            if ':' not in pair:
                raise SettingsError('APP_AUTH requires username:password pairs')
            users.append(tuple(pair.split(':',1)))
    elif user or password:
        users.append((user,password))
    if (len(users)>20 or len({u for u,_ in users}) != len(users) or
        any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',u) or len(p)<12 or len(p)>256 or
            any(ord(c)<32 for c in p) for u,p in users)):
        raise SettingsError('Private access requires unique simple usernames and passwords of 12–256 characters')
    def integer(name,default,low,high):
        try:value=int(env.get(name,str(default)))
        except ValueError:raise SettingsError(f'{name} must be an integer') from None
        if not low<=value<=high:raise SettingsError(f'{name} exceeds the supported private-demo bounds')
        return value
    return AccessSettings(tuple(users), integer('APP_QUESTIONS_PER_HOUR',2,1,10),
        integer('APP_QUESTIONS_PER_DAY',10,1,30),integer('APP_MODEL_CALLS_PER_HOUR',24,1,100),
        integer('APP_GLOBAL_MODEL_CALLS_PER_DAY',120,1,500),integer('APP_REQUESTS_PER_HOUR',240,10,1000),
        integer('APP_MAX_SESSIONS_PER_USER',4,1,8),integer('APP_SESSION_TTL_SECONDS',3600,60,86400),
        integer('APP_QUEUE_SIZE',8,1,16))


@dataclass
class Session:
    owner: str
    key: str
    touched: float
    state: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    lock: object = field(default_factory=threading.Lock, repr=False)
    logger: object = None


class AccessPolicy:
    def __init__(self, settings=None, clock=time.monotonic):
        self.settings=settings or load_access_settings()
        self.clock=clock
        self._lock=threading.RLock()
        self._events=defaultdict(deque)
        self._sessions={}
        self.operations=threading.BoundedSemaphore(1)

    def authenticate(self, authorization):
        try:
            scheme, encoded=authorization.split(' ',1)
            if scheme.lower()!='basic' or len(encoded)>512: return None
            user,password=base64.b64decode(encoded,validate=True).decode('utf-8').split(':',1)
        except (ValueError,UnicodeError):return None
        valid=False
        for expected,secret in self.settings.users:
            valid = (hmac.compare_digest(user.encode(),expected.encode()) &
                     hmac.compare_digest(password.encode(),secret.encode())) or valid
        return user if valid else None

    def _reserve(self, key, limit, seconds):
        now=self.clock();events=self._events[key]
        while events and events[0]<=now-seconds:events.popleft()
        if len(events)>=limit:raise AccessError('Private-demo allowance reached; try again after the window expires')
        events.append(now)

    def request(self,user):
        if user not in {u for u,_ in self.settings.users}:raise AccessError('Authenticated access is required')
        with self._lock:self._reserve(('request',user),self.settings.requests_hour,3600)

    def session(self,user,key):
        if user not in {u for u,_ in self.settings.users} or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key):
            raise AccessError('Authenticated server session is required')
        with self._lock:
            now=self.clock()
            for existing,session in list(self._sessions.items()):
                if now-session.touched>self.settings.session_ttl and not session.lock.locked():
                    budget=session.state.get('llm_budget')
                    if budget:budget.cancel()
                    del self._sessions[existing]
            if key in self._sessions and self._sessions[key].owner!=user:
                raise AccessError('Session belongs to another authenticated user')
            if key not in self._sessions:
                if sum(s.owner==user for s in self._sessions.values())>=self.settings.max_sessions:
                    raise AccessError('Too many active sessions for this account')
                from src.logger import PipelineLogger
                self._sessions[key]=Session(user,key,now,logger=PipelineLogger())
            self._sessions[key].touched=now
            return self._sessions[key]

    def begin_question(self,user):
        with self._lock:
            # Check both windows before charging either; no partial reservation.
            now=self.clock()
            for key,limit,seconds in [(('question_hour',user),self.settings.questions_hour,3600),
                                      (('question_day',user),self.settings.questions_day,86400)]:
                events=self._events[key]
                while events and events[0]<=now-seconds:events.popleft()
                if len(events)>=limit:raise AccessError('Authenticated question allowance reached')
            self._events[('question_hour',user)].append(now)
            self._events[('question_day',user)].append(now)

    def charge(self,user):
        with self._lock:
            now=self.clock()
            keys=[(('calls',user),self.settings.calls_hour,3600),(('global_calls',),self.settings.global_calls_day,86400)]
            for key,limit,seconds in keys:
                events=self._events[key]
                while events and events[0]<=now-seconds:events.popleft()
                if len(events)>=limit:raise AccessError('Authenticated model-call allowance reached')
            for key,_,_ in keys:self._events[key].append(now)


_ACTOR=ContextVar('dbmind_authenticated_actor',default=None)


@contextmanager
def operator_scope():
    """Explicit local CLI boundary; never bound as a browser callback."""
    token=_ACTOR.set(('operator',None))
    try:yield
    finally:_ACTOR.reset(token)


@contextmanager
def private_scope(policy,user):
    if user not in {u for u,_ in policy.settings.users}:raise AccessError('Authenticated access is required')
    token=_ACTOR.set((user,policy))
    try:yield
    finally:_ACTOR.reset(token)


def charge_model_attempt():
    actor=_ACTOR.get()
    if actor is None:raise AccessError('A trusted authenticated or explicit local operator context is required')
    user,policy=actor
    if policy is not None:policy.charge(user)


class SecureASGI:
    """Authenticate EVERY HTTP route before Gradio, reject files/uploads and oversized bodies."""
    def __init__(self,app,policy,*,readiness=None):
        if not policy.settings.users:raise SettingsError('Configure private APP_AUTH or APP_USERNAME/APP_PASSWORD before serving')
        self.app,self.policy=app,policy
        self.readiness=readiness

    async def reject(self,send,status,text):
        headers=[(b'content-type',b'text/plain; charset=utf-8'),(b'cache-control',b'no-store')]
        if status==401:headers.append((b'www-authenticate',b'Basic realm="DB-Mind private demo"'))
        await send({'type':'http.response.start','status':status,'headers':headers})
        await send({'type':'http.response.body','body':text.encode()})

    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.app(scope,receive,send)
        if scope['type']!='http':
            if scope['type']=='websocket':await send({'type':'websocket.close','code':1008})
            return
        if self.readiness is not None:
            path=scope.get('path','')
            if scope.get('method')=='GET' and path in {'/health/live','/health/ready'}:
                ready=path=='/health/live' or self.readiness.is_set()
                return await self.reject(send,200 if ready else 503,'ok' if ready else 'starting')
            if not self.readiness.is_set():
                return await self.reject(send,503,'Startup is not ready')
        authorization=dict(scope.get('headers',[])).get(b'authorization',b'').decode('latin1')
        user=self.policy.authenticate(authorization)
        if user is None:return await self.reject(send,401,'Authenticated access is required')
        headers=dict(scope.get('headers',[]))
        origin=headers.get(b'origin',b'').decode('latin1')
        if origin:
            parsed=urlsplit(origin)
            host=headers.get(b'host',b'').decode('latin1').lower()
            if parsed.scheme not in {'http','https'} or parsed.netloc.lower()!=host or parsed.username or parsed.password:
                return await self.reject(send,403,'Cross-origin requests are not allowed')
        try:self.policy.request(user)
        except AccessError:return await self.reject(send,429,'Private-demo request allowance reached')
        path=scope.get('path','')
        for _ in range(4):path=unquote(path)
        if re.search(r'/(?:call|stream|component_server)(?:/|$)',path):
            return await self.reject(send,404,'Route unavailable')
        if '..' in path.split('/') or re.search(r'/(?:file(?:=|/)|upload(?:_progress)?(?:/|$)|proxy(?:=|/)|download(?:/|$))',path,re.I):
            return await self.reject(send,404,'Resource unavailable')
        # Never trust a principal supplied in JSON/headers. This scope field is server-owned.
        scope['dbmind_principal']=user
        bodies=[];size=0
        if scope.get('method') in {'POST','PUT','PATCH'}:
            deadline=time.monotonic()+5
            while True:
                try:part=await asyncio.wait_for(receive(),timeout=max(.001,deadline-time.monotonic()))
                except asyncio.TimeoutError:return await self.reject(send,408,"Request deadline exceeded")
                if part['type']!='http.request':return
                size+=len(part.get('body',b''))
                if size>65536:return await self.reject(send,413,'Request is too large')
                bodies.append(part)
                if not part.get('more_body',False):break
            content_type=dict(scope.get('headers',[])).get(b'content-type',b'')
            if b'application/json' in content_type and size:
                try:payload=json.loads(b''.join(p.get('body',b'') for p in bodies))
                except (ValueError,RecursionError):return await self.reject(send,400,'Invalid request')
                if isinstance(payload,dict) and payload.get('session_hash') is not None:
                    try:self.policy.session(user,payload['session_hash'])
                    except AccessError:return await self.reject(send,403,'Session unavailable')
        query=parse_qs(scope.get('query_string',b'').decode('latin1'))
        for key in query.get('session_hash',[]):
            try:self.policy.session(user,key)
            except AccessError:return await self.reject(send,403,'Session unavailable')
        async def replay():
            if bodies:return bodies.pop(0)
            return await receive()
        async def secure_send(message):
            if message['type']=='http.response.start':
                message={**message,'headers':list(message.get('headers',[]))+[
                    (b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),
                    (b'referrer-policy',b'no-referrer')]}
            await send(message)
        await self.app(scope,replay,secure_send)
