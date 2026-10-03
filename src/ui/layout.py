"""Focused private synthetic maintenance demo; trusted embedded logo and no client state."""
import base64
import gradio as gr
from src.settings import ROOT
from synthetic_demo.questions import reference_cases
from src.ui.events import bind_event_handlers

from src.ui.state import WELCOME


HEADER_CSS = """
.dbmind-header {display: flex; align-items: center; gap: 16px; margin-bottom: 8px;}
.dbmind-header img {height: 48px; width: auto; flex-shrink: 0; object-fit: contain;}
.dbmind-header .dbmind-heading {min-width: 0;}
.dbmind-header h1 {margin: 0; font-size: 2rem; line-height: 1.15; font-weight: 700;}
.dbmind-header p {margin: 6px 0 0; font-size: 1rem; line-height: 1.45;}
@media (max-width: 600px) {
    .dbmind-header {gap: 12px;}
    .dbmind-header img {height: 36px;}
    .dbmind-header h1 {font-size: 1.5rem;}
    .dbmind-header p {font-size: .875rem;}
}
"""


SESSION_TIMER_JS = """
void (async () => {
    if (window.dbmindSessionTimer) return;
    const expired = 'Your 30-minute demo session has ended. Please contact the developer to request more access.';
    let deadline;
    let busy = false;
    let lastCheck = 0;
    const stop = () => {
        clearInterval(window.dbmindSessionTimer);
        document.body.replaceChildren();
        const message = document.createElement('p');
        message.textContent = expired;
        message.style.cssText = 'font: 18px/1.6 system-ui; max-width: 640px; margin: 64px auto; padding: 24px;';
        document.body.append(message);
    };
    const check = async () => {
        if (deadline && Date.now() >= deadline) return stop();
        if (busy || Date.now() - lastCheck < 15000) return;
        lastCheck = Date.now();
        busy = true;
        try {
            const response = await fetch('/demo/session', {credentials: 'same-origin', cache: 'no-store'});
            if (response.status === 403 || response.status === 401) return stop();
            if (response.ok) {
                const data = await response.json();
                const next = Date.now() + data.remaining_seconds * 1000;
                deadline = deadline ? Math.min(deadline, next) : next;
            }
        } finally { busy = false; }
    };
    window.dbmindSessionTimer = setInterval(() => {check().catch(() => {});}, 1000);
    await check();
})().catch(() => {});
"""


def header_html():
    # Fixed, repository-owned asset only. No visitor path or Gradio file route.
    logo=base64.b64encode((ROOT/'data/icon.png').read_bytes()).decode('ascii')
    return (f'<header class="dbmind-header"><img src="data:image/png;base64,{logo}" alt="DB-Mind">'
            '<div class="dbmind-heading"><h1>DB-Mind</h1>'
            '<p>Synthetic maintenance / Fiktive Instandhaltung</p></div></header>')


def create_ui(service):
    settings=service.settings
    provider=('Offline fixture responses (no LLM)' if service.mode=='mock' else
              f'DeepSeek · {service.llm_settings.model} · stage overrides configured on server')
    with gr.Blocks(title='DB Mind · Synthetic maintenance',analytics_enabled=False) as demo:
        gr.HTML(header_html(),css_template=HEADER_CSS,apply_default_css=False,js_on_load=SESSION_TIMER_JS)
        gr.Markdown(f'**{provider}** · **{settings.dialect}** · fixed reference: 1 October 2026 UTC\n\n'
                    f'Read queries only · at most {settings.max_rows:,} rows and {settings.max_bytes:,} result bytes. '
                    'Each browser visit lasts up to 30 minutes. After expiry, contact the developer for more access. Question allowances apply per visit; reset does not replenish them.')
        chatbot=gr.Chatbot(value=[{'role':'assistant','content':WELCOME}],
                           label='Conversation / Gespräch',height=340,sanitize_html=True,buttons=['copy'],allow_file_downloads=False)
        status=gr.Markdown('Ready / Bereit',label='Pipeline status')
        with gr.Row():
            user_input=gr.Textbox(label='Question / Frage',placeholder='Choose an example or ask a maintenance question',
                                  lines=2,max_lines=5,scale=5,max_length=2000)
            send_btn=gr.Button('Send',variant='primary',scale=1)
            reset_btn=gr.Button('New question / Neue Frage',scale=1)
        table=gr.Dataframe(label='Query results / Abfrageergebnis',interactive=False,wrap=True,
                           max_height=300)
        with gr.Accordion('Executed SQL / Ausgeführtes SQL',open=False):
            sql=gr.Code(label='Read query',language='sql',interactive=False)
        gr.Markdown('If feedback proposes another query, review it and type **/execute** to confirm. '
                    'Empty results are successful reads. Live mode sends your question and bounded synthetic '
                    'schema/samples to DeepSeek; mock mode makes no model request.')
        # Bind before examples so private queue callback indexes remain stable.
        elements=dict(user_input=user_input,chatbot=chatbot,status=status,table=table,sql=sql,
                      send_btn=send_btn,reset_btn=reset_btn)
        bind_event_handlers(elements,None,service)
        with gr.Accordion('Grounded English and German examples',open=True):
            gr.Examples(examples=[[c[lang]] for c in reference_cases() for lang in ('question_en','question_de')],
                        inputs=user_input,label='Click to fill the question; then press Send',cache_examples=False,api_visibility='private')
    return demo
