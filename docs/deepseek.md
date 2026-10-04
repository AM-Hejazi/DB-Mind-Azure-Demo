# Model configuration

The implemented provider is DeepSeek Chat Completions. Mock mode uses local
reference responses; live mode requires a private `DEEPSEEK_API_KEY`. Install the
locked dependencies from the README. Configuration comes from process environment,
not a browser selector or an automatically loaded `.env`.

## Settings

The model identifiers below are the values accepted by this release's settings.
Verify availability for your provider account before deployment; supported names
in code are not a guarantee of future provider availability.

| Setting | Default / policy |
| --- | --- |
| `LLM_PROVIDER` | `deepseek`; all other values rejected |
| `DEEPSEEK_API_KEY` | No default; required only for live calls; excluded from settings repr/config/evidence |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`; only this or its `/v1` form allowed |
| `DEEPSEEK_MODEL` | `deepseek-flash`; the two identifiers accepted by this release |
| `DEEPSEEK_{STAGE}_MODEL` | Optional override for CG/SR/AN/FA/QC/QF/TR/EV; inherits default |
| `DEEPSEEK_THINKING` | `disabled` or explicitly `enabled` |
| `DEEPSEEK_REASONING_EFFORT` | `low`; `low/high/max` accepted, sent only when thinking is enabled |
| `LLM_TIMEOUT_SECONDS` | 30; 1–120 per attempt |
| `LLM_RETRIES` | 1; 0–2 additional attempts |
| `LLM_MAX_OUTPUT_TOKENS` | 2,048; 32–8,192 sent as `max_tokens` |
| `LLM_MAX_INPUT_CHARS` | 48,000; 2,000–100,000 across text messages |
| `LLM_MAX_OUTPUT_CHARS` | 16,000; 256–32,000 final content characters |
| `LLM_PIPELINE_MAX_CALLS` | 12; 1–30 attempts, including retries/model listing |
| `LLM_PIPELINE_TIMEOUT_SECONDS` | 300; 5–900 seconds across a pipeline operation |
| `LLM_ENABLE_EVALUATION` | `false`; TR/EV require explicit `true` |


Restart the app when changing process settings. For Azure, review the deployment
template's environment entries: the included deployer does not forward every
setting from its private env file. Keep credentials in secret references.

## Check configuration and provider access

```bash
# Local settings validation; no model call:
python -m src.llm_check
# Explicit provider access check and one bounded paid completion:
python -m src.llm_check --live
```

The live check lists account models and requests a tiny fixed response. It does
not query SQL or send dataset samples. Without a key it makes no provider call.
For normal live conversations, questions, selected schema, enabled sample values
and limited result/feedback context can be transmitted. Use approved data and
review your provider policy. See [discovery](discovery.md) and [safety](safety.md).

## Response validation and budgets

Candidate Generator and Analyzer return validated JSON with language, explanation
and SQL. Feedback returns a decision/reply/question and routes revised questions
to Candidate Generator. Malformed, duplicate/extra fields, incomplete responses
and unknown objects fail safely; valid JSON does not authorize query execution.
All generated SQL passes the same read-only gate. Revised queries require `/execute`.
Internal reasoning is discarded rather than displayed or accumulated in logs.
Provider response streaming is disabled; UI progress reports bounded stage status.

SDK retries are disabled; the app implements bounded retries for selected transient
HTTP/connection failures. Auth, balance, unsupported-model/invalid-parameter errors,
malformed responses and cancelled/expired operations do not retry or switch providers.
Every attempt, including retries, consumes budgets before network execution.
Per-attempt deadlines and the shared per-question allowance bound caller time;
remote cancellation is best effort and may not prevent provider billing.

Configure provider billing caps separately from app quotas. Missing usage or lost
responses mean unknown charges, not zero cost. Optional cost estimates require
operator-supplied dated rates; no current prices are hard-coded. Stage models may
have different prices. Review provider pricing directly before setting rates.

Evaluation is disabled by default. Optional evaluation entrypoints require explicit
live/evaluation flags and reviewed data; they are not deployment smoke tests or
proof of general SQL accuracy.
