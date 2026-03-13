# Architecture Decisions & Sprint History — Organism AI

> Reference document. Read when modifying specific components.
> For quick project context, see CLAUDE.md.
> For Sprint 1–8 decisions and historical bugs, see ARCHITECTURE_DECISIONS_ARCHIVE.md.

## Active Architecture Principles

These principles are from earlier sprints but remain actively enforced:
- Unicode escapes for Russian strings in .py files (never Cyrillic literals)
- code_executor via tmpfile + volume mount (not -c argument)
- Memory operations always wrapped in try/except
- _handle_conversation is the primary execution path (Q-10.4)
- Two-tier LLM: Haiku for classification, Sonnet for execution
- SolutionCache and KnowledgeBase owned by MemoryManager (ARCH-1.3)
- Gateway is single source of truth for chat_history (FIX-65)

## Business Context

Target client: gold mining artel, ~100 people, Russia (remote settlement).
IT infrastructure: 1C (Accounting + Warehouse + Payroll), 2-3 in-house 1C developers.
Pain points: manual document workflow, duplicate nomenclature in 1C, manual fuel tracking, Rosnedra reporting.
Contact: owner's son, ~40 years old, technically literate.

### Pricing Model
- Monthly: 300,000 RUB/month (includes ~25K RUB for Claude API)
- Season bonus: tied to measurable results (fuel savings, production volume)
- Onboarding (first 2 months): included
- Server (one-time): 70-100K RUB (client purchases)

### Commercial Proposal
See KP_Organism_AI_Artel.md in project knowledge.

## Sprint 9+ Decisions

### Q-9.0: LLM Intent Classifier
Replaced keyword-based _is_conversational() with async _classify_intent() using Haiku LLM.

**Problem**: CHAT_PATTERNS and TASK_SIGNALS were hardcoded keyword lists. Russian morphology is
too rich for keyword matching — "прикинь расход соляры", "почему топлива уходит больше нормы?"
are tasks but keyword matching missed them, routing to conversation mode → hallucinated responses.

**Solution**: Three-layer classification:
1. Pre-filter: /commands → always task
2. Pre-filter: ≤3 words + no digits → chat (avoids LLM cost for "привет", "спасибо")
   Exception: file extensions (xlsx, csv, etc.) → task
3. All other messages → Haiku LLM call (max_tokens=5, ~0.1s)
   Graceful degradation: LLM failure → assume task (safer than dropping request)

CHAT_PATTERNS and TASK_SIGNALS constants removed entirely.

Also added time-sensitive cache skip: queries containing "текущ", "актуал", "сейчас", "сегодн",
"свеж", "now", "current", "today", "latest" bypass solution cache to get fresh results.

### Q-9.1: Conversational mode upgrade — agent as Claude with extensions
Replaced verbose, rule-heavy conversation system prompt with a natural, concise one.
Upgraded model from Haiku (fast) to Sonnet (balanced) and max_tokens from 800 to 2000.
Removed HONEST LIMITATIONS, ANTI-HALLUCINATION RULES, FILE CREATION PROHIBITION blocks —
these were band-aids for keyword-based routing. With Q-9.0 LLM intent classification,
task messages no longer leak into conversation mode, so defensive rules are unnecessary.
New prompt focuses on communication style (think out loud, match user tone, be direct)
and honestly describes capabilities via live_context. User context section renamed to
"What you know about this user" for natural injection.

### Q-10.1: Universal Planner
Replaced 6 specialized planner prompts (PLAN_WRITING, PLAN_CODE, PLAN_RESEARCH,
PLAN_PRESENTATION, PLAN_MIXED, SPECIALIZED_PROMPTS dict) with a single PLAN_UNIVERSAL prompt.
The planner now chooses tools based on what the task actually needs, not by matching a category
template. The Haiku classifier (`_classify`) is kept for `task_type_hint` labeling and few-shot
store indexing in `loop.py`. Fallback chain: `_universal_plan` → `_fast_plan` → `_react_plan`.
Deleted method: `_specialized_plan`. Added: `_universal_plan`, `VALID_TASK_TYPES` set.

### Q-10.2: Writing fast path under LLM control
Before `_run_writing_task()` a Haiku gate `_needs_planner()` now checks whether a writing task
is self-contained (WRITE) or needs data gathering / multiple tools (PLAN). Tasks like
"напиши отчёт по добыче за март" now route to the planner instead of the text_writer fast path.
Fallback on Haiku error: keep fast path (safe default). Cost: ~10 tokens per writing task.

### Q-10.3: MAX_PLAN_STEPS = 10
Permanent fix for FIX-17. Plan step limit raised from 7 to 10 in `CoreLoop.MAX_PLAN_STEPS`.
`_validate_plan()` already used `self.MAX_PLAN_STEPS` (no hardcoded numbers to change).

### Q-9.1: Task Decomposer
New `src/organism/core/decomposer.py`. Haiku analyzes the task: if it has multiple distinct
phases (gather data + process + write report) it breaks it into 2-5 subtasks. Each subtask
executes sequentially through `CoreLoop.run()` with context passing (last 2 results injected).
Results aggregated by Haiku into one final answer. Gate: tasks under 100 chars skip the check.
Graceful degradation: if decomposition fails, continues with normal planning.

### Q-9.9: Subtask progress in Telegram
`progress_callback` passed through `IncomingMessage.metadata` →
`CoreLoop.run()`. During decomposition, Telegram shows "Часть X/Y: ..."
instead of the static ticker. The callback is fire-and-forget (try/except), so rate-limiting
or deleted messages do not crash the execution.

### Q-9.7: Docker production deployment
Production-ready containerization:
- `Dockerfile`: python:3.11-slim, system deps, pip install from pyproject.toml, HEALTHCHECK
- `docker-compose.yml`: bot + postgres (pgvector/pgvector:pg15), healthchecks, named volumes,
  DATABASE_URL injected, docker.sock mounted for sandbox
- `.env.production.example`: template with all required/optional env vars
- `scripts/deploy.sh`: git pull → docker-compose build → up -d --no-deps bot → health check

### Q-9.6: Multi-tenancy (artel_id isolation)
All DB queries in memory layer filtered by `settings.artel_id` (from ARTEL_ID env var).
Since `artel_id` column added via migration `_m002_artel_id` (not in ORM model), filtering
uses `text("artel_id = :artel_id")` with `.params()` for ORM queries and raw SQL conditions.
- `longterm.py`: save_task sets artel_id after INSERT; search_similar filters in vector,
  BM25, and fallback queries; get_stats filtered
- `solution_cache.py`: get() filters by artel_id; put() sets artel_id on INSERT;
  get_stats() uses raw SQL with artel_id filter
- `knowledge_base.py`: get_rules() filters by artel_id; add_rule() sets artel_id on INSERT

### FIX-33: Unified conversation+action mode
Removed hard TASK/CHAT classification (`_classify_intent` deleted). `_handle_conversation`
upgraded from plain `llm.complete()` to `llm.complete_with_tools()`. Tools obtained via
`self.registry.to_json_schema()` (all registered tools including MCP). Agentic loop: max 3
rounds of tool calls before forcing final text response.

Flow after FIX-33: media → `_handle_conversation` (with tools), everything else →
planner path. Conversational messages that fail planning are caught by FIX-16 fallback and
routed to `_handle_conversation` (with tools) — so they get tool access too.
`_build_tool_definitions()` wraps `registry.to_json_schema()`.

Impact: eliminates hallucinated actions in conversation mode. Previously the LLM would
describe actions without executing. Now it can call tools directly via complete_with_tools.

### FIX-34: Recent Work Context in Conversation

**Problem**: User asks "что по файлу который ты создал?" — agent responds "у меня нет доступа к файлу".
Root cause: `_handle_conversation` injects only semantically similar past tasks (vector search).
A query like "что по файлу" doesn't semantically match "создай Excel отчёт" → 0 results.

**Solution**: Add chronological recent tasks as a third context layer (alongside chat history
and semantic memory). `LongTermMemory.get_recent_tasks(limit=3)` returns last N completed
tasks ordered by `created_at DESC`, filtered by `artel_id`. Injected into
`_handle_conversation` system prompt as "Последние выполненные задачи:" section,
placed before semantic memory hits (more likely relevant for self-referential questions).

**Design**: No keyword detection, no if-chains. Always fetched (like chat_history).
Result capped at 3 tasks, preview 300 chars. Wrapped in try/except — graceful degradation.

Files changed: `memory/longterm.py`, `memory/manager.py`, `core/loop.py`.

### FIX-35: confirm_with_user Description Tightening

**Problem**: After FIX-33 gave `_handle_conversation` access to all tools, the LLM started
calling `confirm_with_user` for ordinary conversational responses (e.g. "I can't send video")
where no real action was being taken. The old description ("Ask user for approval before a
critical action") was vague enough that the LLM interpreted uncertainty as a reason to confirm.

**Solution**: Rewrote the tool description to be precise about the trigger condition:
"irreversible action on an external system". The LLM now reasons: "I'm explaining a limitation
→ that's not an action on an external system → tool not needed."

No system prompt changes, no routing logic — just a clearer tool description.

File changed: `tools/confirm_user.py`.

### Q-10.4: _handle_conversation as Primary Execution Path

**Problem**: CoreLoop.run() had multiple execution paths: keyword-based writing fast path
(`WRITE_KEYWORDS` + `_is_writing_task` + `_run_writing_task`), LLM writing gate
(`_needs_planner`), Planner+Executor pipeline, and conversation handler (FIX-33 fallback).
This created routing complexity, duplicate context-building, and multiple failure modes.

**Solution**: Make `_handle_conversation` the primary (and only) execution path for all
non-decomposed tasks. The LLM receives the message + all tools and decides itself whether
to answer directly, call tools, or combine both.

**What was removed**:
- `WRITE_KEYWORDS`, `SEARCH_KEYWORDS`, `INTENT_CLASSIFIER_PROMPT` constants
- `_is_writing_task()` function, `_needs_planner()` method, `_run_writing_task()` method
- Writing fast path block in `run()` (keyword detection, gate, fast path execution)
- Planner path in `run()` (plan, validate, re-plan, execute steps, soft-fail, FIX-29 fallback)

**What was kept** (for decomposer and future use):
- `Planner` class, `_validate_plan()`, `_execute_step()` methods
- `Evaluator`, `ContextBudget`, `KnowledgeBase` (initialized but not called from run())

**_handle_conversation upgrades**:
- New `memory_context` parameter: accepts pre-built context from `run()` to avoid double
  `on_task_start()` calls. Falls back to self-fetch for media-only path.
- `MAX_TOOL_ROUNDS`: 3 → 7 (handles complex multi-step tasks: search+compute+save+verify)
- `on_task_end()`: saves results to memory after every response (was missing before)
- Chat history: saves user message and assistant response after every interaction

**New flow in run()**:
1. Memory init
2. Media → `_handle_conversation` (unchanged)
3. Memory search + user facts + personality + few-shot (unchanged)
4. Cache check (unchanged)
5. Decomposer check for tasks > 100 chars (unchanged)
6. Everything else → `_handle_conversation(task_id, task, user_context, memory_context, user_id)`

**Benchmark**: 5/5 quick (100%), no regression. Score 0.93 avg quality.

Files changed: `core/loop.py`.

### SKILL-1: Technical Skills System

**Problem**: Agent creates basic, unformatted files (Excel without styling, plain text Word docs).
No expert knowledge about HOW to create professional-quality documents.

**Solution**: Static skill files in `config/skills/*.md` — expert templates written once by a human.
`SkillMatcher` in `core/skill_matcher.py` selects relevant skills via Haiku (~50 tokens)
and injects content into `_handle_conversation` system prompt as `skill_context`.

**Components**:
- `config/skills/excel.md` — openpyxl formatting: dark headers, alternating rows, auto-width
- `config/skills/docx.md` — Node.js `docx` library: A4, Arial, proper margins
- `config/skills/charts.md` — matplotlib: Agg backend, clean styling, dpi=150
- `core/skill_matcher.py` — `SkillMatcher.get_skill_context(task)`: Haiku selects 0-2 skills

**Docker changes**: Node.js + npm `docx` + matplotlib added to `sandbox/Dockerfile`.

**Integration**: `skill_context` injected first in system_parts (before user_context) as
"## How to create this file" section. Graceful degradation: any failure = empty string.

Files changed: `sandbox/Dockerfile`, `config/skills/*.md`, `core/skill_matcher.py`, `core/loop.py`.

### FIX-36: File delivery from _handle_conversation

**Problem**: Tools output "Saved files: filename.xlsx" but the LLM rewrites this as prose
in its final answer. Gateway's `_prepare_output()` never sees the pattern, so files are
described in text but never sent as Telegram attachments.

**Solution**: Track `created_files` during tool execution in `_handle_conversation`.
After each tool call, regex-scan `tool_output` for `Saved files: <filename>`.
After the agentic loop ends, append `\nSaved files: {last_file}` to the answer.
Gateway already has detection logic (FIX-23) that picks up this pattern and sends
the file as a binary attachment.

**Key details**:
- `created_files: list[str]` initialized alongside `all_tool_calls`
- Only the last file is appended (most complete result in multi-file scenarios)
- Gateway's `os.path.exists()` check handles missing files gracefully
- No changes to gateway.py or telegram.py needed

Files changed: `core/loop.py`.

### FIX-37: Plain text output — no Markdown in Telegram

**Problem**: Agent formats responses with Markdown (##, ---, |tables|, **bold**).
Telegram renders it partially — looks messy with raw symbols.

**Solution**: Added formatting instruction to `_handle_conversation` system prompt:
"Never use Markdown. No ##, no ---, no |tables|, no **bold**, no ```code blocks```.
Use plain text only. Structure with line breaks and emoji if needed."
Exception for file creation (Excel, Word, PDF) where internal formatting is fine.

Files changed: `core/loop.py`.

### FIX-38: Sandbox reads previously created files

**Problem**: `code_executor` sandbox mounts only a temp dir as `/sandbox` (ro) and
a temp `/output` (rw). Files in `data/outputs/` on the host are invisible inside
the container. When agent tries to read a previously created file (e.g. to update
an Excel), it gets `FileNotFoundError`.

**Solution**: Mount `data/outputs/` as an additional read-only volume at `/data/outputs/`
inside the container. Agent can now read existing files at `/data/outputs/filename.xlsx`
and write updated versions to `/output/` as before.

**Safety**: Mount is read-only — sandbox cannot modify or delete existing files.
Tool description updated to inform LLM about the `/data/outputs/` path.

Files changed: `tools/code_executor.py`.

### FIX-39: Fix save path in sandbox after FIX-38

**Problem**: After FIX-38, agent reads from `/data/outputs/` correctly but tries to
save updated files back to `/data/outputs/` — which is read-only. File never written,
"Saved files:" never printed, file never delivered.

**Solution**: Two changes to make the path rule unmissable:
1. `code_executor.py` description: explicit PATHS section — "Read from /data/outputs/,
   ALWAYS save to /output/"
2. `config/skills/excel.md`: added "Важно: пути в sandbox" section with read/write
   path rules and an update-existing-file example

Files changed: `tools/code_executor.py`, `config/skills/excel.md`.

### FIX-41: Decomposer signature mismatch

**Problem**: `TaskDecomposer.run()` passes `user_context` to `loop.run()`, but `run()`
did not accept it as a parameter. The context built from memory (user facts, personality,
chat history, few-shot examples) was always rebuilt from scratch for each subtask instead
of reusing the one already prepared by the parent `run()` call.

**Solution**: Added `user_context: str = ""` to `CoreLoop.run()` signature. When a
non-empty `user_context` is passed (from decomposer), the memory facts fetch is skipped
(`if not user_context:`). Personality, chat history, and few-shot are still appended
since they extend rather than replace.

Files changed: `core/loop.py`.

### FIX-43: Epistemic honesty in system prompt

**Problem**: LLM sometimes fabricated explanations for failures it hadn't observed —
e.g. "file didn't attach" when it actually received and read the file. This erodes
user trust.

**Solution**: Added an "Epistemic honesty" section to `_handle_conversation` system
prompt. Instructs the LLM to only describe what it actually observed (tool results,
chat history, user context) and never invent unseen causes. Includes concrete examples
of honest vs dishonest answers.

Files changed: `core/loop.py`.

### FIX-44: Disable decomposer from main execution path

**Problem**: TaskDecomposer (Q-9.1) added an extra Haiku LLM call on every task >100
chars. In practice `_handle_conversation` with tool-use loops handles complex tasks
natively — the decomposer added latency without clear benefit and could split tasks
that the LLM handles better as a single conversation.

**Solution**: Commented out the decomposer block in `CoreLoop.run()`. Raised
`MAX_TOOL_ROUNDS` in `_handle_conversation` from 7 to 10 so the agent has enough
rounds for genuinely complex tasks. `TaskDecomposer` class and `decomposer.py` are
kept intact for future orchestrator use.

Files changed: `core/loop.py`.

### FIX-45: Universal document handling in Telegram

**Problem**: Non-image, non-PDF documents sent to the Telegram bot (e.g. `.html`,
`.json`, `.csv`, `.txt`) were handled by only prepending the filename to the task.
The agent never saw the file content.

**Solution**: Download the document into BytesIO, attempt `decode("utf-8", errors="replace")`.
If the result contains no null bytes (`\x00`), treat it as readable text and inject
the first 8000 characters into the task string. Binary files fall back to filename-only.
Everything wrapped in try/except with the old behavior as fallback.

Files changed: `channels/telegram.py`.

### FIX-47: Remove BLOCKED_DOMAINS from web_fetch

**Problem**: A hardcoded blocklist in `web_fetch.py` prevented fetching from specific
domains (g2.com, statista.com, forbes.com, etc.). This was over-protective — the agent
should see real HTTP responses and decide itself how to proceed.

**Solution**: Removed `BLOCKED_DOMAINS` constant and the pre-request check. The existing
HTTP error handling (403/404/429 → `exit_code=1` with descriptive message) gives the
agent honest feedback. Updated tool description to mention that some sites may block bots.

Files changed: `tools/web_fetch.py`.

### FIX-48: LLM-based cache time-sensitivity gate

**Problem**: A keyword heuristic (`any(w in task.lower() for w in [...])`) decided
whether to skip the solution cache. This missed nuanced cases (e.g. "latest best
practices" should cache, "current gold price" should not) and triggered false positives.

**Solution**: Replace keywords with a Haiku LLM call: "Does this task require real-time
or current data that would be wrong if cached? Reply only: yes or no." Costs ~5 tokens,
adds minimal latency. Graceful fallback: if Haiku fails or times out,
`_time_sensitive = True` (skip cache — safer than serving stale data).

Files changed: `core/loop.py`.

### FIX-49: SkillMatcher relaxed prompt

**Problem**: `SKILL_SELECT_PROMPT` required the task to "clearly need to CREATE that type
of file". Tasks like "comparison table of 4 AI tools" didn't trigger `excel.md` because
the word "create" wasn't present — the user implied structured output without explicitly
requesting a file.

**Solution**: Rewritten prompt to select skills when the task requires creating a file
OR when the result is structured data best presented in that format. Added explicit
mapping examples: table/comparison → excel.md, document/instruction → docx.md,
chart/graph → charts.md, PDF report → pdf.md. Still returns [] for search, conversation,
and simple calculations.

Files changed: `core/skill_matcher.py`.

### FIX-50: Docker warm container pool

**Problem**: Every `code_executor` call created a new Docker container from scratch
(~2-3s overhead per call). Multi-step tasks with several code executions paid this
cost repeatedly, making total execution slow.

**Solution**: On `CodeExecutorTool.__init__()`, start a warm container with
`sleep infinity` (detached, persistent host dirs for `/sandbox` and `/output`).
Each execution writes code to the host sandbox dir, runs
`container.exec_run(["timeout", N, "python", "/sandbox/code.py"])`, reads output
files from host output dir. Thread-safe via `threading.Lock`. Falls back to cold
container creation on any failure (warm container died, Docker error, etc.).
`__del__` removes the warm container on shutdown.

Performance: eliminates ~2-3s container startup overhead per code_executor call.
Multi-step tasks see ~3x speedup on code execution phases.

Files changed: `tools/code_executor.py`.

### FIX-57c: fpdf2 replaces reportlab for PDF creation

**Problem**: reportlab requires manual TTFont registration for Cyrillic. On Windows
the system DejaVuSans paths don't exist, so fallback to Helvetica renders all
Cyrillic as squares. Font path resolution was fragile across OS environments.

**Solution**: Replace reportlab with fpdf2 (`pip install fpdf2`). fpdf2 supports
Unicode natively via `add_font()` with TTF files. DejaVuSans bundled in
`config/fonts/` (committed to repo). Font loading: bundled first, then system
paths, then Helvetica fallback. All `multi_cell()` calls use explicit
`new_x="LMARGIN", new_y="NEXT"` to prevent cursor position bugs. PDF creation
runs in executor thread via `run_in_executor`. Read path unchanged (PyPDF2).

Files changed: `tools/pdf_tool.py`, `pyproject.toml`, `config/fonts/*.ttf`.

### FIX-58: Remove hard cutoff in memory search

**Problem**: `search_similar()` had two filters that silently dropped results:
1. `dist_expr < SIMILARITY_THRESHOLD` in SQL WHERE — cut results before scoring
2. `best_score < 0.6 → return []` — discarded all results below arbitrary threshold

Both prevented the LLM reranker from seeing potentially relevant memories when
embedding similarity was moderate (common for paraphrased or loosely related tasks).

**Solution**: Remove both hard cutoffs. Keep only the upper adaptive K
(`best_score > 0.9 → return 1` for near-exact matches). Otherwise return up to
`limit` results sorted by hybrid score. The LLM reranker (`_rerank`) and the
agent itself are better judges of relevance than a fixed numeric threshold.

Files changed: `memory/longterm.py`.

### FORMATTER-1 (deferred — waiting for real 1C data)
Problem: queries spanning >90 days cause 1C MCP tools to return hundreds of time-series
rows. 730 rows of fuel data = ~15-20k tokens, overloading the agent context window.
Solution: `tools/formatter.py` — semantic aggregation (period averages + anomalies).
NOT to be confused with FIX-60 (removed arbitrary data truncation) — this is a
signal-preserving transformation, not data loss.
Activate when: real artel data with >90-day periods becomes available.
Affects: `src/organism/tools/mcp_client.py` (post-processing), `src/organism/mcp_1c/server.py`.
Estimate: 1-2 days, does not touch CoreLoop or Planner.

### ARCH-1.1: Evaluator in _handle_conversation (2026-03-11)
Problem: `_handle_conversation` used binary `quality_score = 1.0 if success else 0.0`,
bypassing the Evaluator entirely. SolutionCache stored incorrect scores, PVC got garbage data.
Fix: After final answer is obtained, call `self.evaluator.evaluate()` with a ToolResult
constructed from the answer. quality_score from Evaluator replaces binary placeholder.
Fallback on Evaluator failure: `0.8 if success else 0.2` (non-binary, better than 1.0/0.0).
SolutionCache storing added in `run()` after `_handle_conversation` returns — stores only
when `quality_score >= 0.8` (enforced by `SolutionCache.put()`).
Files: `src/organism/core/loop.py`.

### ARCH-1.2: Extract dead code from CoreLoop (2026-03-11)
Problem: CoreLoop.__init__ created Planner and TaskDecomposer, but neither was called
from run() or _handle_conversation after Q-10.4. Dead code making CoreLoop a God Object.
Fix: Created `src/organism/core/planner_module.py` with `PlannerModule` class that groups
Planner + TaskDecomposer. Removed `self.planner` and `self.decomposer` from CoreLoop.__init__.
Removed unused imports (Planner, TaskDecomposer) from loop.py. Kept PlanStep import
(used by _validate_plan and _execute_step which remain in CoreLoop).
Orchestrator unchanged — it already has its own routing logic, never used CoreLoop's planner.
Files: `src/organism/core/loop.py`, `src/organism/core/planner_module.py`.

### ARCH-1.3: SolutionCache and KnowledgeBase moved to MemoryManager (2026-03-11)
Problem: CoreLoop created SolutionCache and KnowledgeBase directly, bypassing MemoryManager.
Two unrelated places managing memory, blurred boundary.
Fix: Added `self.cache = SolutionCache()` and `self.kb = KnowledgeBase()` to MemoryManager.__init__.
Removed `self.cache` and `self.knowledge_base` from CoreLoop.__init__ and their imports.
All references repointed: `self.cache.*` -> `self.memory.cache.*` (already inside `if self.memory` guards).
`self.knowledge_base` was dead code in CoreLoop (created but never read after Q-10.4) — simply removed.
Graceful degradation: when memory=None, cache check and kb rules are skipped (existing guards).
Files: `src/organism/memory/manager.py`, `src/organism/core/loop.py`.

### ARCH-1.4: Orchestrator accessible from Telegram without --multi (2026-03-11)
Problem: Orchestrator only worked via `--multi` CLI flag. Telegram users had no access
to multi-agent mode.
Fix: CoreLoop.__init__ accepts optional `orchestrator` param. New `_classify_complex(task)`
method uses Haiku (5 tokens) to detect tasks requiring multiple agents. In `run()`, after
cache check but before `_handle_conversation`, complex tasks route to Orchestrator.
Fallback: if classifier or Orchestrator fails, falls through to `_handle_conversation`.
`build_loop()` in main.py gains `with_orchestrator` param. `run_telegram()` calls
`build_loop(registry, with_orchestrator=True)` — Telegram gets auto-routing.
Gateway/TelegramChannel unchanged — routing is internal to CoreLoop.
Files: `src/organism/core/loop.py`, `main.py`.

### Q-9.2–Q-9.5: Agent Factory (2026-03-11)
Role templates in `config/roles/*.md`, `AgentFactory` in `agents/factory.py`,
`MetaOrchestrator` in `agents/meta_orchestrator.py`. Commands: /agents, /create_agent, /assign.

### FIX-62: Agent Factory code review fixes (2026-03-11)
Four fixes after code review:
1. Recursion guard: `skip_orchestrator` param in CoreLoop.run(). MetaOrchestrator.run_as_agent()
   passes skip_orchestrator=True so _classify_complex() is skipped, preventing infinite recursion.
2. Routing descriptions: _route_choice() loads ## Description from role templates so Haiku
   can distinguish between roles when routing tasks.
3. Write verification: create_from_role/create_from_description check is_file() after writing
   agent JSON. Return None if file not written (instead of silent success).
4. Timestamp precision: agent_id uses %Y%m%d_%H%M%S (seconds) instead of %H%M (minutes)
   to prevent collisions on fast creation.
Bonus: benchmark.py cleans up agent artifacts after test #28.
Files: `core/loop.py`, `agents/meta_orchestrator.py`, `agents/factory.py`, `benchmark.py`.

### FIX-63: Agent personality via system prompt (2026-03-11)
Problem: run_as_agent() injected personality into task text. This contaminated memory
(on_task_start/on_task_end stored personality blob) and broke cache (personality+timestamp
made every cache key unique).
Fix: Added `extra_system_context` param to CoreLoop.run() and _handle_conversation().
MetaOrchestrator passes clean task + personality as extra_system_context. Memory and cache
receive only the original user task.
Files: `core/loop.py`, `agents/meta_orchestrator.py`.

### FIX-64: Skip artel personality when agent personality present (2026-03-11)
Problem: CoreLoop.run() injected artel personality (PersonalityConfig) AND agent personality
(extra_system_context) simultaneously. Two conflicting style instructions confused the LLM.
Fix: Skip PersonalityConfig injection when extra_system_context is non-empty.
Files: `core/loop.py`.

### FIX-65: Critical data bugs — chat history duplication, stats cache, orchestrator quality (2026-03-11)
Three bugs found in code review #4:
1. Chat history duplication: both _handle_conversation() and gateway.handle_message() saved
   user+assistant messages. Removed save from _handle_conversation() — Gateway is the single
   source of truth (it knows the final processed response).
2. /stats cache instance: _handle_stats() created `SolutionCache()` directly instead of using
   `memory.cache` (moved there in ARCH-1.3). Replaced with `memory.cache.get_stats()`.
3. Orchestrator quality bypass: orchestrator path in run() used hardcoded quality=0.85,
   skipping Evaluator. Now calls `self.evaluator.evaluate()` for calibrated quality_score.
Files: `core/loop.py`, `commands/handler.py`.

### FIX-66: Architecture cleanup — dead code extraction, single factory, CLI commands (2026-03-11)
Three cleanup changes:
1. Dead code extraction: `_validate_plan()`, `_execute_step()`, `MAX_RETRIES`, `MAX_PLAN_STEPS`
   moved from CoreLoop to PlannerModule (`core/planner_module.py`). These were dead since Q-10.4
   made `_handle_conversation` the primary path. Also removed unused `ContextBudget` and
   `PlanStep` imports from loop.py.
2. Single AgentFactory: CoreLoop.__init__ gains `factory` param. `build_loop()` creates one
   AgentFactory and passes it to both MetaOrchestrator and CoreLoop. Gateway reuses
   `loop.factory` instead of creating a new instance.
3. CLI commands: `run_single()` now creates a full CommandHandler with factory/loop/personality
   so /agents, /create_agent, /assign work from CLI (not just Telegram).
Files: `core/loop.py`, `core/planner_module.py`, `channels/gateway.py`, `main.py`.

### FIX-67: Media path context injection — personality, user_facts, few-shot (2026-03-11)
Problem: MEDIA-1 early return in CoreLoop.run() happened before user_context was built.
When users sent photos/PDFs, the agent lost artel personality, user facts, and few-shot
examples because those blocks were below the `if media: return` line.
Fix: Reordered run() so user_context is built first (user_facts → personality → few-shot),
then media early return passes full user_context + extra_system_context to
_handle_conversation. Memory search and chat history remain in the text-only path
(media path handles its own memory search internally).
Also removed `context_budget.py` from CLAUDE.md File Structure (unused since FIX-66).
Files: `core/loop.py`, `CLAUDE.md`.

### FIX-68: Orchestrator path — save result to long-term memory (2026-03-11)
Problem: orchestrator path in CoreLoop.run() skipped on_task_end(). Results from multi-agent
tasks were not saved to task_memories, invisible to semantic search, missing from causal
graph and few-shot store.
Fix: Added on_task_end() call after Evaluator scoring, before TaskResult construction.
Files: `core/loop.py`.

### Q-9.10: /errors command (2026-03-12)
New command: /errors [N] shows last N errors from error_log table. Default 5, max 20.
No new benchmark task — infrastructure utility command.
Files: `commands/handler.py`.

### Q-9.8: MCP JSON-RPC 2.0 (2026-03-12)
Added `/jsonrpc` endpoint to both MCP servers for Cursor/Claude Desktop compatibility.
JSON-RPC 2.0 methods: `initialize`, `tools/list`, `tools/call`, notifications (no id → 200 OK).
`mcp_serve/server.py`: serverInfo.name = "organism-ai", async handlers.
`mcp_1c/server.py`: serverInfo.name = "organism-1c", sync handlers wrapped in JSON-RPC envelope.
Error codes: -32700 (parse error), -32601 (method not found). Tool errors via `isError` in result.
Files: `mcp_serve/server.py`, `mcp_1c/server.py`.

### FIX-69: Increase max_tokens in _handle_conversation (2026-03-12)
Problem: max_tokens=2000 truncated LLM responses containing code_executor tool calls.
Python code for Excel/chart generation needs 3000+ tokens. Truncation produced empty
tool input → code_executor({}) → 10 retries → Task failed after 240s.
Fix: max_tokens raised from 2000 to 4096 in both LLM call sites within _handle_conversation.
Files: core/loop.py.

### FIX-70: Increase max_tokens to 8192 — safety margin (2026-03-12)
Problem: 4096 tokens (FIX-69) still marginal for complex file generation (large openpyxl
spreadsheets, multi-chart matplotlib scripts). Raised to 8192 for comfortable headroom.
Fix: max_tokens 4096 → 8192 in all three LLM call sites within _handle_conversation.
Files: core/loop.py.

### FIX-71: /assign chat history + documentation sync (2026-03-13)
Two fixes from code review #3:
1. /assign chat history: Gateway.handle_message() saved chat history only for regular tasks,
   not commands. /assign produces conversational agent output that should be in history.
   Added targeted save for /assign commands only (not /help, /stats etc. — those are utility).
2. Documentation sync: MAX_TOOL_ROUNDS was raised from 7 to 10 but not documented.
   Roadmap had Q-9.8 and Q-9.10 still in "open tasks" despite being completed.
   CLAUDE.md missing Q-9.10 entry.
Note: MAX_TOOL_ROUNDS was 3 (original) → 7 (Q-10.4) → 10 (undocumented change).
Files: `channels/gateway.py`, `organism_ai_roadmap.md`, `ARCHITECTURE_DECISIONS.md`, `CLAUDE.md`.

### ARCH-2: Archive Sprint 1-8 decisions (2026-03-13)
Moved all Sprint 1-8 architecture decisions, historical fixes (FIX-16 through FIX-32),
MEDIA-1/2/3, TOOL-1, Code Protection, and historical benchmark data to
ARCHITECTURE_DECISIONS_ARCHIVE.md. Main file now contains only Sprint 9+ decisions
and active architecture principles.
Files: `ARCHITECTURE_DECISIONS.md`, `ARCHITECTURE_DECISIONS_ARCHIVE.md`, `CLAUDE.md`.

## Testing History

### Current Benchmark (March 2026)
- 28 tasks total (28/28 success with Docker+DB)
- Average Quality Score: 0.93
- Sprint 9 tasks: Agent Factory, Universal Planner, MCP JSON-RPC — all passing
- For historical benchmark data, see ARCHITECTURE_DECISIONS_ARCHIVE.md
