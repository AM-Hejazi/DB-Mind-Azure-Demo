"""Bounded synchronous Chat Completions, with no provider/model fallback."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import inspect
import json
import random
import threading
import time
from types import SimpleNamespace

from src.llm_settings import load_llm_settings, MODELS
from src.settings import SettingsError


class LLMError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass
class CallBudget:
    max_calls: int
    seconds: int
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    cancelled: bool = False
    cancellation: object = field(default_factory=threading.Event, repr=False, compare=False)
    records: list = field(default_factory=list)
    _lock: object = field(default_factory=threading.Lock, repr=False, compare=False)

    def __deepcopy__(self, memo):
        # Budgets are server-side handles, never duplicated to replenish an allowance.
        return self

    def remaining(self):
        if self.cancelled or self.cancellation.is_set():
            raise LLMError('cancelled', 'Model request cancelled')
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise LLMError('deadline', 'Pipeline model deadline exceeded')
        return remaining

    def reserve(self):
        with self._lock:
            self.remaining()
            if self.calls >= self.max_calls:
                raise LLMError('budget', 'Pipeline model call budget exhausted')
            self.calls += 1

    def cancel(self):
        self.cancelled = True
        self.cancellation.set()


_ACTIVE = ContextVar('dbmind_llm_budget', default=None)


def new_budget(settings=None):
    settings = settings or load_llm_settings()
    return CallBudget(settings.pipeline_calls, settings.pipeline_seconds)


@contextmanager
def use_budget(budget=None):
    budget = budget or new_budget()
    token = _ACTIVE.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE.reset(token)


def pipeline_operation(function):
    """One budget across a CLI operation, including all stages/retries."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _ACTIVE.get() is not None:
            return function(*args, **kwargs)
        from src.access import operator_scope
        from src.logger import PipelineLogger, use_logger
        with operator_scope(), use_logger(PipelineLogger()), use_budget():
            return function(*args, **kwargs)
    return wrapped


def session_operation(function):
    """Keep the budget in server session state across callback generator resumes."""
    signature = inspect.signature(function)
    def budget_for(args, kwargs):
        state = signature.bind(*args, **kwargs).arguments['session_state']
        budget = state.get('llm_budget')
        if budget is None:
            budget = state['llm_budget'] = new_budget()
        return budget
    if inspect.isgeneratorfunction(function):
        @wraps(function)
        def generator(*args, **kwargs):
            budget = budget_for(args, kwargs)
            iterator = function(*args, **kwargs)
            try:
                while True:
                    # Never leave a context variable installed across a UI yield.
                    with use_budget(budget):
                        try:
                            item = next(iterator)
                        except StopIteration:
                            return
                    yield item
            finally:
                with use_budget(budget):
                    iterator.close()
        return generator
    @wraps(function)
    def wrapped(*args, **kwargs):
        with use_budget(budget_for(args, kwargs)):
            return function(*args, **kwargs)
    return wrapped


def _error(exc):
    status = getattr(exc, 'status_code', None)
    if status in {401, 403}:
        return 'authentication', False
    if status == 402:
        return 'balance', False
    if status in {400, 404, 422}:
        return 'invalid_request', False
    if status == 429 or status in {500, 502, 503, 504}:
        return 'unavailable', True
    if type(exc).__name__ in {'APITimeoutError', 'APIConnectionError'}:
        return 'transport', True
    return 'transport', False


def _usage(raw):
    if raw is None:
        return None
    result = {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens',
                 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
        value = getattr(raw, name, None)
        if value is not None:
            if type(value) is not int or value < 0:
                raise LLMError('malformed', 'Invalid model usage statistics')
            result[name] = value
    if not {'prompt_tokens', 'completion_tokens', 'total_tokens'} <= result.keys():
        raise LLMError('malformed', 'Incomplete model usage statistics')
    if result['total_tokens'] != result['prompt_tokens'] + result['completion_tokens']:
        raise LLMError('malformed', 'Inconsistent model usage statistics')
    return result


def estimate_cost(usage, settings, model):
    # Rates apply only to the configured default model, not differently priced overrides.
    if not usage or not settings.rates or model != settings.model:
        return None
    hit = usage.get('prompt_cache_hit_tokens')
    miss = usage.get('prompt_cache_miss_tokens')
    if hit is None or miss is None or hit + miss != usage['prompt_tokens']:
        return None
    hit_rate, miss_rate, output_rate = settings.rates
    from decimal import Decimal
    amount = (hit * hit_rate + miss * miss_rate + usage['completion_tokens'] * output_rate)/Decimal(1000000)
    return {'estimated_usd': str(amount), 'rates_date': settings.rates_date, 'rate_model': model}


class LLMClient:
    def __init__(self, model_name=None, provider=None, *, stage='CG', settings=None,
                 sdk_factory=None, budget=None, sleeper=time.sleep, jitter=random.random):
        self.settings = settings or load_llm_settings()
        if provider and provider.lower() != self.settings.provider:
            raise SettingsError('Requested provider differs from server LLM_PROVIDER')
        self.model_name = model_name or self.settings.model_for(stage)
        if self.model_name not in MODELS:
            raise SettingsError('Unsupported model; aliases and inferred providers are disabled')
        if stage in {'TR', 'EV'} and not self.settings.evaluation_enabled:
            raise LLMError('evaluation_disabled', 'Set LLM_ENABLE_EVALUATION=true for explicit evaluation calls')
        self.stage = stage
        self.client_type = self.settings.provider
        self.sdk_factory = sdk_factory
        self.budget = budget or _ACTIVE.get() or new_budget(self.settings)
        self.sleeper, self.jitter = sleeper, jitter

    def _factory(self):
        if not self.settings.api_key:
            raise LLMError('credentials', 'DEEPSEEK_API_KEY is required for a live model call')
        if self.sdk_factory is not None:
            return self.sdk_factory
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMError('dependency', 'Install the pinned OpenAI compatibility SDK for live calls') from None
        return OpenAI

    def _request(self, factory, request, timeout):
        # SDK socket timeouts are per phase. Bound caller wall time as well, and
        # close the client on deadline/cancellation. No executor shutdown joins.
        done = threading.Event()
        result = []
        with factory(api_key=self.settings.api_key, base_url=self.settings.base_url,
                     max_retries=0, timeout=timeout) as sdk:
            def invoke():
                try:
                    result.append((True, sdk.chat.completions.create(**request, timeout=timeout)))
                except Exception as exc:
                    result.append((False, exc))
                finally:
                    done.set()
            worker = threading.Thread(target=invoke, daemon=True, name='dbmind-model-request')
            worker.start()
            deadline = time.monotonic() + timeout
            while not done.is_set():
                remaining = min(deadline-time.monotonic(), self.budget.remaining())
                if remaining <= 0:
                    self.budget.cancel()
                    raise LLMError('deadline', 'Model request deadline exceeded; remote cancellation is best effort')
                done.wait(min(0.05, remaining))
            self.budget.remaining()
            if time.monotonic() > deadline:
                self.budget.cancel()
                raise LLMError("deadline", "Model request deadline exceeded")
            if not result[0][0]:
                raise result[0][1]
            return result[0][1]

    def chat(self, messages, temperature=0.2, *, json_object=False, stream=False):
        if stream:
            raise LLMError('invalid_request', 'Streaming is disabled until complete output can be validated')
        if (not isinstance(messages, list) or not messages or len(messages) > 40 or
            any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
                m['role'] not in {'system', 'user', 'assistant'} or not isinstance(m['content'], str)
                for m in messages)):
            raise LLMError('invalid_request', 'Expected bounded text-only chat messages')
        if sum(len(m['content']) for m in messages) > self.settings.max_input_chars:
            raise LLMError('input_limit', 'Model input exceeds the configured character budget')
        if type(temperature) not in {int, float} or not 0 <= temperature <= 2:
            raise LLMError('invalid_request', 'Temperature must be between zero and two')
        if json_object and not any('json' in m['content'].lower() for m in messages):
            raise LLMError('invalid_request', 'JSON output needs an explicit JSON instruction')
        request = dict(model=self.model_name, messages=messages, max_tokens=self.settings.max_tokens,
                       stream=False, extra_body={'thinking': {'type': self.settings.thinking}})
        if self.settings.thinking == 'enabled':
            # extra_body accommodates low/high/max in the older SDK type definitions.
            request['extra_body']['reasoning_effort'] = self.settings.effort
        else:
            request['temperature'] = temperature
        if json_object:
            request['response_format'] = {'type': 'json_object'}
        factory = self._factory()
        for attempt in range(self.settings.retries + 1):
            from src.access import charge_model_attempt
            charge_model_attempt()
            self.budget.reserve()
            timeout = min(self.settings.timeout, self.budget.remaining())
            try:
                raw = self._request(factory, request, timeout)
            except LLMError as exc:
                self.budget.records.append({"stage": self.stage, "requested_model": self.model_name,
                                            "error": exc.code, "usage": None})
                raise
            except Exception as exc:
                code, retry = _error(exc)
                self.budget.records.append({'stage': self.stage, 'requested_model': self.model_name,
                                            'error': code, 'attempt': attempt + 1, 'usage': None})
                if not retry or attempt >= self.settings.retries:
                    raise LLMError(code, f'Model request failed ({code}); no fallback was used') from None
                delay = min(4.0, 0.5 * (2**attempt) + 0.25 * self.jitter())
                if delay >= self.budget.remaining():
                    raise LLMError('deadline', 'Insufficient pipeline time for a retry') from None
                self.sleeper(delay)
                self.budget.remaining()
                continue
            return self._validate(raw)

    def _validate(self, raw):
        choices = getattr(raw, 'choices', None)
        returned_model = getattr(raw, 'model', None)
        usage = _usage(getattr(raw, 'usage', None))
        record = {'stage': self.stage, 'requested_model': self.model_name,
                  'returned_model': returned_model if isinstance(returned_model, str) and len(returned_model) <= 200 else None,
                  'usage': usage, 'cost': estimate_cost(usage, self.settings, self.model_name)}
        self.budget.records.append(record)
        if not isinstance(returned_model, str) or not returned_model or len(returned_model) > 200:
            raise LLMError('malformed', 'Model response has no valid returned model ID')
        if not isinstance(choices, list) or len(choices) != 1:
            raise LLMError('malformed', 'Model response must have exactly one choice')
        choice = choices[0]
        if getattr(choice, 'finish_reason', None) != 'stop':
            raise LLMError('incomplete', 'Model output was truncated or did not finish normally')
        message = getattr(choice, 'message', None)
        content = getattr(message, 'content', None)
        if getattr(message, 'tool_calls', None) or getattr(message, 'refusal', None):
            raise LLMError('malformed', 'Unexpected model tool call or refusal')
        if not isinstance(content, str) or not content.strip() or len(content) > self.settings.max_output_chars:
            raise LLMError('malformed', 'Model returned empty or oversized content')
        # Rebuild the response: reasoning_content is never exposed to stages/logs/UI.
        return SimpleNamespace(model=returned_model, requested_model=self.model_name, usage=usage,
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content.strip()))],
            metadata=record)

    def chat_json(self, messages, fields, temperature=0.2):
        response = self.chat(messages, temperature, json_object=True)
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result
            data = json.loads(response.choices[0].message.content, object_pairs_hook=pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(data, dict) or set(data) != set(fields):
                raise ValueError
            for name, expected_type in fields.items():
                if type(data[name]) is not expected_type:
                    raise ValueError
                if expected_type is str and not data[name].strip():
                    raise ValueError
        except (ValueError, TypeError, RecursionError):
            raise LLMError('malformed', 'Model JSON does not match the required response fields') from None
        return data
