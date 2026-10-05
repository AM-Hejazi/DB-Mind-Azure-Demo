"""Private-demo session orchestration; client history/state never authorizes work."""
from contextlib import contextmanager
from html import escape
import os

from src.access import AccessError, private_scope
from src.database import Database, DatabaseError
from src.llm_client import new_budget, use_budget, LLMError
from src.logger import use_logger, redact
from src.settings import load_settings
from src.sql_gate import prepare_read, SQLGateError
from src.snapshots import SnapshotError, read_snapshot


def display(text):
    return escape(redact(text),quote=True)


class SessionService:
    def __init__(self,policy,*,database_factory=Database,settings=None,llm_settings=None,mode=None):
        self.policy=policy
        self.database_factory=database_factory
        from src.llm_settings import load_llm_settings
        self.settings=settings or load_settings()
        self.llm_settings=llm_settings or load_llm_settings()
        self.mode=mode or os.environ.get('APP_LLM_MODE','live')
        if self.settings.profile == 'azure_sql_custom' and self.mode != 'live':
            from src.settings import SettingsError
            raise SettingsError('Custom databases require APP_LLM_MODE=live; fixture mock responses are disabled')
        if self.mode not in {'mock','live'}:
            from src.settings import SettingsError
            raise SettingsError('APP_LLM_MODE must be mock or live')

    def session(self,request):
        # Gradio injects this Request, including the auth_dependency username.
        user=getattr(request,'username',None)
        key=getattr(request,'session_hash',None)
        visitor=getattr(getattr(request,'request',None),'scope',{}).get('dbmind_visit')
        return self.policy.session(user,key,visitor)

    @contextmanager
    def operation(self,session):
        if not session.lock.acquire(blocking=False):raise AccessError('This session already has an operation in progress')
        try:
            from src.settings import configured as database_configured
            from src.llm_settings import configured as model_configured
            with private_scope(self.policy,session.owner,session.visitor),use_logger(session.logger),database_configured(self.settings),model_configured(self.llm_settings):
                yield
        finally:
            session.state.pop('_progress',None)
            session.lock.release()

    def submit(self,message,request):
        if not isinstance(message,str) or not message.strip() or len(message)>2000:
            raise AccessError('Ask a nonempty question of at most 2,000 characters')
        session=self.session(request)
        with self.operation(session):
            state=session.state
            if not state.get('question_started'):
                self.policy.begin_question(session.owner,session.visitor)
                state.update(question_started=True,question=message,final_q='',fd_history=[],
                             llm_budget=new_budget(self.llm_settings),query_attempts=0,feedback_turns=0,pending_sql=None)
            else:
                state['feedback_turns']=state.get('feedback_turns',0)+1
                if state['feedback_turns']>5:raise AccessError('Question follow-up allowance reached; reset to ask a new question')
            state['pending_message']=message
            session.history.append({'role':'user','content':display(message)})
            session.history=session.history[-40:]
        return session

    def _query(self,session,sql):
        self.policy.allowance_key(session.owner,session.visitor)
        state=session.state
        if state.get('query_attempts',0)>=3:raise AccessError('Query attempt allowance reached')
        state['query_attempts']=state.get('query_attempts',0)+1
        budget=state['llm_budget']
        budget.remaining()
        settings=load_settings()
        from dataclasses import replace
        settings=replace(settings,query_timeout=min(settings.query_timeout,max(.01,budget.remaining())))
        executed=prepare_read(sql,settings)
        result=self.database_factory(settings).query(executed,cancelled=budget.cancellation)
        state['executed_sql']=executed
        return result

    def advance(self,request,*,progress=None):
        if not self.policy.operations.acquire(blocking=False):raise AccessError("Private-demo operation capacity reached")
        try:return self._advance(request,progress=progress)
        finally:self.policy.operations.release()

    def _ensure_schema(self,session):
        """Refresh invalid context at the authenticated, serialized server boundary."""
        self.policy.allowance_key(session.owner,session.visitor)
        budget=session.state['llm_budget']
        budget.remaining()
        try:
            read_snapshot(self.settings)
            return
        except SnapshotError:
            pass
        self._progress(session,'Refreshing database context')
        from src.catalog import Reader
        from src.discovery import refresh
        import time
        def check():
            self.policy.allowance_key(session.owner,session.visitor)
            budget.remaining()
        reader=Reader(self.settings,
            deadline=time.monotonic()+min(self.settings.discovery_timeout_seconds,budget.remaining()),
            database_factory=self.database_factory,before_query=check,cancelled=budget.cancellation)
        refresh(self.settings,reader)
        check()
        read_snapshot(self.settings)

    def _advance(self,request,*,progress=None):
        session=self.session(request)
        with self.operation(session):
            state=session.state
            if not state.get('question_started') or 'pending_message' not in state:
                raise AccessError('Submit a question before running the pipeline')
            if progress:state['_progress']=progress
            message=state.pop('pending_message')
            with use_budget(state['llm_budget']):
                try:
                    self._ensure_schema(session)
                    if self.mode=='mock':
                        self._mock_answer(session,message)
                    elif state.get('finished'):
                        self._feedback(session,message)
                    else:self._answer(session,message)
                except SnapshotError:
                    self._progress(session,'Database context unavailable')
                    session.logger.log('Database context unavailable','snapshot')
                    session.history.append({'role':'assistant','content':
                        'Database context could not be refreshed. Please contact the operator; a narrower question will not resolve this.'})
                except (DatabaseError,SQLGateError,LLMError,AccessError) as error:
                    self._progress(session,'Operation stopped')
                    session.logger.log('Operation stopped',getattr(error,'code','policy'))
                    session.history.append({'role':'assistant','content':display(str(error))})
                except Exception:
                    self._progress(session,'Operation stopped')
                    session.logger.log('Operation error','internal')
                    session.history.append({'role':'assistant','content':'The operation failed. Reset and try a narrower question.'})
        return session

    def _answer(self,session,message):
        self._progress(session,'Clarifying question')
        from src.frontdesk import fd_chat_step
        from src.retrieval import load_schema_text,retrieve_schema_with_llm
        from src.query_generator import generate_sql_query,extract_final_sql,extract_explanation
        from src.analyzer import analyze_failure
        state=session.state
        reply,clarified,history=fd_chat_step(state['fd_history'],message,load_schema_text(message),('Bounded configured database samples' if self.settings.profile == 'azure_sql_custom' else 'Bounded synthetic samples only'))
        state['fd_history']=history[-20:]
        if not clarified:
            self._progress(session,'Clarification needed')
            session.history.append({'role':'assistant','content':display(reply)})
            return
        state['final_q']=clarified
        self._progress(session,'Selecting schema')
        selected=retrieve_schema_with_llm(clarified)
        state['selected']=selected.get('selected',{})
        state['complexity']=selected.get('complexity','Difficult')
        self._progress(session,'Generating SQL')
        generated=generate_sql_query(clarified,state['selected'],selected.get('complexity','Difficult'))
        sql=extract_final_sql(generated)
        if not sql:raise LLMError('malformed','No validated SQL response was produced')
        explanation=extract_explanation(generated) or ''
        self._progress(session,'Validating and executing read query')
        # Exactly one recovery, only on an actual query error; empty reads are success.
        try:rows,columns=self._query(session,sql)
        except DatabaseError as error:
            if error.code!='query':raise
            self._progress(session,'Correcting one failed query')
            corrected,summary=analyze_failure(clarified,state['selected'],sql,str(error))
            if not corrected:raise LLMError('malformed','Correction produced no validated SQL')
            rows,columns=self._query(session,corrected)
            sql,explanation=corrected,summary or ''
        state.update(finished=True,sql=sql,rows=rows,columns=columns,pending_sql=None)
        session.history.append({'role':'assistant','content':display(explanation)})
        self._result(session,rows,columns)

    def _progress(self,session,stage):
        session.state['stage']=stage
        callback=session.state.get('_progress')
        if callback:callback(stage)

    def _mock_answer(self,session,message):
        """Exact bilingual fixture routing, explicitly not an LLM or semantic benchmark."""
        from synthetic_demo.questions import reference_cases
        self._progress(session,'Matching offline reference fixture')
        case=next((c for c in reference_cases() if message.strip() in (c['question_en'],c['question_de'])),None)
        if not case:
            session.history.append({'role':'assistant','content':'Mock mode supports the listed English/German reference examples only. Choose an example, or configure live DeepSeek mode.'})
            return
        if case['behavior'] in {'clarify','reject'}:
            self._progress(session,'Clarification needed' if case['behavior']=='clarify' else 'Read-only request rejected')
            session.history.append({'role':'assistant','content':case['expected_response']})
            return
        sql=case['sql_sqlite' if self.settings.dialect=='sqlite' else 'sql_sqlserver']
        self._progress(session,'Validating and executing reference SQL')
        rows,columns=self._query(session,sql)
        session.state.update(finished=True,final_q=message,sql=sql,rows=rows,columns=columns,pending_sql=None)
        session.history.append({'role':'assistant','content':'Offline fixture response: '+display(case['id'])+'. No model was called.'})
        self._result(session,rows,columns)

    def _result(self,session,rows,columns):
        self._progress(session,'Result ready')
        text=f'{len(rows)} row(s) returned. See the result table and expandable SQL below.'
        if not rows:text='No matching rows — successful empty result. No correction was attempted.'
        session.history.append({'role':'assistant','content':text})

    def _feedback(self,session,message):
        from src.frontdesk import fd_feedback
        state=session.state
        if message.strip().lower()=='/execute':
            proposed=state.pop('pending_sql',None)
            question=state.pop('pending_question',None)
            if not proposed:raise AccessError('No pending query to confirm')
            self._progress(session,'Validating and executing approved read query')
            try:rows,columns=self._query(session,proposed)
            except DatabaseError as error:
                if error.code!='query' or state.get('feedback_repairs',0)>=1:raise
                state['feedback_repairs']=state.get('feedback_repairs',0)+1
                self._progress(session,'Analyzing failed revised query')
                from src.analyzer import analyze_failure
                corrected,summary=analyze_failure(question or state['final_q'],state['selected'],proposed,str(error))
                if not corrected:raise LLMError('malformed','Analyzer produced no validated repair proposal')
                self._propose_revision(session,corrected,question or state['final_q'],
                    'The approved query failed. The Analyzer proposes this repair for review. '+(summary or ''))
                return
            state.update(sql=proposed,rows=rows,columns=columns,
                         final_q=question or state['final_q'])
            self._result(session,rows,columns)
            return
        # Any new conversational turn supersedes the prior unexecuted proposal.
        # A failed, ambiguous or cancelled revision must not leave stale SQL armed.
        state.pop('pending_sql',None)
        state.pop('pending_question',None)
        history=state.setdefault('fd_feedback_history',[])
        history.append({'role':'user','content':message})
        self._progress(session,'Reviewing follow-up request')
        decision,history=fd_feedback(history,state['final_q'],state['sql'],state['rows'],state['selected'])
        state['fd_feedback_history']=history[-20:]
        session.logger.log('Feedback action',decision.action)
        if decision.action=='propose':
            from src.query_generator import generate_sql_query,extract_final_sql
            self._progress(session,'Generating revised SQL')
            generated=generate_sql_query(decision.question,state['selected'],state.get('complexity','Difficult'),
                revision_context={'previous_question':state['final_q'],'previous_sql':state['sql'],'latest_request':message})
            proposed=extract_final_sql(generated)
            if not proposed:raise LLMError('malformed','Query Generator produced no revised query')
            self._propose_revision(session,proposed,decision.question,decision.reply)
        else:
            self._progress(session,'Follow-up response ready')
            session.history.append({'role':'assistant','content':display(decision.reply)})

    def _propose_revision(self,session,sql,question,reply):
        self._progress(session,'Validating proposed read query')
        prepare_read(sql,load_settings())
        session.state.update(pending_sql=sql,pending_question=question)
        self._progress(session,'Query revision awaiting confirmation')
        session.history.append({'role':'assistant','content':display(reply)+
            '\nReview this proposed read query, then type /execute to confirm.\n<pre>'+display(sql)+'</pre>'})

    def reset(self,request):
        session=self.session(request)
        budget=session.state.get('llm_budget')
        if budget:budget.cancel()
        with self.operation(session):
            session.state.clear();session.history.clear();session.logger.lines.clear()
        return session
