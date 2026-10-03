"""Read-only compatibility maps. Server environment selects models, never visitors."""
from types import MappingProxyType
from src.settings import load_settings, SettingsError
from src.llm_settings import load_llm_settings

CONFIG = MappingProxyType({
    'LLM': load_llm_settings().public_models(),
    'DATABASE': MappingProxyType(load_settings().public_database_config()),
    'FLAGS': MappingProxyType({'ENABLE_DEBUG_LOGGING': False, 'USE_GPT_FALLBACK_FOR_CG': False}),
})


def set_models_for_provider(provider):
    """Legacy no-op for the configured provider; switching is explicitly refused."""
    if provider and provider.lower() != load_llm_settings().provider:
        raise SettingsError('Provider switching is disabled; configure LLM_PROVIDER on the server')
