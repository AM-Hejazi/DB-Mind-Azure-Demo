# main.py
from src.logger import PipelineLogger
from src.retrieval import retrieve_schema_with_llm
from src.clarifier import clarify_question
from src.QFinalizer import finalize_question
from src.query_generator import generate_sql_query, extract_final_sql
from src.validator import validate_sql

import os

from src.llm_client import pipeline_operation

@pipeline_operation
def main():
    logger = PipelineLogger()

    # Initialize session state
    session_state = {
        "original_q": "",
        "qc_feedback": "",
        "user_rewrite": "",
        "final_q": "",
        "schema_result": None,
        "sql_query": "",
        "final_result": "",
        "is_clear": False
    }

    # Step 0: Input
    session_state["original_q"] = input("🧾 Your question: ")
    logger.log("Original Question", session_state["original_q"])

    # Step 1: Schema Retriever
    schema_result = retrieve_schema_with_llm(session_state["original_q"])
    session_state["schema_result"] = schema_result
    logger.log("Schema Reasoning Explanation", schema_result["explanation"])
    logger.log("Selected Schema Context", schema_result["selected_schema"])

    # Step 2: Clarifier
    qc_text, is_clear = clarify_question(session_state["original_q"], schema_result["selected_schema"])
    session_state["qc_feedback"] = qc_text
    session_state["is_clear"] = is_clear
    logger.log("Clarifier Output", qc_text)

    # Step 3: Skip QF if clear
    if session_state["is_clear"]:
        session_state["final_q"] = session_state["original_q"]
        session_state["complexity"] = "Simple"
        logger.log("User Rewrite", "(Skipped - question was clear)")
        logger.log("Final Reformulated Question", session_state["final_q"])
    else:
        # Prompt for user rewrite
        session_state["user_rewrite"] = input("✍️ Rewrite your question based on the feedback above:\n\n")
        logger.log("User Rewrite", session_state["user_rewrite"])

        # Finalize
        final_q, complexity = finalize_question(
            original_q=session_state["original_q"],
            feedback=session_state["qc_feedback"],
            user_rewrite=session_state["user_rewrite"]
        )
        session_state["final_q"] = final_q
        session_state["complexity"] = complexity
        logger.log("Final Reformulated Question", final_q)

        # Confirm use
        confirm = input(f"\n✅ Use this version?\n{final_q}\n(Y/N): ").strip().lower()
        if confirm != "y":
            logger.log("Confirmation", "User declined final question.")
            print("❌ Discarded.")
            logger.save()
            return

    # Step 4: Generate SQL
    print("⚙️ Generating SQL...")
    sql_response = generate_sql_query(
        session_state["final_q"],
        session_state["schema_result"]["selected"],
        session_state.get("complexity", "Difficult")
    )
    sql_query = extract_final_sql(sql_response)
    if not sql_query:
        raise RuntimeError("No validated query returned")
    session_state["sql_query"] = sql_query
    logger.log("Generated SQL Query", sql_query)

    # Step 5: Validate
    print("🔍 Validating SQL...")
    rows, columns, feedback = validate_sql(sql_query)

    if rows:
        result = "\n".join(str(r) for r in rows) if rows else "(no rows returned)"
        session_state["final_result"] = result
        logger.log("Validator Result Rows", result)
        print("✅ SQL Executed.\n")
        print(result)
    else:
        logger.log("Validation Status", "❌ Failure")
        logger.log("Validator Feedback", feedback if feedback else "(No feedback provided)")
        print("❌ SQL Execution Failed.")
        print(feedback if feedback else "(No feedback provided)")

    # Save and rename log
    logger.save()
    success_path = logger.log_file.replace(".txt", "__SUCCESS.txt")
    if not logger.log_file.endswith("__SUCCESS.txt") and rows:
        os.rename(logger.log_file, success_path)
        logger.log_file = success_path

    print(f"\n📄 Log saved to: {logger.log_file}")

if __name__ == "__main__":
    main()
