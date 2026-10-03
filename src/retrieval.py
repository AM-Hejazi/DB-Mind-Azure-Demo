import os
import re
import json
import time
from pathlib import Path
from typing import Dict, List
from src.logger import global_logger as logger
from config import CONFIG
from src.llm_client import LLMClient
from src.schema_context import stage_messages, resolve_table
from src.snapshots import SnapshotError



def load_schema_dict() -> Dict[str, Dict]:
    from src.settings import load_settings
    from src.snapshots import read_snapshot
    from src.schema_context import as_legacy
    return as_legacy(read_snapshot(load_settings()))


def load_schema_text(question=None, selected_tables=None) -> str:
    from src.schema_context import load_context
    return load_context(question, selected_tables)


def load_prompt_template():
    base_dir = Path(__file__).resolve().parent.parent
    prompt_path = base_dir / "prompts" / "SR.txt"
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()

def build_prompt(user_question, schema_text):
    template = load_prompt_template()
    return template.replace("{QUESTION}", user_question).replace("{SCHEMA}", schema_text)

def retrieve_schema_with_llm(user_question, provider: str | None = None):
    schema_text = load_schema_text(user_question)
    prompt = build_prompt(user_question, schema_text)

    start_time = time.time()
    sr_llm = LLMClient(stage="SR", provider=provider)
    response = sr_llm.chat(
        messages=stage_messages(load_prompt_template(), {"QUESTION": user_question, "SCHEMA": schema_text},
                                "You are a SQL schema selector assistant."),
        temperature=0.0
    )
    duration = round(time.time() - start_time, 2)
    logger.log("SR Model", sr_llm.model_name)
    logger.log("SR Response Time", f"{duration} seconds")

    full_output = response.choices[0].message.content.strip()

    complexity_match = re.search(r"<COMPLEXITY>(.*?)</COMPLEXITY>", full_output, re.DOTALL)
    complexity = complexity_match.group(1).strip() if complexity_match else "Difficult"

    explanation = full_output
    raw_schema_block = ""
    selected_tables: List[str] = []
    selected_columns: List[str] = []

    if "**SELECTED SCHEMA**" in full_output:
        parts = full_output.split("**SELECTED SCHEMA**")
        if parts:
            explanation = parts[0].strip()
        if len(parts) > 1:
            raw_schema_block = parts[1].strip()

        current_table = None
        for line in raw_schema_block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//"):
                continue
            if stripped.startswith("Ref:"):
                continue
            if stripped.startswith("**SELECTED SCHEMA**"):
                continue
            if stripped.startswith("<COMPLEXITY>"):
                continue
            if stripped.startswith("}"):
                current_table = None
                continue

            table_match = re.match(r"Table\s+([A-Za-z0-9_.]+)", stripped)
            if table_match:
                current_table = table_match.group(1).strip()
                if current_table not in selected_tables:
                    selected_tables.append(current_table)
                continue

            if current_table:
                column_token = stripped.split("//", 1)[0].strip()
                if not column_token:
                    continue
                column_name = column_token.split()[0].strip('"[]')
                full_column = f"{current_table}.{column_name}"
                if full_column not in selected_columns:
                    selected_columns.append(full_column)

    schema = load_schema_dict()
    selected_tables = [resolve_table(schema, name) for name in selected_tables]
    from src.schema_context import select_schema
    select_schema(schema, selected_tables, selected_columns)
    selected_payload = {
        "tables": selected_tables,
        "columns": selected_columns,
        "raw_block": raw_schema_block,
    }

    return {
        "full_output": full_output,
        "explanation": explanation,
        "selected_schema": raw_schema_block,
        "selected": selected_payload,
        "complexity": complexity
    }


if __name__ == "__main__":
    q = "How many customers from Germany ordered at least one item from the AUTO picking area last week?"
    result = retrieve_schema_with_llm(q)

    print("\n--- Full Output ---\n")
    print(result["full_output"])

    print("\n--- Explanation ---\n")
    print(result["explanation"])

    print("\n--- Selected Schema Block ---\n")
    print(result["selected_schema"])
