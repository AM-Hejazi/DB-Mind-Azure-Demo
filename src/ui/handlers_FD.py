"""Bounded progress stream; context and mutable session work remain inside one worker."""
from queue import Queue,Empty
from threading import Thread
import gradio as gr
from src.access import AccessError
from src.ui.state import WELCOME

welcome_message=WELCOME


def callbacks(service):
    def outputs(session=None,*,history=None,status=None):
        state=session.state if session else {}
        columns=state.get('columns',[])
        data={'headers':columns,'data':[list(r) for r in state.get('rows',[])]}
        return ('',list(session.history) if session else history or [],
                status or state.get('stage','Ready / Bereit'),
                gr.update(value=data if columns else None),state.get('executed_sql',state.get('sql','')))

    def handle_user_input(message,request:gr.Request):
        try:return outputs(service.submit(message,request),status='Queued / In Warteschlange')
        except AccessError as error:return outputs(history=[{'role':'assistant','content':str(error)}],status='Request stopped')

    def process_next_step(request:gr.Request):
        events=Queue(maxsize=16)
        def work():
            session=None
            try:
                session=service.session(request)
                events.put(('done',service.advance(request,progress=lambda stage:events.put(('stage',stage)))))
            except AccessError as error:events.put(('error',str(error)))
            except Exception:events.put(('error','The operation failed. Reset and try again.'))
        Thread(target=work,daemon=True,name='dbmind-private-operation').start()
        while True:
            try:kind,value=events.get(timeout=.5)
            except Empty:continue
            if kind=='stage':
                yield gr.skip(),gr.skip(),value,gr.skip(),gr.skip()
            elif kind=='done':
                yield outputs(value)
                return
            else:
                yield outputs(history=[{'role':'assistant','content':value}],status='Operation stopped')
                return

    def reset_state(request:gr.Request):
        try:
            service.reset(request)
            return outputs(history=[{'role':'assistant','content':welcome_message}])
        except AccessError as error:return outputs(history=[{'role':'assistant','content':str(error)}],status='Reset stopped')

    return handle_user_input,process_next_step,reset_state


def get_latest_log():return None
