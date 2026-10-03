import os
import time
from src.logger import global_logger as logger
from config import CONFIG
from src.llm_client import LLMClient
from src.schema_context import stage_messages, load_context


def load_prompt_template():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", "FA.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def build_prompt(Original_Q_For_FA_Prompt, Finalized_q, sql_query, user_feedback): # Update signature
    template = load_prompt_template()
    return (
        template
        .replace("{ORIGINAL_QUESTION}", Original_Q_For_FA_Prompt.strip()) # Use the truly original question if FA.txt uses {ORIGINAL_QUESTION}
        .replace("{FINALIZED_QUESTION}", Finalized_q.strip()) # Use this for the current question
        .replace("{SQL_QUERY}", sql_query.strip())
        .replace("{USER_FEEDBACK}", user_feedback.strip())
    )


def generate_updated_question(Original_Q_For_FA_Prompt, Finalized_q, sql_query, user_feedback, provider: str | None = None):
    prompt = build_prompt(Original_Q_For_FA_Prompt, Finalized_q, sql_query, user_feedback)

    logger.log("FA Prompt", prompt)
    model_name = CONFIG["LLM"]["FA_MODEL"]
    start_time = time.time()
    llm = LLMClient(stage="FA", provider=provider)
    response = llm.chat(
        messages=stage_messages(load_prompt_template(), {'ORIGINAL_QUESTION':Original_Q_For_FA_Prompt,
                                'FINALIZED_QUESTION':Finalized_q, 'SQL_QUERY':sql_query, 'USER_FEEDBACK':user_feedback,
                                'SCHEMA':load_context(Finalized_q+' '+user_feedback)},
                                'Update the question based on results and user corrections.'),
        temperature=0.1,
    )
    duration = round(time.time() - start_time, 2)

    logger.log("FA Model", llm.model_name)
    logger.log("FA Provider", llm.client_type)
    logger.log("FA Response Time", f"{duration} seconds")
    content = response.choices[0].message.content.strip()

    # Extract updated question from <UPDATED_QUESTION> tags
    updated = None
    if "<UPDATED_QUESTION>" in content and "</UPDATED_QUESTION>" in content:
        start = content.find("<UPDATED_QUESTION>") + len("<UPDATED_QUESTION>")
        end = content.find("</UPDATED_QUESTION>")
        updated = content[start:end].strip()
    # NEW: Handle cases where LLM might generate SQL or other tags unexpectedly
    elif "<FINAL_ANSWER>" in content:  # If LLM included SQL
        logger.log("FA Extraction Warning", "LLM included FINAL_ANSWER tag. Attempting to find UPDATED_QUESTION.")
        start = content.find("<UPDATED_QUESTION>")
        if start != -1:  # If UPDATED_QUESTION is still present
            end = content.find("</UPDATED_QUESTION>")
            updated = content[start + len("<UPDATED_QUESTION>"): end].strip()
        else:  # If no UPDATED_QUESTION tag, use the full content as a fallback
            logger.log("FA Extraction Warning", "No <UPDATED_QUESTION> found with FINAL_ANSWER. Using full content.")
            updated = content.strip()

    if not updated:  # Final fallback if no specific tag was found
        logger.log("FA Extraction Failure",
                   "No <UPDATED_QUESTION> tag found. Using full LLM response as updated question.")
        updated = content.strip()

    logger.log("FA Updated Question", updated)
    return updated
