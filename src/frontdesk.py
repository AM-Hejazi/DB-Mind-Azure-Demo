# === frontdesk.py ===
import os
import json
from dataclasses import dataclass
from src.llm_client import LLMClient, LLMError
from src.schema_context import stage_messages


def load_fd_prompt_template():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", "FD.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def load_fd_feedback_template():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", "FD_feedback.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def build_prompt(schema_block, sample_values_block, chat_history_str):
    template = load_fd_prompt_template()
    return (template
            .replace("{SCHEMA_BLOCK}", schema_block or "No schema available.")
            .replace("{SAMPLE_VALUES}", sample_values_block or "No values available.")
            .replace("{HISTORY}", chat_history_str or "None yet.")
            )


def fd_chat_step(chat_history, user_input, schema_block, sample_values_block, provider: str | None = None):
    """
    Runs a single turn of the frontdesk conversation.
    Returns:
        reply: LLM reply (markdown string)
        clarified: str or None — extracted <CLARIFIED> question
        updated_chat: full updated chat history
    """
    # Append user input before sending
    chat_history.append({"role": "user", "content": user_input})

    # Format conversation for prompt
    history_str = ""
    for msg in chat_history:
        prefix = "User" if msg["role"] == "user" else "Assistant"
        history_str += f"{prefix}: {msg['content']}\n"

    # Build prompt
    template = load_fd_prompt_template()
    prompt = (
        template.replace("{SCHEMA_BLOCK}", schema_block or "N/A")
                .replace("{SAMPLE_VALUES}", sample_values_block or "N/A")
                .replace("{HISTORY}", history_str)
    )

    llm = LLMClient(stage="FA", provider=provider)
    response = llm.chat(
        messages=stage_messages(template, {'SCHEMA_BLOCK': schema_block,
                                'SAMPLE_VALUES': sample_values_block, 'HISTORY': chat_history}),
        temperature=0.2,
    )

    reply = response.choices[0].message.content.strip()
    chat_history.append({"role": "assistant", "content": reply})

    # Extract <CLARIFIED> block
    clarified = None
    if "<CLARIFIED>" in reply and "</CLARIFIED>" in reply:
        start = reply.find("<CLARIFIED>") + len("<CLARIFIED>")
        end = reply.find("</CLARIFIED>")
        clarified = reply[start:end].strip()

    return reply, clarified, chat_history


@dataclass(frozen=True)
class FeedbackDecision:
    action: str
    reply: str
    question: str


def recent_feedback_history(history, limit=5000):
    """Keep whole recent messages; independently include the latest request below."""
    recent=[]
    for message in reversed(history):
        item={'role':message['role'], 'content':message['content'][:2000]}
        candidate=[item]+recent
        if len(json.dumps(candidate,ensure_ascii=False))>limit:break
        recent=candidate
    return recent


def fd_feedback(chat_history, final_question, sql_query, result_rows, selected_schema, provider: str | None = None):
    """Plan a follow-up. SQL is a proposal; this function never runs a query."""
    latest=next((message['content'] for message in reversed(chat_history)
                 if message.get('role')=='user'),None)
    if not latest:
        raise LLMError('malformed','A follow-up user request is required')
    schema_str=''
    if isinstance(selected_schema,dict) and selected_schema.get('tables'):
        from src.query_generator import build_schema_struct_from_json, format_schema_block
        schema_str=format_schema_block(build_schema_struct_from_json(
            selected_schema['tables'],selected_schema.get('columns',[])))
    preview=[list(row) for row in result_rows[:10]] if result_rows else []
    from src.settings import load_settings
    data={'latest_request':latest, 'final_question':final_question,
          'sql_query':sql_query, 'result_preview':preview, 'Selected Schema':schema_str,
          'chat_history_text':recent_feedback_history(chat_history),
          'dialect':load_settings().dialect}
    response=LLMClient(stage='FA',provider=provider).chat_json(
        stage_messages(load_fd_feedback_template(),data),
        {'action':str,'reply':str,'question':str},temperature=0.2)
    if (set(response)!={'action','reply','question'} or
        any(not isinstance(response[key],str) or not response[key].strip() for key in response)):
        raise LLMError('malformed','Feedback response has an invalid revision request')
    action=response['action']
    question=response['question'].strip()
    if action not in {'propose','explain','clarify'} or len(question)>2000:
        raise LLMError('malformed','Feedback response has an invalid revision request')
    decision=FeedbackDecision(action,response['reply'],question if action=='propose' else final_question)
    history=[*chat_history,{'role':'assistant','content':decision.reply}]
    return decision,history
