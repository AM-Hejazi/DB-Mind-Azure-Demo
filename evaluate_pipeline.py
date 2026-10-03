import os
import pandas as pd
from datetime import datetime
from src.logger import PipelineLogger
from src.retrieval import retrieve_schema_with_llm
from src.clarifier import clarify_question
from src.QFinalizer import finalize_question  # Will be skipped
from src.query_generator import generate_sql_query, extract_final_sql
from src.validator import validate_sql
import matplotlib.pyplot as plt

from src.llm_client import pipeline_operation
from src.llm_settings import load_llm_settings

@pipeline_operation
def run_evaluation():
    if not load_llm_settings().evaluation_enabled:
        raise RuntimeError("Explicit LLM_ENABLE_EVALUATION=true is required")
    # === CONFIG ===
    EVAL_PATH = "Evaluation/Evaluation_Questions.csv"
    RESULT_LOG_PATH = "Evaluation/eval_results.csv"
    PLOTS_FOLDER = "Evaluation"
    LOGS_FOLDER = "ev_logs"
    os.makedirs(PLOTS_FOLDER, exist_ok=True)
    os.makedirs(LOGS_FOLDER, exist_ok=True)

    # === LOAD QUESTIONS ===
    df = pd.read_csv(EVAL_PATH)
    df = df[df["Question"] != ""]
    df = df[df["Question"] != " "]

    # --- Resume from last processed question ---
    if os.path.exists(RESULT_LOG_PATH):
        df_done = pd.read_csv(RESULT_LOG_PATH)
        done_questions = set(df_done["Question"])
        df = df[~df["Question"].isin(done_questions)]

    # === EVALUATION LOOP ===
    df_results = []

    from src.llm_client import _ACTIVE
    for idx, row in df.iterrows():
        question = row["Question"]
        complexity = row["Complexity"]
        print(f"\n=== [{complexity}] {question} ===")

        logger = PipelineLogger()
        logger.log("Original Question", question)

        try:
            # --- SR ---
            schema_result = retrieve_schema_with_llm(question)
            logger.log("Schema Reasoning Explanation", schema_result["explanation"])
            logger.log("Selected Schema Context", schema_result["selected_schema"])

            # --- QC ---
            qc_text, is_clear = clarify_question(question, schema_result["selected_schema"])
            logger.log("Clarifier Output", qc_text)

            # --- QF ---
            if is_clear:
                final_q = question
                comp = complexity
                logger.log("User Rewrite", "(Skipped - question was clear)")
                logger.log("Final Reformulated Question", final_q)
                logger.log("Complexity", comp)
            else:
                print("\n⚠️ Follow-up clarification is required:")
                print(qc_text)
                user_rewrite = "Use the best matches from schema and your own reasoning"
                print(f"✍️ Auto-responding with: {user_rewrite}")
                final_q, comp = finalize_question(question, qc_text, user_rewrite)
                logger.log("User Rewrite", user_rewrite)
                logger.log("Final Reformulated Question", final_q)
                logger.log("Complexity", comp)

            # --- CG ---
            try:
                sql_response = generate_sql_query(final_q, schema_result["selected"], comp)
                sql_query = extract_final_sql(sql_response)
                if not sql_query:
                    raise RuntimeError("No validated query returned")
                logger.log("Generated SQL Query", sql_query)
                cg_model = next((r.get("returned_model", "UNKNOWN") for r in reversed(_ACTIVE.get().records) if r["stage"] == "CG"), "UNKNOWN")
            except Exception as e:
                logger.log("Generated SQL Query", f"❌ CG failed: {str(e)}")
                df_results.append([question, comp, "UNKNOWN", "CG FAILED", str(e)])
                logger.save()
                continue

            # --- Validator ---
            try:
                rows, columns, feedback = validate_sql(sql_query)
                result_preview = "\n".join([str(r) for r in rows]) if rows else "(no rows returned)"
                logger.log("Validator Result Rows", result_preview)
                status = "Success" if rows else "Empty"
                feedback = feedback or ""
            except Exception as e:
                status = "Validation Error"
                feedback = str(e)
                logger.log("Validator Result Rows", "❌ Exception")
                logger.log("Validator Feedback", feedback)

            logger.save()
            df_results.append([question, comp, cg_model, sql_query, feedback])

            # Rename log to __SUCCESS.txt
            if status == "Success":
                log_path = logger.log_file
                success_path = log_path.replace(".txt", "__SUCCESS.txt")
                if not log_path.endswith("__SUCCESS.txt"):
                    os.rename(log_path, success_path)
                    logger.log_file = success_path

        except Exception as e:
            df_results.append([question, complexity, "", "SR FAILED", str(e)])
            print(f"❌ Skipping due to error: {e}")
            logger.log("Pipeline Exception", str(e))
            logger.save()
            continue

    # === SAVE RESULTS CSV ===
    df_out = pd.DataFrame(df_results, columns=["Question", "Complexity", "CG Model", "SQL Query", "Execution Feedback"])
    import json
    evidence_path = os.path.join(PLOTS_FOLDER, "llm_request_evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as evidence:
        json.dump(_ACTIVE.get().records, evidence, indent=2)
    df_out.to_csv(RESULT_LOG_PATH, index=False)
    print(f"\n✅ Evaluation completed. Results saved to: {RESULT_LOG_PATH}")

    # === PLOTS ===
    df_stats = df_out.copy()
    df_stats["Status"] = df_stats["Execution Feedback"].apply(
        lambda x: "Success" if x.strip() == "" else (
            "Empty" if "no rows returned" in x else "Failed"
        )
    )

    # Bar chart: Success count per complexity
    plt.figure(figsize=(8, 5))
    df_stats.groupby(["Complexity", "Status"]).size().unstack().plot(kind="bar", stacked=True)
    plt.title("Query Execution Outcome by Complexity")
    plt.ylabel("Number of Questions")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(f"{PLOTS_FOLDER}/execution_by_complexity.png")
    print(f"📊 Plot saved to: {PLOTS_FOLDER}/execution_by_complexity.png")

    # Pie chart: Overall Success Rate
    plt.figure(figsize=(5, 5))
    df_stats["Status"].value_counts().plot(kind="pie", autopct="%1.1f%%", startangle=90)
    plt.title("Overall Execution Success Rate")
    plt.ylabel("")
    plt.tight_layout()
    plt.savefig(f"{PLOTS_FOLDER}/execution_success_pie.png")
    print(f"📊 Plot saved to: {PLOTS_FOLDER}/execution_success_pie.png")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Historical evaluation: model calls, SQL reads and output writes")
    parser.add_argument("--live", action="store_true")
    if not parser.parse_args().live:
        parser.error("--live is required; inspect historical input provenance before use")
    run_evaluation()
