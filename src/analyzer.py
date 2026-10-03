# analyzer.py
import os, time
import re
from src.llm_client import LLMClient
from config import CONFIG
from src.settings import load_settings
from src.logger import global_logger as logger
from src.query_generator import format_schema_block, build_schema_struct_from_json, _coerce_selected, format_generated_response, extract_final_sql, extract_explanation
from src.schema_context import stage_messages


def analyze_failure(
    question: str,
    selected_schema: dict,
    sql_query: str,
    error_message: str,
) -> tuple[str | None, str | None]:
    if not question:
        logger.log("Analyzer Error", "Missing 'question' input (None received).")
        return None, None

    # Format subset
    selected_schema = _coerce_selected(selected_schema)
    schema_block = format_schema_block(build_schema_struct_from_json(
        selected_schema.get("tables", []),
        selected_schema.get("columns", [])
    ))

    structured_context = f"""
    === Clarified Question ===
    {question.strip()}

    === Selected Schema Block ===
    {schema_block.strip()}

    === CG-Generated SQL ===
    {sql_query.strip()}

    === SQL Server Error ===
    {(error_message or 'No error message returned from validator.').strip()}
    """

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_template_path = os.path.join(base_dir, "prompts", "AN.txt")
    if not os.path.exists(prompt_template_path):
        return None, None

    with open(prompt_template_path, "r", encoding="utf-8") as f:
        template = f.read()

    full_prompt = template.replace("{LOG_CONTENT}", structured_context.strip())

    llm = LLMClient(stage="AN")
    start_time = time.time()
    try:
        data = llm.chat_json(
            messages=stage_messages(template, {'LOG_CONTENT': {'Question': question, 'Schema': schema_block,
                                    'SQL': sql_query, 'Error': error_message}},
                                    f'Correct SQL for the configured {load_settings().dialect} dialect.'),
            fields={"language": str, "explanation": str, "sql": str}, temperature=0.0
        )
    except Exception  as e:
        logger.log("Analyzer LLM Call Failed", str(e))
        return None, None

    duration = round(time.time() - start_time, 2)
    print(f"Analyzer call took {duration} seconds.")
    formatted = format_generated_response(data)
    return extract_final_sql(formatted), extract_explanation(formatted)
