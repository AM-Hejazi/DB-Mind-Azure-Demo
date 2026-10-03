# Follow-up query refinement verification

Verified 3 October 2026. The existing Azure demo serves revision
`dbmind-demo--0000009`, using runtime source
`f4e4c9b2971d677ad1fe81e77d28586f4926d1c6` and the immutable image in the
[sanitized receipt](deployment-followup-receipt.json).
[PR #2](https://github.com/AM-Hejazi/DB-Mind-Azure-Demo/pull/2) was merged after
[CI passed](https://github.com/AM-Hejazi/DB-Mind-Azure-Demo/actions/runs/37150277211).
Work was isolated in a separate worktree/branch; the original DB-Mind repository
remains unchanged. No history was rewritten.

## Problem and corrected behavior

After an initial 12-month result, the old Feedback prompt required an explanation
checkpoint before addressing requested changes. It also incorrectly said that
query proposal tags caused immediate execution. Requests such as “how about last
6 months” could therefore receive another explanation of the 12-month query.

Feedback now prioritizes the latest request, preserves recent history, and returns
a structured decision and revised natural-language question. It passes that
question, selected schema, prior question/SQL and latest request to Candidate
Generator (CG). CG generates the replacement SQL; the Validator boundary checks
it before showing a proposal. The old result remains visible until the user
reviews and confirms with `/execute`. Confirmation revalidates the read and
applies the restricted runtime permission guard. Only a successful read updates
the active question, executed SQL and results, so subsequent turns refer to the
new time window. Superseded/invalid proposals cannot remain armed.

This preserves the thesis's core FrontDesk → SR → CG → Validator → Feedback
loop. For a failed approved revision, one Analyzer recovery can produce a new
reviewed proposal, requiring fresh confirmation. It cannot execute silently or
repeat indefinitely. Empty successful results and connectivity failures do not
invoke Analyzer. Model/query budgets, authentication, file protections, session
expiry and database permission guards are preserved. These engineering checks
do not establish thesis accuracy scores or complete semantic correctness.

## Evidence

- **110 offline tests passed**, including real adapter execution on a disposable
  SQLite fixture, Feedback-to-CG routing/context, latest-request retention,
  malformed responses, writes, unknown tables, stale proposals, revalidation,
  and bounded Analyzer recovery.
- Local Chromium through the actual Gradio queue showed three rows for 12 months,
  a six-month proposal with no execution, and four rows after confirmation. Only
  this disposable local fixture received one older failure to make the two result
  sets distinguishable; Azure data was unchanged.
- One completed hosted DeepSeek conversation verified the six-month proposal,
  separate `/execute`, updated SQL, expected assets SYN-EQ-057 through SYN-EQ-060,
  and a later explanation using six months. An earlier clarification-only attempt
  did not reach query results; the completed case supplied the explicit reference
  date 1 October 2026. No broader benchmark was run.
- The live SQL changed to a six-month window, even though both live queries return
  the same four assets: the standard fixture contains no older failure events
  that distinguish those periods. Initial/proposed/approved SQL are in the receipt.
- Hosted readiness returned 200, anonymous config 401, authenticated config 200,
  and the blocked file route 404. The 1,800-second visit limit and Secure cookie
  remained enabled. No JavaScript errors, third-party browser requests or mobile
  overflow were observed in the completed live check.

The existing hosted password was preserved. No Azure SQL users, roles, firewall,
schema or data were changed; no extra Azure resources were created. Analyzer
error recovery was verified offline without injecting errors into Azure.
