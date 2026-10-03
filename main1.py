import time
import csv
import os
from datetime import datetime
from src.retrieval import retrieve_schema_with_llm
from src.query_generator import generate_sql_query, extract_final_sql, extract_explanation
from src.validator import validate_sql
from src.analyzer import analyze_failure
from src.logger import global_logger as logger
from config import CONFIG
from src.llm_client import pipeline_operation
from translator import run_translator_agent
import sys

EVAL_INPUT_PATH = "Evaluation/Evaluation_Questions.csv"
LOG_OUTPUT_DIR = "Evaluation/ev_logs"


def read_questions(filepath):
    with open(filepath, newline='', encoding='ISO-8859-1') as csvfile:
        reader = csv.DictReader(csvfile)
        questions = [
            (
                row["By"].strip(),
                row["Category"].strip(),
                row["Complexity"].strip(),
                row["Question"].strip(),
            )
            for row in reader
        ]
    return questions

def run_llm_match_score_with_schema(original: str, translated: str, selected_schema: dict) -> float:
    from src.llm_client import LLMClient
    from src.query_generator import format_schema_block, build_schema_struct_from_json

    # Build context block
    schema_struct = build_schema_struct_from_json(
        selected_schema.get("tables", []),
        selected_schema.get("columns", [])
    )
    schema_block = format_schema_block(schema_struct)

    from src.schema_context import stage_messages
    from src.llm_client import LLMError
    import math
    client = LLMClient(stage="EV")
    response = client.chat(messages=stage_messages(
        "Compare the two questions against the synthetic schema. Return only a number between 0 and 1.",
        {"Original question": original, "Translated question": translated, "Schema": schema_block},
    ))
    try:
        score = float(response.choices[0].message.content.strip())
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError
    except (ValueError, TypeError):
        raise LLMError("malformed", "Evaluator returned an invalid similarity score") from None
    return round(score, 4)


def save_evaluation_result(row_data, csv_path="Evaluation/results/evaluation_metrics.csv"):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    write_header = not os.path.exists(csv_path)

    with open(csv_path, mode="a", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row_data.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row_data)

@pipeline_operation
def run_pipeline(by, category, original_complexity, question):
    from src.llm_settings import load_llm_settings
    if not load_llm_settings().evaluation_enabled:
        raise RuntimeError("Explicit LLM_ENABLE_EVALUATION=true is required")
    os.makedirs(LOG_OUTPUT_DIR, exist_ok=True)
    logger.lines = []  # reset logger buffer
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_q = question[:50].replace(" ", "_").replace("/", "-").replace("?", "")
    logger.log_file = os.path.join(LOG_OUTPUT_DIR, f"{timestamp}__{safe_q}.txt")

    start_time = time.time()

    logger.log("BY", by)
    logger.log("Category", category)
    logger.log("QUESTION", question)
    logger.log("CSV-Complexity", original_complexity)
    logger.log("CONFIG Snapshot", str(CONFIG))


    timings = {}

    # === Step 1: Schema Retriever ===
    t0 = time.time()
    schema_result = retrieve_schema_with_llm(question)
    timings['SchemaRetriever'] = round(time.time() - t0, 2)

    logger.log("SR explanation", schema_result.get("explanation", ""))
    logger.log("Selected Schema", schema_result.get("selected_schema", ""))
    logger.log("SR Complexity", schema_result.get("complexity", ""))

    # carry SR complexity forward
    complexity = schema_result.get("complexity", original_complexity)

    if "Table" not in schema_result.get("selected_schema", ""):
        logger.log("Schema Error", "No valid table selected. Skipping question.")
        return

    # === Step 2: Candidate Generator ===
    t1 = time.time()


    cg_response = generate_sql_query(
        question,
        schema_result["selected"],
        complexity
    )
    timings['CandidateGenerator'] = round(time.time() - t1, 2)

    explanation = extract_explanation(cg_response)
    final_sql = extract_final_sql(cg_response)
    logger.log("CG explanation", explanation or "<None>")
    logger.log("CG SQL query", final_sql or "<None>")

    # === Step 3: Validator ===
    t2 = time.time()
    rows, columns, feedback = validate_sql(final_sql)
    timings['Validator'] = round(time.time() - t2, 2)

    #logger.log("Validator Result", str(rows[:10] if rows else "<no rows>"))
    logger.log("Validator result Columns", str(columns))
    logger.log("Validator Time", f"{timings['Validator']} s")

    # Recovery is bounded to one actual query error, never a successful empty/zero result.
    if feedback and "[DATABASE ERROR:query]" in feedback:
        t3 = time.time()
        corrected_sql, analyzer_expl = analyze_failure(
            question=question,
            selected_schema=schema_result["selected"],
            sql_query=final_sql,
            error_message=feedback or "No results returned"
        )
        timings['Analyzer'] = round(time.time() - t3, 2)

        # Run corrected SQL again if available
        if corrected_sql:
            t4 = time.time()
            rows2, columns2, feedback2 = validate_sql(corrected_sql)
            timings['Validator_AnalyzerFix'] = round(time.time() - t4, 2)
            logger.log("Validator (After Analyzer) Result", str(rows2[:10] if rows2 else "<no rows>"))
            logger.log("Validator (After Analyzer) Columns", str(columns2))

    total_time = round(time.time() - start_time, 2)
    timings['TotalTime'] = total_time
    logger.log("Timings", str(timings))

    # Save log to file
    logger.save()

    # Use corrected_sql if Analyzer was triggered
    sql_to_translate = corrected_sql if 'corrected_sql' in locals() and corrected_sql else final_sql
    translated_question = run_translator_agent(schema_result["selected"], sql_to_translate)

    match_score = run_llm_match_score_with_schema(
        question,
        translated_question,
        schema_result["selected"]
    )

    # Evaluation output structure
    eval_row = {
        "By": by,
        "Category": category,
        "Original Complexity": original_complexity,
        "Question": question,
        "Translated Question": translated_question,  # Optional: fill later via TR agent
        "Complexity (SR)": schema_result.get("complexity"),
        "SR-time": timings.get("SchemaRetriever"),
        "CG-time": timings.get("CandidateGenerator"),
        "Val.1-time": timings.get("Validator"),
        "Val.1 results": bool(rows),
        "AN triggered?": 'Analyzer' in timings,
        "AN-time": timings.get("Analyzer", ""),
        "Val2-time": timings.get("Validator_AnalyzerFix", ""),
        "Val2 results": bool('rows2' in locals() and rows2),
        "total-time": timings.get("TotalTime"),
        "Match-score": match_score,  # Optional: will be filled after TR-agent comparison
    }
    from src.llm_client import _ACTIVE
    eval_row["LLM requests"] = __import__("json").dumps(_ACTIVE.get().records)
    save_evaluation_result(eval_row)




if __name__ == "__main__":
    import argparse
    from src.llm_settings import load_llm_settings
    parser = argparse.ArgumentParser(description="Historical batch: sends questions to DeepSeek, reads SQL and writes reports")
    parser.add_argument("--live", action="store_true")
    if not parser.parse_args().live or not load_llm_settings().evaluation_enabled:
        parser.error("--live and LLM_ENABLE_EVALUATION=true are required; review historical input provenance")
    questions = read_questions(EVAL_INPUT_PATH)
    for by, category, complexity, question in questions:
        run_pipeline(by, category, complexity, question)

# if __name__ == "__main__":
#     questions = read_questions(EVAL_INPUT_PATH)
#
#     # Allow resume via CLI argument: python main1.py 18
#     start_index = 0
#     if len(sys.argv) > 1:
#         try:
#             start_index = int(sys.argv[1])
#             print(f"Resuming from question index: {start_index}")
#         except ValueError:
#             print("Invalid start index. Starting from 0.")
#
#     for i, (by, category, complexity, question) in enumerate(questions):
#         if i < start_index:
#             continue
#         print(f"\n=== Running question {i+1} ===")
#         run_pipeline(by, category, complexity, question)
