# DeepSeek integration — Milestone 5

The supported transport is explicit **DeepSeek Chat Completions**. Default requested model: `deepseek-flash`; optional operator override: `deepseek-v4-pro`. All eight stages (CG, SR, AN, FA/Front Desk, QC, QF, TR, EV) use validated immutable settings. Missing legacy QC/QF settings are resolved. Old aliases, prefix-based provider inference, Responses/o3-pro evaluator calls, and silent fallback are removed from these active paths. Other providers are disabled.

## Current documentation and evidence

Checked **2 October 2026**: DeepSeek's [model listing reference](https://api-docs.deepseek.com/api/list-models/) lists Flash and Pro. Its [updates](https://api-docs.deepseek.com/updates/) identifies the September Flash release. These establish the requested identifiers; they do not establish this account's access or a permanent underlying version. Requested and returned model IDs are retained separately in request evidence.

The [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) documents `max_tokens`, thinking controls, text/JSON response formats, streaming, and returned usage. The [thinking guide](https://api-docs.deepseek.com/guides/thinking_mode/) documents enabled-by-default thinking, effort controls, and the separate reasoning field. [JSON output guidance](https://api-docs.deepseek.com/guides/json_mode/) requires an explicit JSON instruction and warns about empty/truncated output. This implementation disables thinking explicitly by default, omits temperature when thinking is enabled, validates JSON locally, and rejects incomplete responses.

The `openai==1.93.0` compatibility SDK pin is retained in the final tested release lock. Static inspection of its [Chat Completions source](https://github.com/openai/openai-python/blob/v1.93.0/src/openai/resources/chat/completions/completions.py) confirms `max_tokens`, `response_format`, `extra_body`, and request timeout support; its [base client source](https://github.com/openai/openai-python/blob/v1.93.0/src/openai/_base_client.py) provides retry/timeout controls. Thinking/effort use `extra_body`, accommodating the older SDK effort type declarations. A lazy SDK import occurs only on an explicit call; construction/import of stage clients does not open connections.

**Milestone 5 evidence (2 October 2026): 49 offline tests passed** (29 earlier + 20 transport tests). Request construction and SDK behavior were mocked; the actual SDK is absent and has not been installed or integration-tested here. The DeepSeek key is absent. The default configuration check passed; account model listing and live completion are **not run**. No Azure/SQL Server, Gradio launch, package installation, or deployment was performed. Documentation compatibility is not an end-to-end SDK result.

## Operator settings

Settings come only from process environment; `.env` is not loaded implicitly. See `.env.example` and `src/llm_settings.py`.

| Setting | Default / policy |
| --- | --- |
| `LLM_PROVIDER` | `deepseek`; all other values rejected |
| `DEEPSEEK_API_KEY` | No default; required only for live calls; excluded from settings repr/config/evidence |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com`; only this or its `/v1` form allowed |
| `DEEPSEEK_MODEL` | `deepseek-flash`; the two verified identifiers only |
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

Use process startup/restart to change server settings. The active browser selector is a read-only provider label and has no configuration callback. Active handlers ignore the legacy session provider value; immutable compatibility maps reject mutations. A visitor cannot provide credentials/endpoints/model overrides through the supported UI. Preserved backup entrypoints are not the supported public serving path.

## Response contracts and limits

Query Generator and Analyzer request JSON containing exactly nonempty string fields `language`, `explanation`, and `sql`. The language must be a two-letter code; summary length, SQL length, NUL, markdown fences, and injected compatibility markers are checked. Summaries are concise user-facing rationales, not internal deliberation. Duplicate/extra fields, incorrect types, nonfinite JSON constants, empty content, tool calls, refusals, multiple choices, and non-`stop` finish reasons fail without retry. Valid JSON is not proof that SQL is safe or correct: Milestone 6 supplies the common dialect-aware execution gate.

The existing tagged output API remains via an adapter; summaries are escaped and final-query extraction rejects duplicate answer blocks. Front Desk feedback execution blocks must be unique/nonempty/bounded before passing to the database adapter. This preserves the feedback flow, but the full confirmation/execution policy remains Milestone 6. Schema Retriever selections still resolve against the validated canonical snapshot. Evaluator score parsing rejects missing/duplicate/out-of-range values rather than fabricating a zero score.

Returned SDK responses are reconstructed with final `content`, usage, and model IDs only; `reasoning_content` is discarded before stage logging/UI. API reasoning is never accumulated or returned. Inputs remain bounded, role-separated untrusted data from Milestone 4. Streaming is **explicitly disabled/rejected**: partial JSON/SQL is not surfaced or executed. Provider streaming support and its final-chunk usage behavior were checked in the reference; no streaming SDK integration is claimed.

Character caps complement schema/sample limits; they are not tokenizer-based accounting. The SDK/provider can allocate a response before local content validation, including hidden reasoning. Provider output-token limits and the response validator are not a general hostile-endpoint memory guarantee.

## Retries, cancellation, and pipeline budgets

SDK retries are set to **zero** on every client, so only the application's bounded retry loop operates. HTTP 429 and 500/502/503/504, plus SDK connection/timeout errors, may retry with capped exponential backoff and jitter. Authentication/authorization, balance, unsupported-model/invalid-parameter responses, malformed output, and application cancellation/deadlines do not retry. Error messages omit exception bodies, credentials, prompts, SQL, and response dumps. Requests never switch model/provider on failure.

Every attempt consumes the shared call allowance before network execution; retries cannot replenish it. Main CLI operations and the active UI generator use a shared operation/session budget across stages. UI generator context is installed only while advancing the generator, so interleaved callbacks cannot inherit another session's context. Reset/new-question cancels the old budget. The deadline includes elapsed user clarification time; after expiry, start a new question. Historical batch commands have explicit bounded operation budgets; a long batch may exhaust them rather than continue spending.

A daemon request worker bounds caller wall time in addition to SDK socket timeouts. The caller checks cancellation/deadline every 50 ms, closes the SDK client on cancellation/deadline, discards late results, and performs no blocking executor shutdown/join. Closing is **best-effort remote cancellation**: a provider may continue processing/billing, and a stuck underlying worker may briefly outlive the caller. Client-close behavior and actual network cancellation need integration verification. Timed-out application requests cancel their budget and cannot launch another attempt on it.

Connection failures/retries may have unknown provider charges, especially when a response is lost. Missing usage remains unknown, never zero-cost evidence. These per-operation limits do not replace authenticated server-side quotas, concurrency limits, abuse controls, or session-scoped diagnostics; those are Milestone 6 work. The original global logger and later UI callback/factory issues are not resolved by this milestone. Do not publish the current UI yet.

## Usage and optional cost estimates

Evidence records stage, requested/returned IDs, returned prompt/completion/total/cache token counts, or a safe failure code with unknown usage. `main1.py` adds request evidence to evaluation CSV records; translator evaluation JSONL and `evaluate_pipeline.py`'s separate JSON evidence retain it too. Tests cover usage consistency. No current prices are hard-coded.

For operator-reviewed estimates, set **all** of `LLM_INPUT_CACHE_HIT_USD_PER_M`, `LLM_INPUT_CACHE_MISS_USD_PER_M`, `LLM_OUTPUT_USD_PER_M`, and ISO `LLM_RATES_DATE`. Values must be finite/nonnegative. Estimates use returned cache hit/miss and completion counts only when those counts are complete and consistent. They apply only to `DEEPSEEK_MODEL`, not differently priced stage overrides. They are labelled `estimated_usd` with rate date/model; absent rates or usage yield no estimate. Review [official pricing](https://api-docs.deepseek.com/quick_start/pricing/) and applicable billing time/tier before supplying rates. Estimates do not account for unknown failed-request charges or guarantee invoice totals.

## Commands and network effects

```bash
# Local configuration only; no SDK/key/network needed:
python -B -m src.llm_check
python -B -m unittest discover -s tests -v

# Explicit opt-in: HTTPS account model listing + at most ONE chargeable completion:
python -B -m src.llm_check --live
```

Without a key, `--live` reports **not run** and makes no SDK/network call. With a key, it checks all configured stage model IDs against that account's first model-list page, then requests a tiny fixed response. This smoke command disables retries/thinking and caps output at 64 tokens regardless of the pipeline defaults; model listing also counts toward its call/time budget. It prints usage/model evidence rather than internal reasoning or credentials. A missing model stops before the completion. The command does not query SQL or send dataset samples.

Historical `main1.py`, `translator.py`, and `evaluate_pipeline.py` require both `--live` and `LLM_ENABLE_EVALUATION=true` in their command paths. They send reviewed questions/logs/schema/results to the external provider and write local reports; the first two no longer create output directories/read schema at import, and the automatic evaluation loop is main-guarded. Their original research inputs/score methodology are preserved, not certified synthetic or validated benchmarking. Inspect provenance and paths before running them; they were not run here. Ordinary stage calls are explicit live operations even without the smoke command's `--live` flag.

Bounded metadata/sample values leave Azure when the future live pipeline calls DeepSeek. The public profile remains synthetic-only. There is no live-model accuracy, latency, cost, deployment, or SDK-runtime claim from offline mocks.

Next: **Milestone 6 — the common fail-closed SQL gate, bounded recovery, session isolation, authenticated spending controls, and route-level safety tests.**


## Final SDK evidence — 3 October 2026

The compatibility SDK is now installed in the clean hashed runtime. A real `OpenAI` client serialized the DeepSeek thinking extension and parsed a completion through `httpx.MockTransport` with no network. All 77 final offline checks passed on the host; this is SDK compatibility evidence, not a live DeepSeek integration or model-quality result. Default model/account access and billed usage remain unverified. The current UI exposes fixed provider/model labels and explicit mock/live mode; mock fixture responses perform no model calls.
