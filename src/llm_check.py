"""Configuration-only by default. --live lists models and makes ONE tiny request."""
from dataclasses import replace
import argparse
import json
from src.access import operator_scope
from src.llm_client import LLMClient, LLMError, _error, use_budget
from src.llm_settings import load_llm_settings
from src.settings import SettingsError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Opt in to DeepSeek network calls and one chargeable completion')
    args = parser.parse_args(argv)
    try:
        settings = load_llm_settings()
        if not args.live:
            print(json.dumps({'provider': settings.provider, 'model': settings.model,
                              'stage_models': dict(settings.stages), 'thinking': settings.thinking,
                              'live_check': 'not_run'}))
            return 0
        if not settings.api_key:
            print('Live check not run: DEEPSEEK_API_KEY is absent')
            return 0
        settings = replace(settings, retries=0, max_tokens=64, thinking="disabled", max_output_chars=512)
        client = LLMClient(settings=settings)
        factory = client._factory()
        with operator_scope(), use_budget(client.budget):
            client.budget.reserve()
            timeout = min(settings.timeout, client.budget.remaining())
            # Model listing is also bounded by the request wall-clock helper.
            class ModelsAdapter:
                def __init__(self, **kwargs):
                    self.sdk = factory(**kwargs)
                    from types import SimpleNamespace
                    self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: self.sdk.models.list()))
                def __enter__(self):
                    self.sdk.__enter__()
                    return self
                def __exit__(self, *args):
                    return self.sdk.__exit__(*args)
            available = client._request(ModelsAdapter, {}, timeout)
            ids = {m.id for m in available.data if isinstance(m.id, str)}
            requested = set(dict(settings.stages).values())
            if not requested <= ids:
                raise LLMError('model_unavailable', 'A configured stage model is absent from the account model list')
            response = client.chat([{'role': 'user', 'content': 'Reply only with OK.'}])
            print(json.dumps({'live_check': 'passed', 'configured_models_available': True,
                              'requests': client.budget.records, 'completion_content_validated': True}))
            return 0
    except (SettingsError, LLMError) as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        code, _ = _error(exc)
        print(f'Live check failed ({code}); no fallback was used')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
