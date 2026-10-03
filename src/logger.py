"""Session-local diagnostics. Default logs retain event names/codes, never raw content."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import re
from uuid import uuid4


def redact(text):
    text=str(text)
    text=re.sub(r'(?i)(?:api[_ -]?key|password|pwd|token|authorization)\s*[:=]\s*[^\s;,]+','[redacted secret]',text)
    text=re.sub(r'(?i)(?:server|data source|uid|user id)\s*=\s*[^;\n]+','[redacted connection]',text)
    text=re.sub(r'https?://[^\s]+|(?:[A-Za-z0-9-]+\.)+(?:internal|local|database\.windows\.net)\b','[redacted host]',text)
    return text


class PipelineLogger:
    def __init__(self, log_dir='var/diagnostics'):
        self.log_file=str(Path(log_dir)/f'{uuid4().hex}.json')
        self.lines=[]

    def log(self,title,content):
        # Existing callers pass prompts/results/errors. Do not persist their bodies.
        event=re.sub(r'[^A-Za-z0-9 _/-]','',str(title))[:80]
        self.lines.append({'event':event})
        self.lines=self.lines[-100:]

    def save(self):
        # Explicit operator write only, constrained to ignored diagnostics.
        root=Path(__file__).resolve().parent.parent/'var'/'diagnostics'
        path=Path(self.log_file).absolute()
        if path.parent!=root or not re.fullmatch(r'[a-f0-9]{32}\.json',path.name):
            return
        root.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(self.lines),encoding='utf-8')


_CURRENT=ContextVar('dbmind_session_logger',default=None)


@contextmanager
def use_logger(logger):
    token=_CURRENT.set(logger)
    try:yield logger
    finally:_CURRENT.reset(token)


class LoggerProxy:
    def log(self,title,content):
        current=_CURRENT.get()
        if current is not None:current.log(title,content)
    def save(self):
        current=_CURRENT.get()
        if current is not None:current.save()


# Compatibility symbol is a context proxy, never shared mutable storage.
global_logger=LoggerProxy()
