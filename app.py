"""Private ASGI serving boundary; imports never build/launch a UI or contact services."""
from src.access import AccessPolicy,SecureASGI
from src.settings import ROOT,SettingsError


def build_demo(policy=None,*,service=None,settings=None,llm_settings=None,mode=None):
    policy=policy or AccessPolicy()
    if not policy.settings.users:
        raise SettingsError('Configure private access before building the serving application')
    from src.service import SessionService
    from src.ui.layout import create_ui
    demo=create_ui(service or SessionService(policy,settings=settings,llm_settings=llm_settings,mode=mode))
    demo.queue(max_size=policy.settings.queue_size,default_concurrency_limit=1,api_open=False)
    return demo


def create_secure_app(policy=None,*,service=None,settings=None,llm_settings=None,mode=None,readiness=None):
    policy=policy or AccessPolicy()
    if not policy.settings.users:
        raise SettingsError('Configure private access before serving')
    import gradio as gr
    from fastapi import FastAPI,Request
    def authenticated_user(request:Request):
        # Outer middleware supplies this value after validating HTTP Basic auth.
        return request.scope.get('dbmind_principal')
    demo=build_demo(policy,service=service,settings=settings,llm_settings=llm_settings,mode=mode)
    inner=gr.mount_gradio_app(FastAPI(),demo,path='/',auth_dependency=authenticated_user,
        allowed_paths=[],blocked_paths=[str(ROOT)],footer_links=[],show_error=False,enable_monitoring=False,mcp_server=False,run_history=False,ssr_mode=False,
        css='.gradio-container {width: 100% !important; max-width: 1100px !important; min-width: 0 !important; margin: auto;}')
    return SecureASGI(inner,policy,readiness=readiness)


def main():
    import os
    import uvicorn
    # Explicit startup reads the selected DB; SQL Server profiles have network/auth side effects.
    from src.discovery import preflight
    from src.settings import load_settings
    policy=AccessPolicy()
    if not policy.settings.users:raise SettingsError('Private credentials are required before startup')
    import threading
    import time
    from src.llm_settings import load_llm_settings
    settings=load_settings()
    models=load_llm_settings()
    readiness=threading.Event()
    application=create_secure_app(policy,settings=settings,llm_settings=models,readiness=readiness)
    def prepare():
        # Bounded startup retries only. Probes never contact the DB or an LLM.
        for attempt in range(3):
            try:
                preflight(settings)
                readiness.set()
                print('DB-Mind startup ready',flush=True)
                return
            except Exception:
                print('DB-Mind startup preflight failed; check configuration and cache',flush=True)
                if attempt<2:time.sleep(10)
    threading.Thread(target=prepare,daemon=True,name='dbmind-startup').start()
    # Gradio loads many assets in parallel. Uvicorn counts idle keep-alive
    # connections too; 16 slots rejected legitimate browser loads behind ingress.
    # Query execution remains serialized separately by the queue/access policy.
    uvicorn.run(application,host=os.environ.get('APP_HOST','127.0.0.1'),port=int(os.environ.get('PORT','7860')),
                access_log=False,limit_concurrency=128,timeout_keep_alive=2)


if __name__=='__main__':main()
