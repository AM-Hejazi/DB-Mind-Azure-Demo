import ast
import json
import re
import csv
from src.llm_client import LLMClient, LLMError, pipeline_operation, _ACTIVE
from config import CONFIG
from src.retrieval import load_schema_text
from src.schema_context import stage_messages
import os
from datetime import datetime


EVAL_LOG_DIR = "Evaluation/ev_logs"
EVAL_RESULTS_DIR = "Evaluation/results"

EVALUATION_CSV_PATH = "Evaluation/Evaluation_Questions.csv"
REPORT_TXT_PATH = os.path.join(EVAL_RESULTS_DIR, "Evaluation report.txt")
eval_map = {}

CSV_METRICS_PATH = os.path.join(EVAL_RESULTS_DIR, "evaluation_metrics.csv")
JSONL_OUTPUT_PATH = os.path.join(EVAL_RESULTS_DIR, "evaluation_results.jsonl")

PROMPT_TR_PATH = "prompts/TR.txt"
PROMPT_EV_PATH = "prompts/EV.txt"

FULL_SCHEMA_DIGEST_PATH = CONFIG.get("DATABASE", {}).get("SCHEMA_JSON", "")
FULL_SCHEMA_DIGEST = None

# === STEP 1: Convert Raw Logs to Structured JSON ===
def parse_log_file(filepath):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    block = lambda key: re.search(rf"=== {re.escape(key)} ===\n(.*?)(?=\n=== |\Z)", content, re.DOTALL)

    def extract(key):
        m = block(key)
        return m.group(1).strip() if m else None

    def extract_all(key):
        return re.findall(rf"=== {re.escape(key)} ===\n(.*?)(?=\n=== |\Z)", content, re.DOTALL)

    def extract_block(header):
        """
        Return every line between '=== {header} ===' and the next '==='.
        """
        m = re.search(rf"=== {re.escape(header)} ===\n(.*?)(?=\n=== |\Z)", content, re.DOTALL)
        return m.group(1).strip() if m else ""

    def extract_first(key):
        matches = extract_all(key)
        return matches[0].strip() if matches else None


    def extract_second(key):
        matches = extract_all(key)
        return matches[1].strip() if len(matches) > 1 else None

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------
    def parse_rows(text: str):
        """
        Turn the raw 'Validator Result Rows' block into a Python list.
        Handles 3 formats:
          – JSON list   → [ {...}, ... ]
          – Single tuple → "(123,)"      (old ev_logs)
          – Multi-line tuples, one per line
                ('DE', 'DHL STANDARD', 70)
                ('FI', 'DPD', 1)
        """
        if not text:
            return []

        # 1) Proper JSON list *********************************************
        if text.lstrip().startswith("["):
            try:
                return json.loads(text)
            except Exception:
                pass  # fall through

        # 2) Single tuple *************************************************
        if text.lstrip().startswith("(") and text.strip().endswith(")"):
            try:
                return [ast.literal_eval(text)]
            except Exception:
                pass  # fall through

        # 3) Multi-line tuples ********************************************
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("(") and line.endswith(")"):
                try:
                    rows.append(ast.literal_eval(line))
                except Exception:
                    continue
        return rows

    def parse_columns(text: str):
        if not text:
            return []
        try:
            # same JSON-then-literal strategy
            if text.startswith("["):
                try:
                    return json.loads(text)
                except Exception:
                    return ast.literal_eval(text)
            return ast.literal_eval(text)
        except Exception:
            return []

    def merge_cols_rows(cols, rows):
        """
        Merge columns+rows into a list of dicts, so one structure holds both.
        Fallbacks gracefully when cols are missing or lengths mismatch.
        """
        if not rows:
            return []
        if cols and len(cols) == len(rows[0]):
            return [dict(zip(cols, r)) for r in rows]
        return rows  # lose the column names but keep the rows

    # ---------------------------------------------------------------------------
    # Timings block --------------------------------------------------------------
    try:
        timings_block = ast.literal_eval(extract("Timings") or "{}")
    except Exception:
        timings_block = {}

    # ---------------------------------------------------------------------------
    # Schema-Retriever pieces ----------------------------------------------------
    sr_expl = extract("SR explanation") or ""
    sr_selected = extract("Selected Schema") or ""

    try:
        # old ev_logs sometimes had the whole dict under "Schema Retriever Output"
        sr_output = extract("Schema Retriever Output") or "{}"
        sr_data = ast.literal_eval(sr_output) if sr_output.startswith("{") else {}
        sr_selected = sr_selected or sr_data.get("selected_schema", "")
        sr_complexity = sr_data.get("complexity", None)
        sr_selected_d = sr_data.get("selected", {})
    except Exception:
        sr_complexity = None
        sr_selected_d = {}
    # Fallback to the separate log line when the JSON blob has no complexity
    if not sr_complexity:
        sr_complexity = extract("SR Complexity")

    # ---------------------------------------------------------------------------
    # Candidate-Generator pieces -------------------------------------------------
    cg_expl = extract("CG explanation") or ""
    cg_sql = extract("CG SQL query") or ""

    # ---------------------------------------------------------------------------
    # Validator-1 ----------------------------------------------------------------
    val1_rows_text = extract_block("Validator Result") or extract_first("Validator Result Rows")
    val1_cols_text = extract_block("Validator Columns") or extract_first("Validator result Columns")

    val1_rows = parse_rows(val1_rows_text)
    val1_cols = parse_columns(val1_cols_text)
    val1_combined = merge_cols_rows(val1_cols, val1_rows)

    # ---------------------------------------------------------------------------
    # Analyzer ------------------------------------------------------------------
    an_raw = extract("Analyzer Raw Output") or ""

    # ---------------------------------------------------------------------------
    # Validator-2 (after Analyzer) ----------------------------------------------
    val2_rows_text = extract_block("Validator (After Analyzer) Result")
    val2_cols_text = extract_block("Validator (After Analyzer) Columns")

    val2_rows = parse_rows(val2_rows_text)
    val2_cols = parse_columns(val2_cols_text)
    val2_combined = merge_cols_rows(val2_cols, val2_rows)

    # ---------------------------------------------------------------------------
    # Assemble JSON object -------------------------------------------------------
    json_obj = {
        "question": extract("QUESTION"),
        "complexity": extract("CSV-Complexity")
                      or extract("SR Complexity")
                      or extract("Complexity"),
        "sr": {
            "model": extract("SR Model"),
            "response_time": extract("SR Response Time"),
            "explanation": sr_expl,
            "selected_schema": sr_selected,
            "selected": sr_selected_d,
            "complexity": sr_complexity
        },
        "cg": {
            "model": extract("CG Model"),
            "response_time": extract("CG Time"),
            "explanation": cg_expl,
            "sql": cg_sql
        },
        "validator_1": {
            "time": timings_block.get("Validator"),
            "status": extract_first("Validation Status"),
            "result": val1_combined,
            "error": extract_first("Validator Feedback") or "",
            "timeout": "Timeout" in (extract_first("Validator Feedback") or "")
        },
        "analyzer": {
            "explanation": extract("Analyzer Explanation"),
            "sql": extract("Analyzer SQL"),
            "raw_output": an_raw,
            "response_time": timings_block.get("Analyzer")
        },
        "validator_2": {
            "time": timings_block.get("Validator_AnalyzerFix"),
            "status": extract_second("Validation Status"),
            "result": val2_combined,
            "error": extract_second("Validator Feedback") or "",
            "timeout": "Timeout" in (extract_second("Validator Feedback") or "")
        },
        "total_time": timings_block.get("TotalTime") or extract("TotalTime"),
        "log_filename": os.path.basename(filepath)
    }

    return json_obj


# === STEP 2: Translator Agent ===
def run_translator_agent(selected_schema: dict, sql_query: str) -> str:
    with open(PROMPT_TR_PATH, encoding='utf-8') as f:
        prompt_template = f.read()

    # Step 1: Build rich schema block
    schema_struct = build_schema_struct_from_json(
        selected_schema.get("tables", []),
        selected_schema.get("columns", [])
    )
    rich_schema_block = format_schema_block(schema_struct)

    # Step 2: Fill in prompt
    prompt = (
        prompt_template
        .replace("{SQL_QUERY}", sql_query.strip())
        .replace("{SELECTED_SCHEMA}", rich_schema_block.strip())
    )
    #print(prompt)
    # Step 3: Call the LLM
    client = LLMClient(stage="TR")
    response = client.chat(
        messages=stage_messages(prompt_template, {'SQL_QUERY': sql_query, 'SELECTED_SCHEMA': rich_schema_block},
                                'Convert SQL to a natural-language question.'), temperature=0.2,
    )
    return response.choices[0].message.content.strip()




def parse_evaluation_scores(content):
    scores = {}
    for name in ('Question', 'Schema', 'Match', 'Query'):
        matches = re.findall(rf"^## {name} Score: ([0-9]+(?:\.[0-9]+)?)\s*$", content, re.M)
        if len(matches) != 1 or not 0 <= float(matches[0]) <= 1:
            raise LLMError("malformed", "Evaluator scores are missing, duplicated or out of range")
        scores[name.lower()] = float(matches[0])
    return scores


# === STEP 3: Evaluator Agent ===
def run_evaluator_agent(
    question,
    regenerated_question,
    sr_expl,
    sr_sel,
    cg_expl,
    cg_sql,
    an_raw,
    validator_1_result,
    validator_2_result,
    full_schema_digest          # <── NEW
):

    with open(PROMPT_EV_PATH, encoding="utf-8") as f:
        template = f.read()

    client = LLMClient(stage="EV")
    response = client.chat(messages=stage_messages(template, {
        'ORIGINAL_QUESTION': question, 'REGENERATED_QUESTION': regenerated_question,
        'SR_EXPLANATION': sr_expl, 'SR_SELECTED': sr_sel, 'CG_EXPLANATION': cg_expl,
        'CG_SQL': cg_sql, 'Results_1': validator_1_result, 'AN_RAW_OUTPUT': an_raw,
        'Results_2': validator_2_result, 'FULL_SCHEMA_DIGEST': full_schema_digest,
    }, 'Evaluate accuracy against the synthetic schema. Return a concise assessment; no internal deliberation.'))
    content = response.choices[0].message.content.strip()
    parse_evaluation_scores(content)
    return content


# === STEP 4: Run Evaluation ===
@pipeline_operation
def run_full_evaluation():
    from src.llm_settings import load_llm_settings
    if not load_llm_settings().evaluation_enabled:
        raise RuntimeError("Explicit LLM_ENABLE_EVALUATION=true is required")
    os.makedirs(EVAL_RESULTS_DIR, exist_ok=True)
    # Preserve optional historical CSV annotations, but load only in explicit evaluation.
    if os.path.exists(EVALUATION_CSV_PATH):
        with open(EVALUATION_CSV_PATH, newline='', encoding='ISO-8859-1') as source:
            for row in csv.DictReader(source):
                eval_map[row['Question'].strip().lower()] = {key: row.get(key) for key in ('By','Category','Complexity')}
    schema_digest = load_schema_text()

    jsonl_out = []
    csv_rows = []
    report_lines = []
    question_counter = 0

    TARGET = "20250709_095629__Which_product_group_is_shipped_the_most_to_each_co__SUCCESS.txt"

    for filename in os.listdir(EVAL_LOG_DIR):

        # skip non-txt files immediately
        if not filename.endswith(".txt"):
            continue

        # skip every file except the target
        if filename != TARGET:
            continue

        log_path = os.path.join(EVAL_LOG_DIR, filename)
        print(f"\n🔍 Processing log file: {filename}")
        data = parse_log_file(log_path)

        try:
            print(f"\n🧪 Running Translator Agent for: {data['log_filename']}")
            question_counter += 1
            tr_output = run_translator_agent(data['sr']['selected'], data['cg']['sql'])
            print(f"🧪 Running Evaluator Agent...")
            ev_output = run_evaluator_agent(
                data["question"],
                tr_output,
                data["sr"]["explanation"],
                data["sr"]["selected_schema"],
                data["cg"]["explanation"],
                data["cg"]["sql"],
                data["analyzer"]["raw_output"],
                data["validator_1"]["result"],
                data["validator_2"]["result"],
                schema_digest
            )

            print(f"✅ Evaluation completed for: {filename}")
            question_clean = data['question'].strip().lower()
            metadata = eval_map.get(question_clean, {})

            result = {
                "llm_requests": list(_ACTIVE.get().records),
                "log_file": data['log_filename'],
                "question": data['question'],
                "complexity": data['complexity'],
                "original_complexity": metadata.get("Complexity"),
                "category": metadata.get("Category"),
                "user": metadata.get("By"),
                "translator_output": tr_output,
                "evaluator_output": ev_output
            }

            jsonl_out.append(result)

            question_score = float(re.search(r"Question Score: ([0-9.]+)", ev_output).group(1)) if "Question Score" in ev_output else 0.0
            match_score = float(re.search(r"Match Score: ([0-9.]+)", ev_output).group(1)) if "Match Score" in ev_output else 0.0
            schema_score = float(re.search(r"Schema Score: ([0-9.]+)", ev_output).group(1)) if "Schema Score" in ev_output else 0.0
            query_score = float(re.search(r"Query Score: ([0-9.]+)", ev_output).group(1)) if "Query Score" in ev_output else 0.0
            summary_match = re.search(
                r"## Evaluation Summary\s*(.*?)\s*## Question Score:",
                ev_output,
                re.DOTALL
            )
            evaluation_summary = summary_match.group(1).strip() if summary_match else ""

            csv_rows.append([
                question_counter,  # Nr.
                metadata.get("By"),  # By
                metadata.get("Complexity"),  # Original Complexity
                metadata.get("Category"),  # Category
                data["question"],  # Question
                tr_output,  # Translated Question
                data["sr"]["complexity"] or "",  # Complexity (est. by SR)
                data["sr"]["response_time"],  # SR-time
                data["cg"]["response_time"],  # CG-time
                data["validator_1"]["time"],  # Val.1-time
                bool(data["analyzer"]["sql"]),  # AN triggered?
                data["analyzer"]["response_time"],  # AN-time
                data["validator_2"]["time"],  # Val2-time
                data["total_time"],  # total-time
                question_score,  # Question-score
                schema_score,  # Schema-score
                match_score,  # Match-score
                query_score,  # Query-score
                evaluation_summary  # Evaluation Summary (text)
            ])
            # ---- build text report block --------------------------------------
            who = metadata.get("By", "Unknown")
            cat = metadata.get("Category", "N/A")
            origC = metadata.get("Complexity", "N/A")
            sr_cmp = data["sr"]["complexity"] or ""
            val1_str = json.dumps(data["validator_1"]["result"], ensure_ascii=False)
            val2_str = json.dumps(data["validator_2"]["result"], ensure_ascii=False)

            report_lines.extend([
                f"* Question {question_counter}",
                f"  By: {who}",
                f"  Question: {data['question']}",
                f"  Original Complexity: {origC}",
                f"  Category: {cat}",
                "",
                "  * WAMind results",
                f"    Estimated complexity by SR: {sr_cmp}",
                f"    Final SQL query (from {'Analyzer' if data['analyzer']['sql'] else 'CG'}):",
                f"    {data['cg']['sql'] if not data['analyzer']['sql'] else data['analyzer']['sql']}",
                "    Final Validation results:",
                f"    " + (val2_str if val2_str not in ('[]', 'null', 'None') else val1_str),
                f"    Total time: {data['total_time']}",
                "",
                "  * Evaluation results",
                f"    Translated question: {tr_output}",
                "    Evaluation summary:",
                f"    {evaluation_summary}",
                "    Scores:",
                f"      Question: {question_score}   Schema: {schema_score}   Match: {match_score}   Query: {query_score}",
                ""
            ])

            print(f"""
            📄 Log: {filename}
            ├─ SR Time: {data['sr']['response_time']}
            ├─ CG Time: {data['cg']['response_time']}
            ├─ Validator Time: {data['validator_1']['time']}
            ├─ Total Time: {data['total_time']}
            ├─ Result 1: {"error" if data['validator_1']['error'] else ("empty" if not data['validator_1']['result'] else "available")}
            ├─ Analyzer Triggered: {bool(data['analyzer']['sql'])}
            ├─ Schema Score: {schema_score}, Match Score: {match_score}, Query Score: {query_score}, Question Score: {question_score}
            """)

        except Exception as e:
            print(f"❌ Evaluation failed for {filename}: {e}")
            continue
        break

    with open(JSONL_OUTPUT_PATH, "w", encoding='utf-8') as f:
        for obj in jsonl_out:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    with open(CSV_METRICS_PATH, "w", encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            "Nr.", "By", "Original Complexity", "Category", "Question",
            "Translated Question", "Complexity (SR)", "SR-time", "CG-time",
            "Val.1-time", "AN triggered?", "AN-time", "Val2-time",
            "total-time", "Question-score", "Schema-score", "Match-score",
            "Query-score", "Evaluation Summary"
        ])
        writer.writerows(csv_rows)

    with open(REPORT_TXT_PATH, "w", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%A, %d %B %Y  %H:%M")
        f.write(f"Evaluation Report ({timestamp})\n\n")
        f.write("\n".join(report_lines))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Historical evaluation: sends reviewed logs to DeepSeek and writes reports")
    parser.add_argument("--live", action="store_true")
    if not parser.parse_args().live:
        parser.error("--live is required; review historical input provenance before use")
    run_full_evaluation()
# if __name__ == "__main__":
#     # === Test Block ===
#     test_logs = [f for f in os.listdir(EVAL_LOG_DIR) if f.endswith(".txt")]
#     if not test_logs:
#         print("No log files found in Evaluation/ev_logs/")
#     else:
#         test_file = os.path.join(EVAL_LOG_DIR, test_logs[0])
#         print(f"\n🔍 Running test on: {test_file}")
#         result = parse_log_file(test_file)
#         print(json.dumps(result, indent=2, ensure_ascii=False))
#
#         # === Translator Agent Test ===
#         try:
#             print("\n🧪 Calling Translator Agent...")
#             regenerated_question = run_translator_agent(
#                 selected_schema=result["sr"]["selected"],
#                 sql_query=result["cg"]["sql"]
#             )
#             print("\n🔁 Regenerated Question:\n", regenerated_question)
#         except Exception as e:
#             print(f"\n❌ Translator Agent failed: {e}")
#
#         # === Evaluator Agent Test ===
#         try:
#             print("\n🧪 Calling Evaluator Agent...")
#
#             schema_digest = load_schema_text()
#
#             ev_output = run_evaluator_agent(
#                 full_schema_digest=schema_digest,
#                 question=result["question"],
#                 regenerated_question=regenerated_question,
#                 sr_expl=result["sr"]["explanation"],
#                 cg_expl=result["cg"]["explanation"],
#                 an_expl=result["analyzer"]["explanation"],
#                 sql=result["cg"]["sql"],
#                 validator_1_result=result["validator_1"]["result"],
#                 validator_2_result=result["validator_2"]["result"]
#             )
#
#             print("\n📊 Evaluator Output:\n", ev_output)
#
#         except Exception as e:
#             print(f"\n❌ Evaluator Agent failed: {e}")
