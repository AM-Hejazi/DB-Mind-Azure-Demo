import gradio as gr

def default_session_state():
    return gr.State({
        # Core logic state
        "original_q": "",
        "initial_user_query": "",
        "qc_feedback": "",
        "user_rewrite": "",
        "final_q": "",
        "explanation": "",
        "schema_result": None,
        "sql_query": "",
        "final_result": "",
        "fd_history": [],
        # "is_clear": False,
        "complexity": "",
        "has_welcomed": False,

        # Feedback logic
        "feedback_rating": None,
        "feedback_text": "",
        "feedback": False,
        "last_yes_context": None,
        "fd_feedback_history": [],

        # UI visibility flags
        "show_log_file": False,
        "show_feedback_rating": False,

        # Question limit tracking
        "question_count": 0,
        "max_questions": 2,
        "question_started": False,
        "limit_reached": False,
        "skip_pipeline": False,
        # Legacy display value; server settings choose provider and stage models
        "model_choice": "deepseek",
    })


WELCOME = ('Ask about fictional maintenance records in English or German. '
           'Choose an example below. Reset before starting a new question. '
           'Fragen auf Deutsch sind willkommen. Alle Daten sind frei erfunden.')
