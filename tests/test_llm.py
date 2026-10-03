"""No installed SDK, network, credentials, database or paid completion required."""
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from src.llm_client import (LLMClient, LLMError, CallBudget, use_budget, pipeline_operation,
                            session_operation, estimate_cost, _ACTIVE)
from src.llm_settings import load_llm_settings
from src.settings import SettingsError
from src.query_generator import format_generated_response, extract_final_sql
from src.llm_check import main as check_main

MESSAGES = [{'role': 'system', 'content': 'Return concise JSON.'}, {'role': 'user', 'content': 'Synthetic test.'}]


def completion(content='OK', **changes):
    raw = NS(model='deepseek-flash-verified-version', usage=NS(prompt_tokens=10, completion_tokens=2,
        total_tokens=12, prompt_cache_hit_tokens=3, prompt_cache_miss_tokens=7),
        choices=[NS(finish_reason='stop', message=NS(content=content, reasoning_content='PRIVATE REASONING', tool_calls=None))])
    for key, value in changes.items():
        setattr(raw, key, value)
    return raw


class LLMTests(unittest.TestCase):
    def setUp(self):
        from src.access import operator_scope
        self.enterContext(operator_scope())
        self.settings = load_llm_settings({'DEEPSEEK_API_KEY': 'offline-test-only'})
        self.sdk = MagicMock()
        self.sdk.__enter__.return_value = self.sdk
        self.sdk.chat.completions.create.return_value = completion()
        self.factory = MagicMock(return_value=self.sdk)

    def client(self, **kwargs):
        return LLMClient(settings=self.settings, sdk_factory=self.factory, **kwargs)

    def test_settings_explicit_defaults_overrides_secret_redaction_and_rejection(self):
        self.assertEqual(self.settings.model_for('QC'), 'deepseek-flash')
        self.assertEqual(self.settings.model_for('QF'), 'deepseek-flash')
        self.assertNotIn('offline-test-only', repr(self.settings))
        other = load_llm_settings({'DEEPSEEK_AN_MODEL':'deepseek-v4-pro'})
        self.assertEqual(other.model_for('AN'), 'deepseek-v4-pro')
        with self.assertRaises(TypeError):
            other.public_models()['AN_MODEL'] = 'something'
        for env in [{'LLM_PROVIDER':'openai'}, {'DEEPSEEK_MODEL':'deepseek-chat'},
                    {'DEEPSEEK_BASE_URL':'https://attacker.test'}, {'LLM_RETRIES':'9'},
                    {'DEEPSEEK_THINKING':'auto'}, {'DEEPSEEK_API_KEY':'secret\nheader'},
                    {'LLM_RATES_DATE':'2026-10-02'}]:
            with self.subTest(env=tuple(env)), self.assertRaises(SettingsError):
                load_llm_settings(env)
        with self.assertRaises(SettingsError):
            self.client(model_name='gpt-5')
        with self.assertRaises(SettingsError):
            self.client(provider='openai')

    def test_sdk_request_limits_retry_disabled_and_reasoning_removed(self):
        client = self.client(stage='QC')
        response = client.chat(MESSAGES, temperature=0.2)
        constructor = self.factory.call_args.kwargs
        self.assertEqual(constructor['max_retries'], 0)
        self.assertEqual(constructor['base_url'], 'https://api.deepseek.com')
        request = self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(request['max_tokens'], 2048)
        self.assertFalse(request['stream'])
        self.assertEqual(request['extra_body'], {'thinking': {'type':'disabled'}})
        self.assertEqual(request['temperature'], .2)
        self.assertLessEqual(request['timeout'], 30)
        self.assertFalse(hasattr(response.choices[0].message, 'reasoning_content'))
        self.assertNotIn('PRIVATE REASONING', repr(response))
        self.assertEqual(response.usage['total_tokens'], 12)
        self.assertEqual(client.budget.records[0]['requested_model'], 'deepseek-flash')
        self.assertEqual(client.budget.records[0]['returned_model'], 'deepseek-flash-verified-version')
        self.sdk.__exit__.assert_called_once()

    def test_thinking_is_explicit_not_inferred_and_temperature_omitted(self):
        client = LLMClient(settings=replace(self.settings, thinking='enabled', effort='max'), sdk_factory=self.factory)
        client.chat(MESSAGES)
        request = self.sdk.chat.completions.create.call_args.kwargs
        self.assertNotIn('temperature', request)
        self.assertEqual(request['extra_body'], {'thinking':{'type':'enabled'}, 'reasoning_effort':'max'})

    def test_retryable_failures_backoff_and_no_fallback(self):
        class Retryable(Exception):
            status_code = 429
        self.sdk.chat.completions.create.side_effect = [Retryable('secret'), completion()]
        sleep = MagicMock()
        client = self.client(sleeper=sleep, jitter=lambda:0)
        client.chat(MESSAGES)
        self.assertEqual(client.budget.calls, 2)
        sleep.assert_called_once_with(.5)
        models = [c.kwargs['model'] for c in self.sdk.chat.completions.create.call_args_list]
        self.assertEqual(models, ['deepseek-flash']*2)
        self.assertNotIn('secret', repr(client.budget.records))
        self.assertEqual(self.sdk.__exit__.call_count, 2)

    def test_auth_invalid_and_retry_exhaustion_are_sanitized(self):
        for status, attempts in [(401,1),(403,1),(402,1),(400,1),(404,1),(422,1),(429,2),(503,2)]:
            class Failure(Exception):
                status_code = status
            self.factory.reset_mock(); self.sdk.chat.completions.create.reset_mock()
            self.sdk.chat.completions.create.side_effect = Failure('key=VERY_SECRET host query')
            client = self.client(sleeper=lambda _:None, jitter=lambda:0)
            with self.subTest(status=status), self.assertRaises(LLMError) as caught:
                client.chat(MESSAGES)
            self.assertNotIn('VERY_SECRET',str(caught.exception))
            self.assertEqual(self.sdk.chat.completions.create.call_count, attempts)

    def test_malformed_truncated_and_oversized_responses_never_retried(self):
        cases = [completion(''), completion('x'*16001), completion(choices=[]), completion(model=None),
            completion(choices=[NS(finish_reason='length', message=NS(content='SELECT'))]),
            completion(choices=[NS(finish_reason='stop',message=NS(content='OK',tool_calls=['bad']))]),
            completion(usage=NS(prompt_tokens=-1,completion_tokens=2,total_tokens=1))]
        for raw in cases:
            self.sdk.chat.completions.create.reset_mock(); self.sdk.chat.completions.create.return_value=raw
            with self.subTest(raw=str(raw)[:50]), self.assertRaises(LLMError):
                self.client().chat(MESSAGES)
            self.assertEqual(self.sdk.chat.completions.create.call_count,1)

    def test_json_contract_duplicate_keys_types_empty_and_nonfinite(self):
        good = {'language':'en','explanation':'A short summary.','sql':'SELECT 1'}
        self.sdk.chat.completions.create.return_value=completion(json.dumps(good))
        fields = dict(language=str, explanation=str, sql=str)
        self.assertEqual(self.client().chat_json(MESSAGES,fields),good)
        self.assertEqual(self.sdk.chat.completions.create.call_args.kwargs['response_format'],{'type':'json_object'})
        for content in ['{}', '{"sql":1}', '{"sql":""}', '{"sql":"SELECT 1","sql":"DELETE"}',
                        '{"sql":NaN}', '```json\n{}\n```']:
            self.sdk.chat.completions.create.return_value=completion(content)
            with self.subTest(content=content), self.assertRaises(LLMError):
                self.client().chat_json(MESSAGES,{'sql':str})

    def test_input_limits_streaming_and_missing_credentials_never_call_sdk(self):
        for messages, kwargs in [([{'role':'tool','content':'x'}],{}),
            ([{'role':'user','content':'x'*48001}],{}), (MESSAGES,{'stream':True}),
            ([{'role':'user','content':'hello'}],{'json_object':True}),
            (MESSAGES,{'temperature':float('nan')})]:
            with self.assertRaises(LLMError):
                self.client().chat(messages,**kwargs)
        with self.assertRaises(LLMError):
            LLMClient(settings=replace(self.settings,api_key=''),sdk_factory=self.factory).chat(MESSAGES)
        self.factory.assert_not_called()

    def test_budget_shared_between_stages_retries_and_concurrent_reservation(self):
        budget = CallBudget(2,30)
        with use_budget(budget):
            self.client(stage='SR').chat(MESSAGES)
            self.client(stage='CG').chat(MESSAGES)
            with self.assertRaisesRegex(LLMError,'budget exhausted'):
                self.client(stage='AN').chat(MESSAGES)
        self.assertEqual(self.factory.call_count,2)
        exhausted = CallBudget(1,30)
        success=[]
        def reserve():
            try:
                exhausted.reserve(); success.append(True)
            except LLMError:
                pass
        threads=[threading.Thread(target=reserve) for _ in range(10)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(success),1)

    def test_deadline_and_cancel_close_sdk_without_waiting_for_worker(self):
        release = threading.Event()
        entered = threading.Event()
        def blocking(**_):
            entered.set(); release.wait(2); return completion()
        self.sdk.chat.completions.create.side_effect=blocking
        budget = CallBudget(3,30)
        client=self.client(budget=budget)
        timer=threading.Timer(.1,budget.cancel);timer.start()
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(LLMError,'cancelled'):
                client.chat(MESSAGES)
            self.assertLess(time.monotonic()-started,.8)
            self.sdk.__exit__.assert_called_once()
            self.assertEqual(budget.calls,1)
        finally:
            release.set();timer.join()
        with self.assertRaises(LLMError):
            self.client(budget=CallBudget(1,1,started=time.monotonic()-2)).chat(MESSAGES)
        self.assertEqual(self.factory.call_count,1)

    def test_per_attempt_wall_deadline_does_not_retry(self):
        release=threading.Event()
        self.sdk.chat.completions.create.side_effect=lambda **_: (release.wait(2), completion())[1]
        settings=replace(self.settings,timeout=.08)
        client=LLMClient(settings=settings,sdk_factory=self.factory)
        try:
            with self.assertRaisesRegex(LLMError,'deadline'):
                client.chat(MESSAGES)
            self.assertEqual(client.budget.calls,1)
            self.assertTrue(client.budget.cancelled)
        finally:
            release.set()

    def test_session_generator_context_isolated_and_cli_operation_shared(self):
        @session_operation
        def callback(history, session_state):
            yield _ACTIVE.get()
            yield _ACTIVE.get()
        left,right={},{}
        a,b=callback([],left),callback([],right)
        first=next(a);second=next(b)
        self.assertIsNot(first,second)
        self.assertIsNone(_ACTIVE.get())
        self.assertIs(next(a),first)
        a.close();b.close()
        @pipeline_operation
        def operation():
            return self.client(stage='SR').budget is self.client(stage='CG').budget
        self.assertTrue(operation())

    def test_rates_use_returned_usage_and_never_invent_missing_cost(self):
        settings=load_llm_settings({'LLM_RATES_DATE':'2026-10-02','LLM_INPUT_CACHE_HIT_USD_PER_M':'.1',
            'LLM_INPUT_CACHE_MISS_USD_PER_M':'.2','LLM_OUTPUT_USD_PER_M':'.3'})
        usage={'prompt_tokens':10,'completion_tokens':2,'total_tokens':12,
               'prompt_cache_hit_tokens':3,'prompt_cache_miss_tokens':7}
        self.assertEqual(Decimal(estimate_cost(usage,settings,settings.model)['estimated_usd']),Decimal('.0000023'))
        self.assertIsNone(estimate_cost(usage,self.settings,self.settings.model))
        self.assertIsNone(estimate_cost(usage,settings,'deepseek-v4-pro'))
        self.assertIsNone(estimate_cost({'prompt_tokens':10},settings,settings.model))

    def test_generated_sql_contract_escaping_and_unique_tag(self):
        good={'language':'en','explanation':'Literal <FINAL_ANSWER> tag example.','sql':'SELECT 1'}
        formatted=format_generated_response(good)
        self.assertEqual(extract_final_sql(formatted),'SELECT 1')
        self.assertIn('&lt;FINAL_ANSWER&gt;',formatted)
        self.assertIsNone(extract_final_sql('<FINAL_ANSWER>A</FINAL_ANSWER><FINAL_ANSWER>B</FINAL_ANSWER>'))
        for change in [{'language':'english'},{'sql':'```sql SELECT 1```'},{'sql':'<FINAL_ANSWER>SELECT 1'}]:
            with self.assertRaises(LLMError):
                format_generated_response({**good,**change})

    def test_evaluation_requires_opt_in_and_translator_import_is_local(self):
        with self.assertRaisesRegex(LLMError,'EVALUATION'):
            self.client(stage='EV')
        with self.assertRaises(LLMError):
            self.client(stage='TR')
        import translator
        self.assertIsNone(translator.FULL_SCHEMA_DIGEST)
        self.factory.assert_not_called()

    def test_default_check_is_offline_and_live_without_key_is_not_run(self):
        with patch.dict(os.environ,{},clear=True), patch('src.llm_check.LLMClient') as constructor:
            output=io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(check_main([]),0)
                self.assertEqual(check_main(['--live']),0)
            constructor.assert_not_called()
        self.assertIn('not_run',output.getvalue())
        self.assertIn('not run',output.getvalue())

    def test_opt_in_smoke_lists_models_then_one_small_completion_with_no_retry(self):
        self.sdk.models.list.return_value=NS(data=[NS(id='deepseek-flash')])
        output=io.StringIO()
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'offline-test-only'},clear=True), \
             patch('src.llm_client.LLMClient._factory',return_value=self.factory), redirect_stdout(output):
            self.assertEqual(check_main(['--live']),0)
        self.sdk.models.list.assert_called_once()
        self.sdk.chat.completions.create.assert_called_once()
        request=self.sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(request['max_tokens'],64)
        self.assertEqual(self.factory.call_args.kwargs['max_retries'],0)
        self.assertIn('requested_model',output.getvalue())
        self.assertIn('returned_model',output.getvalue())
        self.assertNotIn('offline-test-only',output.getvalue())

    def test_smoke_unavailable_model_makes_no_completion(self):
        self.sdk.models.list.return_value=NS(data=[NS(id='unsupported-model')])
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'offline-test-only'},clear=True), \
             patch('src.llm_client.LLMClient._factory',return_value=self.factory), redirect_stdout(io.StringIO()):
            self.assertEqual(check_main(['--live']),1)
        self.sdk.chat.completions.create.assert_not_called()

    def test_config_immutable_and_pipeline_imports_make_no_sdk_calls_or_files(self):
        # Separate process avoids inherited imported modules masking import effects.
        root=Path(__file__).resolve().parent.parent
        code = """
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
with patch('os.makedirs', side_effect=AssertionError('import write')), patch('pathlib.Path.mkdir', side_effect=AssertionError('import write')):
    import config, translator, main1, main
try:
    config.CONFIG['LLM']['CG_MODEL'] = 'bad'
except TypeError:
    pass
else:
    raise AssertionError('mutable model configuration')
assert 'openai' not in sys.modules
"""
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,'-B','-c',code,str(root)],cwd=folder,
                env={**os.environ,'DEEPSEEK_API_KEY':'offline-test-only','LLM_PROVIDER':'deepseek'},
                capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_evaluator_scores_reject_missing_duplicate_out_of_range(self):
        from translator import parse_evaluation_scores
        good = '\n'.join(f'## {name} Score: 0.7' for name in ('Question','Schema','Match','Query'))
        self.assertEqual(parse_evaluation_scores(good)['query'],.7)
        for bad in ['missing', good+'\n## Query Score: 0.1', good.replace('Query Score: 0.7','Query Score: 7')]:
            with self.assertRaises(LLMError):
                parse_evaluation_scores(bad)


if __name__ == '__main__':
    unittest.main()
