import os
import time
from src.logger import global_logger as logger
from config import CONFIG
from src.llm_client import LLMClient
from src.schema_context import stage_messages, load_context


def load_prompt_template():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(base_dir, "prompts", "QC.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def build_prompt(user_question, schema_context):
    template = load_prompt_template()
    return template.replace("{QUESTION}", user_question).replace("{DATABASE_SCHEMA}", schema_context or "No schema available.")

def clarify_question(user_question, schema_context=None, provider: str | None = None):
    import re
    selected = re.findall(r'Table\s+([\w.]+)', schema_context or '')
    context = load_context(user_question, selected if selected else None)

    start_time = time.time()
    llm = LLMClient(stage="QC", provider=provider)
    response = llm.chat(
        messages=stage_messages(load_prompt_template(), {'QUESTION':user_question,'DATABASE_SCHEMA':context},
                                'You are a strict schema-aware question clarifier.'),
        temperature=0.3,
    )
    end_time = time.time()
    duration = round(end_time - start_time, 2)
    logger.log("QC Model", llm.model_name)
    logger.log("QC Provider", llm.client_type)
    logger.log("QC Response Time", f"{duration} seconds")

    full_text = response.choices[0].message.content.strip()
    logger.log("Clarifier Output", full_text)

    is_clear = "<YES>" in full_text and "<NO>" not in full_text
    return full_text, is_clear

if __name__ == "__main__":
    example_question = "Show me the latest updates"
    fake_schema = """
- Table: ART
  - Columns: ARTBEZ, HIST_AEZEIT
"""
    result = clarify_question(example_question, fake_schema)
    print(result)
