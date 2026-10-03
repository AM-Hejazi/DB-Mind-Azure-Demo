# Clean demo file set

The portfolio checkout was assembled independently from `Codex-Azure` at source
commit `c2fea2b`. The original repository and every original file are preserved.
The new repository contains no old Git history or Git LFS pointer-only assets.

## Excluded from the portfolio checkout

| Paths | Reason |
| --- | --- |
| `data/database/` | Historical database and schema export; runtime uses generated fictional data or existing Azure SQL |
| `data/schemas/`, `data/embeddings/` | Warehouse schemas and vector artifacts are outside the active snapshot/retrieval workflow; provenance/private-data risk |
| `Evaluation/`, `ev_logs/` | Historical research inputs, metrics, and execution logs are outside demo evidence |
| `.gradio/certificate.pem` | Retired certificate; hosted HTTPS belongs to Azure ingress |
| `src/db_handler.py`, `src/ui/handlers.py`, `src/ui/handlers_FD_bk.py` | Retired database/serving adapters; excluded from canonical runtime and tests |
| `main_UI_bk.py`, `src/llm_client_bk.py`, `src/ui/events_bk.py`, `src/ui/layout_bk.py` | Backups; fresh Git history records current code |
| `TEST.py`, `UI_debug.py`, `export_structure.py`, `src/Preprocess_wamas.py` | Historical debugging/export/warehouse preprocessing, outside current runtime |
| `rewrite_welcome.py`, `update_welcome.py`, `changes.patch` | One-off edits and patch residue |
| `requirements-research.txt` | Obsolete large research environment, outside hashed runtime |
| `DB_Mind_Codex_Migration_Prompt.md`, `docs/migration-plan.md`, `docs/migration-report.md`, `docs/cleanup-review.md` | Migration working instructions and superseded reports; current deployment/verification docs replace these |
| `.git`, private environment files, `var/`, caches, logs, virtual environments | Never copied; generated state or credentials do not belong in publication |

Ignore rules were replaced with concise demo rules; PNG assets are regular Git
files in the new checkout rather than requiring old LFS objects.

## Retained and why

Canonical `app.py`, its modules, all prompts, the exact original `data/icon.png`,
synthetic generator/schema/SQL/reference cases, tests, dependency locks, and two
actual desktop/mobile screenshots support runtime, reproduction, or checks.
The local fictional SQLite workflow remains the fastest offline quick start.
Useful configuration, discovery, safety, provider, and synthetic-data guides remain.

`main.py`, `main1.py`, `translator.py`, and `evaluate_pipeline.py` are retained
compatibility/operator entrypoints because tests exercise their import isolation,
explicit evaluation opt-in, and score parsing. Their historical input artifacts
are excluded; they are not deployment entrypoints and no research metrics are
claimed for the demo. `main_UI.py` remains a safe compatibility launcher.
`docs/dependency-audit.json` is retained as dated dependency evidence, not a claim
that a current live advisory scan has been run.

The public file audit checks excluded asset roots, high-confidence credential
patterns, and exact privately configured credential values in memory. It prints
only paths/categories/counts. An audit is not proof that every possible secret
format or provenance concern is detectable; no third-party license is invented.

Unused module-level smoke examples in `src/frontdesk.py`, `src/feedback.py`, and
`src/query_generator.py` were removed from the demo copy; two contained historical
warehouse SQL examples. Active prompts now name DB-Mind and use fictional
maintenance objects in place of legacy warehouse examples.
No original source was modified, and runtime function bodies remain supported.
