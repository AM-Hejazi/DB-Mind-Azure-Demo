"""Explicit, immutable DeepSeek settings; parsing never creates an SDK client."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import os
from types import MappingProxyType
from src.settings import SettingsError

STAGES = ('CG', 'SR', 'AN', 'FA', 'QC', 'QF', 'TR', 'EV')
MODELS = frozenset({'deepseek-flash', 'deepseek-v4-pro'})

@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str = field(repr=False)
    base_url: str
    model: str
    stages: tuple[tuple[str, str], ...]
    thinking: str
    effort: str
    timeout: int
    retries: int
    max_tokens: int
    max_input_chars: int
    max_output_chars: int
    pipeline_calls: int
    pipeline_seconds: int
    evaluation_enabled: bool
    rates: tuple[Decimal, Decimal, Decimal] | None
    rates_date: str

    def model_for(self, stage):
        if stage not in STAGES:
            raise SettingsError('Unknown LLM stage')
        return dict(self.stages)[stage]

    def public_models(self):
        return MappingProxyType({stage+'_MODEL': model for stage, model in self.stages})


from contextvars import ContextVar
from contextlib import contextmanager
_RUNTIME = ContextVar('dbmind_model_settings', default=None)

@contextmanager
def configured(settings):
    token = _RUNTIME.set(settings)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


def load_llm_settings(environ=None):
    if environ is None and _RUNTIME.get() is not None:
        return _RUNTIME.get()
    env = os.environ if environ is None else environ
    def value(name, default=''):
        return env.get(name, default).strip()
    def number(name, default, low, high):
        try:
            result = int(value(name, str(default)))
        except ValueError:
            raise SettingsError(f'{name} must be an integer') from None
        if not low <= result <= high:
            raise SettingsError(f'{name} must be between {low} and {high}')
        return result
    def choice(name, default, allowed):
        result = value(name, default)
        if result not in allowed:
            raise SettingsError(f'{name} has an unsupported value')
        return result
    provider = choice('LLM_PROVIDER', 'deepseek', {'deepseek'})
    base_url = choice('DEEPSEEK_BASE_URL', 'https://api.deepseek.com',
                      {'https://api.deepseek.com', 'https://api.deepseek.com/v1'})
    model = choice('DEEPSEEK_MODEL', 'deepseek-flash', MODELS)
    stages = tuple((s, choice(f'DEEPSEEK_{s}_MODEL', model, MODELS)) for s in STAGES)
    key = value('DEEPSEEK_API_KEY')
    if any(c.isspace() or ord(c) < 32 for c in key):
        raise SettingsError('DEEPSEEK_API_KEY must not contain whitespace/control characters')
    thinking = choice('DEEPSEEK_THINKING', 'disabled', {'enabled', 'disabled'})
    effort = choice('DEEPSEEK_REASONING_EFFORT', 'low', {'low', 'high', 'max'})
    evaluation = choice('LLM_ENABLE_EVALUATION', 'false', {'true', 'false'}) == 'true'
    names = ('LLM_INPUT_CACHE_HIT_USD_PER_M', 'LLM_INPUT_CACHE_MISS_USD_PER_M', 'LLM_OUTPUT_USD_PER_M')
    rates = None
    rate_date = value('LLM_RATES_DATE')
    if any(value(n) for n in names) or rate_date:
        try:
            rates = tuple(Decimal(value(n)) for n in names)
            if any(not r.is_finite() or r < 0 or r > 10000 for r in rates):
                raise ValueError
            if date.fromisoformat(rate_date).isoformat() != rate_date:
                raise ValueError
        except (ValueError, InvalidOperation):
            raise SettingsError('All three LLM rates and an ISO LLM_RATES_DATE are required') from None
    return LLMSettings(provider, key, base_url, model, stages, thinking, effort,
        number('LLM_TIMEOUT_SECONDS', 30, 1, 120), number('LLM_RETRIES', 1, 0, 2),
        number('LLM_MAX_OUTPUT_TOKENS', 2048, 32, 8192),
        number('LLM_MAX_INPUT_CHARS', 48000, 2000, 100000),
        number('LLM_MAX_OUTPUT_CHARS', 16000, 256, 32000),
        number('LLM_PIPELINE_MAX_CALLS', 12, 1, 30),
        number('LLM_PIPELINE_TIMEOUT_SECONDS', 300, 5, 900), evaluation, rates, rate_date)
