"""All work requires the authenticated server Request; browser history is never input."""
from src.ui.handlers_FD import callbacks


def bind_event_handlers(elements,session_state,service):
    submit,advance,reset=callbacks(service)
    outputs=[elements[k] for k in ('user_input','chatbot','status','table','sql')]
    options=dict(api_visibility='private',concurrency_limit=1,concurrency_id='private-demo')
    elements['send_btn'].click(submit,inputs=[elements['user_input']],outputs=outputs,**options).then(
        advance,inputs=[],outputs=outputs,**options)
    elements['user_input'].submit(submit,inputs=[elements['user_input']],outputs=outputs,**options).then(
        advance,inputs=[],outputs=outputs,**options)
    elements['reset_btn'].click(reset,inputs=[],outputs=outputs,**options)
