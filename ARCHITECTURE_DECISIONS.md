# Architecture Decisions & Sprint History — Organism AI

> Reference document. Read when modifying specific components.
> For quick project context, see CLAUDE.md.

## Key Architecture Decisions

### Fast path for writing tasks
`_is_writing_task()` in loop.py detects writing keywords (напиши, составь, etc.)
and routes directly to text_writer, bypassing Planner. BUT: if task also contains
search keywords (найди, актуальные, etc.) — goes through Planner for mixed plan.

### Two-phase Planner (Q-1.2)
Phase 1: Haiku classifies task type (writing/code/research/data/presentation/mixed)
Phase 2: Sonnet gets specialized prompt with only 2-3 relevant tools (not all 7)

### Evaluator 2.0 (Q-1.1)
Returns quality_score 0.0-1.0 alongside success/fail.
Score saved to memory, used for caching decisions and self-improvement.

### Plan Validation (Q-1.4)
Before execution: checks tool exists, required inputs present, step count ≤ 5,
no circular dependencies. Auto re-plans on validation failure.

### L1 Solution Cache (Q-2.1)
`SolutionCache` in memory/solution_cache.py. In `CoreLoop.run()`, after memory search:
1. Haiku normalizes task to canonical form (synonym folding, filler removal)
2. SHA-256 hash of canonical form used as cache key
3. `solution_cache` DB table checked — cache hit returns immediately
4. Successful results (quality >= 0.8) stored after execution, TTL 30 days
5. On hash collision with higher quality result, entry is refreshed
Gate: cache check only runs when `self.memory` is set (DB available).

### Enriched Embeddings (Q-1.5)
Embeddings include `[TASK] text [TOOLS] tools [OUTCOME] result` for better
semantic search. Distinguishes similar tasks with different tools/outcomes.

### Unicode escapes in loop.py
Russian keywords stored as unicode escapes (\u043d\u0430\u043f\u0438\u0448\u0438)
to avoid encoding issues on Windows PowerShell. DO NOT replace with Cyrillic.

### _sanitize_json() in Planner
Cleans \n \r \t inside JSON strings before parsing. Solves "Invalid control character" errors.

### code_executor via tmpfile
Code passed to Docker via temp file + volume mount (/sandbox/code.py), NOT via -c argument.

### Memory Graph (Q-5.2)
memory_edges table: temporal|causal|entity|procedural edges between tasks.
Edges inferred async by CausalAnalyzer (fire-and-forget after task completion).

### Temporal Fact Tracking (Q-5.1)
UserProfile and KnowledgeRule have valid_from/valid_until columns.
Facts are archived on update (old row gets valid_until, new row created).
/history command shows fact change timeline.

### Adaptive Search Policy (Q-5.5)
Intent classified by Russian keyword regex (no LLM cost): factual|temporal|causal|entity|procedural.
Each intent activates different memory sources with different weights.
Graceful degradation: if graph empty, falls back to pure vector search.

### Intent-aware fast path skip
In CoreLoop.run(), before _is_writing_task() check: if SearchPolicy classifies
intent as temporal/causal/entity AND memory_context is non-empty, skip writing
fast path so the planner can answer from memory instead of generating new content.

### Orchestrator State Machine (Q-6.1)
Orchestrator uses a state machine workflow (INIT → ROUTING → EXECUTING → EVALUATING → DONE/FAILED).
Replaces simple sequential loop with graph-based control, conditional edges, parallel agent execution.
State transitions logged via structlog for debugging.

### Proactive Scheduler (Q-6.2)
`ProactiveScheduler` in core/scheduler.py. Background asyncio task polls every 30s.
`ScheduledJob` dataclass: name, task_text, schedule_type (daily/weekly/interval), time_of_day, weekday.
`DEFAULT_ARTEL_JOBS`: morning_summary (daily 06:30), weekly_production (Mon 08:00), fuel_anomaly_check (360min).
`_should_run()` computes next run time, compares with `last_run`. Scheduler NOT started in benchmark.

### Human-in-the-loop Approval (Q-6.3)
`HumanApproval` in core/human_approval.py. `PendingApproval` dataclass with asyncio.Event.
`request_approval(description)` sends to Telegram via `send_fn`, waits up to 300s.
`resolve(short_id, approved)` finds by request_id prefix (8 chars), sets event.
`ConfirmUserTool` wraps HumanApproval, registered only in Telegram mode (`run_telegram()`).
Planner prompts (planner_fast.txt, planner_react.txt) list confirm_with_user for critical actions.

### Configurable Personality (Q-6.4)
`PersonalityConfig` in core/personality.py. Loads `config/personality/{artel_id}.md`.
Sections parsed from `## Heading` structure: style, terminology, escalation, report_prefs, working_hours.
`get_system_prompt_addition()` returns full personality as system prompt suffix.
`get_term(key)` returns terminology mapping. `get_section(name)` returns raw section text.
Injected into `user_context` in CoreLoop.run(), after user_facts.
`settings.artel_id` (env ARTEL_ID) selects personality file, default fallback.

### Gateway Abstraction (Q-6.5)
`Gateway` in channels/gateway.py. Channel-agnostic message router.
`IncomingMessage`/`OutgoingMessage` dataclasses in channels/base.py.
`handle_message()`: routes commands → CommandHandler, tasks → CoreLoop, long text → temp file.
`CLIChannel` in channels/cli_channel.py uses Gateway instead of direct CoreLoop.
`TelegramChannel` refactored to use Gateway, keeps progress ticker (Telegram-specific).
`broadcast()` sends to all registered channels (for scheduler notifications).

### Re-plan with available tools hint
When plan validation fails (tool not in registry), re-plan appends available tools list
to the task text (`IMPORTANT: Only use these tools: [...]`) so the LLM picks from
valid tools only. Prevents regression when planner prompts list tools not registered
in current mode (e.g., confirm_with_user absent in CLI/benchmark mode).

### Structured Reflections (Q-7.1)
Agent `_reflect()` in base.py returns structured JSON: {score, failure_type, root_cause,
corrective_action, confidence} instead of just {score, insight}. Haiku prompt asks for
tool_error|plan_error|llm_error|timeout|validation|none classification. `AgentReflection`
model has 4 new nullable columns. Graceful fallback: if Haiku returns old {score, insight}
format, fills failure_type="unknown", confidence=0.5. max_tokens=200 (was 80).

### Benchmark-Driven Prompt Optimization (Q-7.2)
`BenchmarkPromptOptimizer` in self_improvement/benchmark_optimizer.py. Pipeline:
1. Read current prompt (PVC or file fallback)
2. Run --quick benchmark (5 tasks) for baseline score
3. Haiku generates MAX_VARIANTS=3 mutated prompt versions
4. Each variant: deploy via PVC -> quick benchmark -> record score
5. If best > baseline + MIN_IMPROVEMENT (0.03) -> keep deployed; else restore original
Evaluator.evaluate() now uses PVC-managed prompt (`get_active("evaluator")`) with
file-based EVALUATOR_PROMPT as fallback. OPTIMIZABLE_PROMPTS dict expandable for
planner_fast/react. CLI: `python main.py --optimize-prompts`.

### Few-Shot Example Curation (Q-7.3)
`FewShotStore` in memory/few_shot_store.py. `FewShotExample` table: task_text, task_type,
plan_json, quality_score, tools_used, embedding(1536), usage_count. Save: quality >= 0.75,
dedup by task prefix + type, MAX_EXAMPLES=100 FIFO. Get: vector cosine distance search
(pgvector `<=>`), fallback to quality-ordered if no embedding. TOP_K=3. Injected into
`user_context` in CoreLoop.run() (before cache check, after personality). Saved after
on_task_end for both planned tasks and writing fast path.

### Evolutionary Prompt Search (Q-7.4)
`PromptPopulationMember` table in database.py: prompt_name, content, generation, fitness,
eval_count, parent_id, mutation_type, is_active. `EvolutionaryPromptSearch` in
self_improvement/evolutionary_search.py. Constants: POPULATION_SIZE=5, MIN_POPULATION=3,
ELITE_COUNT=2, MUTATION_TYPES=[rephrase, restructure, specialize].
Cycle: `seed_population()` creates MIN_POPULATION members (1 original + mutations);
`evolve()` evaluates all via quick benchmark, keeps top ELITE_COUNT, culls rest,
mutates children from elites, deploys best via PVC. `evolve_all()` iterates OPTIMIZABLE_PROMPTS.
CLI: `--evolve-prompts` flag. Scheduler: `weekly_prompt_evolution` job (Sunday 3AM, disabled by
default). Internal tasks use `__internal__:evolve_prompts` prefix, handled by
`ProactiveScheduler._run_internal()` bypassing CoreLoop.

### Cross-Agent Knowledge Sharing (Q-7.5)
`MemoryManager.get_cross_agent_insights(current_agent, task_text, limit=5)` queries
`agent_reflections` WHERE agent_name != current_agent, score >= 3 OR corrective_action
IS NOT NULL. Keyword overlap scoring (min 2 common words) for relevance filtering.
`BaseAgent._format_cross_insights(insights)` formats as `[Insights from other agents:]`
block, preferring corrective_action over insight, 150 char cap per entry.
`_enrich_with_cross_insights(task)` helper in BaseAgent: fetch + format + prepend to task.
Each agent's `run()` calls `_enrich_with_cross_insights()` before execution; orchestrator
also injects in both `_sm_run()` and `_legacy_run()` before `agent.run()`. Graceful
degradation: all wrapped in try/except, empty list on any failure.

### MCP Client in ToolRegistry (Q-8.1)
`MCPServerConfig` (dataclass): name, url, api_key, enabled. `MCPClient` in tools/mcp_client.py:
HTTP-based discovery (POST /tools/list) and invocation (POST /tools/call). Tools cache after
first discovery. `MCPTool(BaseTool)` wraps each remote tool: name=`mcp_{server}_{tool}`,
description=`[MCP:{server}] ...`, input_schema from server's inputSchema. `ToolRegistry` gains
`register_mcp_server(config)` (async, returns count), `unregister_mcp_server(name)`,
`list_mcp_servers()`. Config via env: `MCP_SERVERS='[{"name":"1c","url":"http://..."}]'`.
`build_registry()` queues configs as `_pending_mcp`; async callers run `_connect_mcp(registry)`.
Plan validation: MCP tools (mcp_* prefix) skip input schema checks (dynamic schemas).
Planner prompts mention MCP tools for LLM awareness. Graceful: server down = 0 tools, no crash.

### MCP Server for 1C (Q-8.2)
`MCP1CServer` in src/organism/mcp_1c/server.py. Standard MCP protocol (POST /tools/list,
POST /tools/call). 5 read-only tools: search_counterparties, get_fuel_consumption,
get_equipment_registry, get_production_data, get_spare_parts_requests. Two modes:
`DemoDataProvider` returns realistic hardcoded artel data (gold mining context);
`LiveDataProvider` skeleton for real 1C OData integration. All Russian strings as unicode
escapes. `create_app(mode, odata_url, odata_user, odata_password)` factory returns aiohttp
Application. CLI: `python -m src.organism.mcp_1c.server --port 8090 --mode demo`.
Connect from Organism AI via MCP_SERVERS env: `[{"name":"1c","url":"http://localhost:8090"}]`.

### Duplicate Search Service (Q-8.3)
`DuplicateFinderTool` in tools/duplicate_finder.py. Local tool (not MCP) that finds
duplicate entries in 1C directories using semantic similarity. Strategy: accept list of
entity names → compute embeddings (OpenAI text-embedding-3-small via get_embedding()) →
pairwise cosine similarity via numpy matrix multiplication → union-find grouping of
connected duplicates. SIMILARITY_THRESHOLD=0.85, MAX_ENTITIES=200 safety cap. Input:
entities (list[str]), entity_type (counterparties|equipment|nomenclature), threshold (float).
Real workflow: fetch entities via MCP tools first, then pass to duplicate_finder.
Registered in build_registry() (main.py + benchmark.py). Plan validation: skips input
checks (entities can be empty). Planner prompts updated with duplicate_finder tool.

### Organism AI as MCP Server (Q-8.4)
`OrganismMCPServer` in src/organism/mcp_serve/server.py. Exposes 4 tools via standard MCP
protocol: execute_task (delegate task to CoreLoop or Orchestrator), get_stats (system
statistics via CommandHandler), search_knowledge (semantic search via memory.longterm),
list_capabilities (available tools, agents, MCP servers). `create_organism_app()` returns
aiohttp Application. CLI: `python main.py --serve-mcp --mcp-port 8091`. Output capped at
3000 chars per response. Listens on 0.0.0.0 for network access. No auth (post-MVP).
Connect from other AI: `MCP_SERVERS='[{"name":"organism","url":"http://localhost:8091"}]'`.

### Agent-to-Agent Protocol (Q-8.5)
`PeerAgent` dataclass (name, url, api_key, capabilities, enabled). `PeerRegistry` manages
known peers with add/remove/list/to_prompt_hint(). `A2AClient` sends tasks to peers via
MCPClient (reuses Q-8.1 HTTP client): send_task() calls peer's execute_task MCP tool,
discover_capabilities() calls list_capabilities. `DelegateToAgentTool(BaseTool)` wraps
A2AClient for Planner: input requires peer_name + task, validates peer exists, delegates
and returns result. Registered in build_registry() only when A2A_PEERS env is set.
Config: `A2A_PEERS='[{"name":"artel-south","url":"http://192.168.2.100:8091"}]'`.
Without configured peers: delegate_to_agent not registered, zero impact on existing flow.
Plan validation: requires peer_name and task in input. Planner prompts updated.

### Database Schema v2 (DB-1)
Comprehensive DB revision: versioned migrations, indexes, multi-tenancy readiness, error_log, retention.
`SchemaMigration` table tracks applied migrations (version, name, applied_at). `init_db()` runs
`Base.metadata.create_all` then iterates `_MIGRATIONS` list — skips already-applied versions.
All old inline migrations (_migrations_51, _migrations_71, _migrations_73, _migrations_74) removed.

7 versioned migrations (append-only, idempotent):
| # | Name | Purpose |
|---|------|---------|
| 1 | base_indexes | 14 performance indexes on task_memories, solution_cache, agent_reflections, etc. |
| 2 | artel_id | Add artel_id VARCHAR DEFAULT 'default' to 6 core tables + indexes |
| 3 | error_log | CREATE TABLE error_log (level, component, message, traceback, task_id, notified) + 3 indexes |
| 4 | structured_reflections | Q-7.1 columns on agent_reflections (failure_type, root_cause, etc.) |
| 5 | result_size | Add result_hash column to task_memories for dedup |
| 6 | retention_helpers | 4 PostgreSQL functions: cleanup_expired_cache(), cleanup_old_reflections(N), cleanup_old_errors(N), cleanup_old_edges(N) |
| 7 | few_shot_indexes | Additional indexes on few_shot_examples and prompt_population |

`ErrorLog` model: level, component, message, traceback, task_id, task_text, artel_id, notified, created_at.
`notified=false` enables monitoring ("show unnotified errors"). `/cleanup` command and `db_cleanup`
scheduled job (Sun 04:00) call retention functions. Result truncation in longterm.py: 10000 chars (was 2000).

## Tool Implementation Details

| Tool | Key Detail |
|---|---|
| code_executor | Docker sandbox. Code passed via tmpfile + volume mount (NOT -c argument). Solves long code truncation |
| web_search | Tavily API |
| web_fetch | Blocks: g2.com, statista.com, forbes.com, gartner.com. 403/404 -> exit_code=0, graceful skip. verify=False for Russian sites |
| text_writer | Returns full content in output (not just preview) |
| pptx_creator | python-pptx, no Docker required |
| file_manager | Uses OUTPUTS_DIR from base.py |
| confirm_with_user | Wraps HumanApproval. Telegram-only. Planner picks it for critical writes (Q-6.3) |

### Core Loop Mechanics
- `{{step_N_output}}` placeholders -- CoreLoop._resolve_input() replaces with actual previous step results
- WRITE_KEYWORDS via unicode escapes (avoid Windows cp1251 issues)
- Intent-aware fast path skip: temporal/causal/entity intents with memory context bypass writing fast path
- Planner: max_tokens=4096, JSON parsed from "Thought + JSON" format
- Evaluator: lenient mode -- doesn't fail on 403 responses, old data, or empty output

## Development Roadmap — Quality Plan ✅ COMPLETE

### Sprint 1 ✅ (Foundation) — COMPLETE
- Q-1.1: Evaluator 2.0 — gradient quality_score ✅
- Q-1.2: Two-phase Planner — Haiku classifier + specialized prompts ✅
- Q-1.3: Smart fast path — search keywords bypass writing shortcut ✅
- Q-1.4: Plan Validation Layer ✅
- Q-1.5: Enriched embeddings ✅

### Sprint 2 ✅ (Multi-level RAG) — COMPLETE
- Q-2.1: L1 Solution Cache — hash + task normalization ✅
- Q-2.2: Hybrid Search (vector + BM25 ts_vector in PostgreSQL) ✅
- Q-2.3: Metadata filtering + Adaptive K ✅
- Q-2.4: LLM Reranking (Haiku) for top candidates ✅
- Q-2.5: L3 Knowledge Base — rules table + extraction mechanism ✅

### Sprint 3 ✅ (Smart Agents) — COMPLETE
- Q-3.1: Agent specialization (temperature, max_iterations, TemperatureLocked) ✅
- Q-3.2: Writer 3-phase (outline → draft → polish) ✅
- Q-3.3: Inter-agent context summarization (Haiku) ✅
- Q-3.4: Agent Self-Reflection + save to agent_reflections table ✅
- Q-3.5: Context Window Budget (3000t sweet spot, priority trimming) ✅

### Sprint 4 ✅ (Personalization & Automation) — COMPLETE
- Q-4.1: User Facts Extraction — Haiku extracts name/role/company from task text ✅
- Q-4.2: Personal context in system prompt — user_context injected into all LLM calls ✅
- Q-4.3: Commands /remember, /forget, /profile, /style, /stats, /improve, /prompts ✅
- Q-4.4: Automatic improvement cycle — failures → patterns → KnowledgeBase rules ✅
- Q-4.5: Prompt Version Control — versioned prompts, running quality avg, auto-rollback ✅

### Sprint 5 ✅ (Memory Enhancement — Graph + Temporal) — COMPLETE
- Q-5.1: Temporal fact tracking — valid_from/valid_until in user_profile and knowledge_rules ✅
- Q-5.2: Memory edges table — memory_edges with temporal|causal|entity|procedural edges ✅
- Q-5.3: Causal inference — async background worker analyzes task relationships via Haiku ✅
- Q-5.4: Procedural templates — extract and reuse successful tool+code patterns ✅
- Q-5.5: Adaptive search policy — intent classification and weighted multi-source memory search ✅

### Sprint 6 ✅ (Orchestration Upgrade) — COMPLETE
- Q-6.1: State machine — graph-based orchestrator workflow (INIT→ROUTING→EXECUTING→EVALUATING→DONE) ✅
- Q-6.2: Proactive scheduler — ScheduledJob (daily/weekly/interval), DEFAULT_ARTEL_JOBS, /schedule commands ✅
- Q-6.3: Human-in-the-loop — HumanApproval + ConfirmUserTool, asyncio.Event + 300s timeout, /approve /reject ✅
- Q-6.4: Configurable personality — PersonalityConfig from config/personality/{artel_id}.md, sections + terms ✅
- Q-6.5: Gateway abstraction — IncomingMessage/OutgoingMessage, Gateway router, CLIChannel, TelegramChannel refactor ✅

### Sprint 7 ✅ (Self-Improvement 2.0) — COMPLETE
- Q-7.1: Structured reflections — upgrade from {score, insight} to {failure_type, root_cause, corrective_action, confidence} ✅
- Q-7.2: Benchmark-driven prompt optimization — auto-pipeline: generate variants -> run benchmark.py -> select winner -> deploy via PVC ✅
- Q-7.3: Few-shot example curation — store successful task-result pairs as demonstrations, top-3 injected into planner prompts ✅
- Q-7.4: Evolutionary prompt search — population of 3-5 variants per component, weekly evaluate-mutate-select cycle ✅
- Q-7.5: Cross-agent knowledge sharing — reflection insights from one agent automatically inform planning of others ✅

### Sprint 8 ✅ (Integration — MCP + 1C) — COMPLETE
- Q-8.1: MCP client in ToolRegistry — discover and invoke tools from external MCP servers ✅
- Q-8.2: MCP server for 1C — read operations: search counterparties, fuel data, equipment registry. Read-only first ✅
- Q-8.3: Duplicate search service — semantic search across 1C entities via MCP. Key artel use case ✅
- Q-8.4: Organism AI as MCP server — expose task execution capabilities for other AI systems ✅
- Q-8.5: Agent-to-Agent protocol — prepare architecture for multi-system collaboration ✅

### Future Priorities (Beyond Sprint 8)

**High Priority**
- Computer vision — YOLO: counting bucket turns (excavator productivity), truck trip counter, ore level in sluice box
- Voice input — Telegram voice -> Whisper API -> text -> CoreLoop (for workers in gloves)
- Web dashboard for director — FastAPI + React, real-time metrics (production, fuel, equipment downtime)

**Medium Priority**
- Role-based access control — Director (unlimited), Foreman (50 tasks/day), Worker (15 tasks/day)
- Multi-tenancy — data isolation per client, separate DB schemas
- Billing — API usage tracking per client

**Low Priority**
- Junior developer hire — from first client revenue, takes routine maintenance
- Second client — use first artel as case study

## Strategic Vision
Organism AI is the foundation for a one-person + AI-agents unicorn company.
All architectural decisions should consider scaling to an autonomous AI team
that could eventually replace entire departments while maintaining one human as architect.

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

## Testing History

### Pre-Quality Plan (Feb 2026)
Blocks A-D passed: 17/17 tasks (basic tools, multi-step chains, multi-agent, edge cases).
Block D (real artel tasks): 3/5+ completed (KP, work order template, production plan).

### Quality Plan Benchmark (Mar 2026)
- Baseline 10 tasks: 10/10 (100%), avg quality 0.848
- Sprint 5 tasks (11-14): 4/4 (100%), avg quality 0.55 (cold graph, expected)
- Overall: 14/14 (100%), avg quality 0.78
- Cache hits: 5/14 (36%)

### Sprint 6 Benchmark (Mar 2026)
- Expanded to 19 tasks (10 baseline + 4 Sprint 5 + 5 Sprint 6)
- Without Docker/DB: 16/19 (84.2%), avg quality 0.85
- Sprint 6 tasks (15-19): 5/5 (100%), avg quality 0.90
- Failures are environmental only (Docker/DB unavailable), not code regressions
- Re-plan regression fixed: confirm_with_user in planner prompts → available tools hint in re-plan

### Sprint 7 Benchmark (Mar 2026)
- Expanded to 23 tasks (10 baseline + 4 Sprint 5 + 5 Sprint 6 + 4 Sprint 7)
- Sprint 7 tasks (20-23): cross-agent knowledge sharing, structured reflections, few-shot, evolutionary
- Without Docker/DB: ~20/23 (~87%), avg quality 0.85
- Failures are environmental only (Docker/DB unavailable), not code regressions

### Critical Bugs Fixed (historical)
- Evaluator too strict -> lenient rules added
- web_fetch 403 crash -> graceful skip with exit_code=0
- code_executor -c argument truncation -> tmpfile + volume mount
- Planner control chars in JSON -> sanitize before parsing
- Telegram long messages cut off -> file attachment for >800 chars
- SSL verify fail on Russian sites -> verify=False + ConnectError graceful handling
- datetime.now(timezone.utc) incompatible with asyncpg -> datetime.utcnow()
- Writing fast path intercepting temporal/causal queries -> intent-aware skip
- Re-plan picking unavailable tools -> available tools hint appended to re-plan task (Q-6.3/Q-6.5)
- FIX-16: Long conversational messages (>80 chars) bypassed _is_conversational() -> Planner returned text instead of JSON -> user saw error. Fixed: universal fallback in CoreLoop.run() — "Could not parse plan" routes to _handle_conversation()
- FIX-17: Plan validation failed for complex tasks (>5 steps). MAX_PLAN_STEPS raised to 7. Re-plan hint now explicitly instructs to consolidate steps within limit. Permanent fix planned for Sprint 9 via automatic task decomposition through orchestrator.
- FIX-18: Bot answered from hardcoded capability list instead of live system state. Fixed: _handle_conversation now queries registry.list_all(), scheduler.list_jobs(), memory availability at runtime and builds system prompt from real data. Bot self-knowledge is now always accurate and automatically reflects new tools/integrations.
- FIX-19: Bot could hallucinate capabilities (e.g. promise to create scheduled jobs via chat). Fixed: added HONEST LIMITATIONS and ANTI-HALLUCINATION RULES block to _handle_conversation system prompt. Bot now distinguishes between what it knows vs what it can actually execute right now.

## Code Protection (PROTECT-1)
Three-layer regression protection:
1. pre_commit_check.py — syntax, Cyrillic literals, critical imports. Run before every commit.
2. benchmark.py --quick — 5-task quality check after changes to core files. Score must not drop.
3. GitHub Actions CI — automatic pre_commit_check.py + benchmark --quick on every push to master.
CLAUDE.md updated: pre_commit_check.py is mandatory before any commit.

## FIX-22: Tasks routed to conversation mode instead of execution
Short task messages (<80 chars) like "создай excel таблицу" were classified as conversational
by _is_conversational() and handled by _handle_conversation() which has no tools — causing LLM
to hallucinate file creation. Three fixes: (1) Expanded TASK_SIGNALS with excel, таблиц, файл,
отчёт, график, посчитай, построй, скачать. (2) Replaced <80 char rule with <15 char + no-digits
rule — only truly trivial messages are conversational. (3) Added FILE CREATION PROHIBITION block
to _handle_conversation system prompt as safety net — if a task still leaks through, LLM says
"Выполняю..." instead of hallucinating results.

## TOOL-1: pdf_tool
New tool: create PDF from text/markdown (reportlab) and read/extract text from PDF (pypdf2).
Use cases: reports, grant applications, commercial proposals, any document output.
Registered in main.py, benchmark.py. Added to PLAN_WRITING and PLAN_MIXED prompts.

## FIX-21: Binary files not sent in Telegram
Binary files (.xlsx, .pptx, .pdf, .docx) caused UnicodeDecodeError in telegram.py because
both handle_task and voice handler opened all files with `open(path, "r", encoding="utf-8")`.
The except block silently swallowed the error and never called send_document.
Fixed: BINARY_EXTENSIONS tuple at module level. Both is_file blocks now check extension first;
binary files skip text preview and go directly to answer_document. Text files keep existing logic.

## FIX-20: openpyxl in Docker sandbox
openpyxl missing in sandbox → Excel tasks fell back to CSV with dummy data.
Fixed: openpyxl added to Dockerfile. Planner prompt updated: fallback must use real data.

## FIX-23: Gateway not sending files from multi-line code_executor output
Gateway._prepare_output() detected file paths only when output was a single line ending with
a known extension. But code_executor returns multi-line output with "Saved files: filename.xlsx"
at the end. The "\n" not in stripped check always failed → file never sent as attachment.
Fixed: Added regex extraction of filename from "Saved files: <filename>" pattern before the
existing is_file_path check. Constructs candidate path in data/outputs/, verifies existence
and extension, routes to _prepare_file_response(). Also added .pdf to _file_exts tuple.

## Q-9.0: LLM Intent Classifier
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

## FIX-24: Memory not initialized for conversational messages
Memory.initialize() was called AFTER intent classification, so conversational messages
returned before memory was ready. This meant _handle_conversation had no access to
chat history (HIST-1), user facts, or any memory-backed features.
Fixed: moved memory.initialize() before _classify_intent(). The second initialize()
call in the task path is harmless (idempotent _initialized flag) but the memory search
block no longer calls it redundantly.

## FIX-25: Conversation handler lacks longterm memory + small history window
_handle_conversation had only 4 messages of chat history and no access to longterm memory.
User couldn't reference past tasks ("remember that salary report?") in conversation mode.
Fixed: (1) Increased chat history window from 4 to 10 messages. (2) Added longterm memory
search via memory.on_task_start() — top 3 relevant past tasks injected into system prompt
as "Relevant past tasks:" block. Both changes gated on self.memory + try/except.

## FIX-26: Memory retention expanded — agent never forgets
Longterm memory search had a hardcoded 90-day cutoff, contradicting the "agent remembers
everything" design principle. Tasks older than 3 months were invisible to memory search.
Fixed: (1) Replaced 90-day cutoff with configurable MEMORY_RETENTION_DAYS setting (default
1095 = 3 years). Imported settings in longterm.py. (2) Increased chat history storage limit
from 50000 to 100000 messages per user. The retention period is now an env var that can be
overridden without code changes.

## Q-9.1: Conversational mode upgrade — agent as Claude with extensions
Replaced verbose, rule-heavy conversation system prompt with a natural, concise one.
Upgraded model from Haiku (fast) to Sonnet (balanced) and max_tokens from 800 to 2000.
Removed HONEST LIMITATIONS, ANTI-HALLUCINATION RULES, FILE CREATION PROHIBITION blocks —
these were band-aids for keyword-based routing. With Q-9.0 LLM intent classification,
task messages no longer leak into conversation mode, so defensive rules are unnecessary.
New prompt focuses on communication style (think out loud, match user tone, be direct)
and honestly describes capabilities via live_context. User context section renamed to
"What you know about this user" for natural injection.

## FIX-27: Intent classifier prompt — principle instead of lists
Replaced list-based INTENT_CLASSIFIER_PROMPT with principle-based one. Old prompt listed
categories (files, calculations, search, greetings, thanks). New prompt states a single
principle: "wants a NEW action performed right now" = TASK, "is conversing about past work,
reflecting, giving feedback" = CHAT. Added key distinction examples showing the difference
between action requests and references to past work. This enables proper routing of messages
like "помнишь тот excel по зарплатам?" → CHAT → longterm memory lookup instead of TASK.

## FIX-28: Multi-query memory search for conversational mode
Single-query memory search in _handle_conversation missed relevant past tasks when the user
phrased their reference differently from the original task text. E.g., "помнишь тот excel
по зарплатам?" didn't match "Создай CSV таблицу зарплат по регионам России".
Fixed: Haiku generates 2-3 short search queries from the user message, each query runs
against longterm memory independently, results are deduplicated by task prefix and capped
at 5 unique results. Graceful fallback: if Haiku fails, falls back to original message as
single query. JSON array parsing uses regex extraction for robustness.

## FIX-29: Explicit memory search status in conversation prompt
Sonnet didn't know that memory search had already been executed before its turn, so it
would say "let me search" or "I'll look that up" instead of reporting found results.
Fixed: Changed longterm_context wording from passive "Relevant past tasks:" to active
"Memory search already completed. You found these relevant past tasks:" with instruction
to report findings directly. When no results found, explicitly states "Memory search
completed. No relevant past tasks found" so Sonnet can honestly say it checked and found
nothing, rather than promising to search.

## MEDIA-1: Photo/video/document support via Vision API
Previously, media sent to Telegram bot was silently ignored — no handler for F.photo/F.video/
F.document. User sending a photo with "analyze this" got routed as text → planner → web_search
→ hallucination → FAILED.

Implementation across 5 files:
- **base.py**: IncomingMessage gains `media: list[dict]` field. Each item: type, data (base64),
  media_type (MIME). Backward-compatible (default empty list).
- **telegram.py**: New `handle_media` handler for F.photo|F.video|F.document. Photos: download
  largest resolution, base64-encode. Videos: extract up to 4 frames via ffmpeg subprocess
  (graceful: if ffmpeg unavailable, honest error instead of FAILED). Image documents: treated
  as photos. Non-image documents: filename prepended to caption text.
- **gateway.py**: Passes `msg.media` to `loop.run()`.
- **loop.py**: `run()` and `_handle_conversation()` gain `media` param. Messages with media
  bypass intent classification and go directly to conversation handler. User message built as
  multimodal content blocks (image source type=base64 + text block) for Claude Vision API.
- **llm/base.py**: `Message.content` type widened from `str` to `str | list` to support
  multimodal content blocks. `_to_anthropic_messages` already passes content through unchanged.

Graceful degradation: ffmpeg unavailable → honest "cannot process video, send screenshot".
No changes to benchmark, monitoring, or existing text/voice handlers.

## FIX-29: Graceful degradation on total step failure
When all plan steps failed, the system returned success=False with a raw error message.
Users saw cryptic errors in Telegram. Now falls back to _handle_conversation with an enriched
task that includes the error context: "[Автоматическое выполнение не удалось: {error}. Отвечаю
напрямую.]" — Sonnet answers from its own knowledge instead of showing FAILED.

## FIX-30: retry_hint extended to web_search
Previously only code_executor got retry hints on failed evaluation. Now web_search also uses
eval_result.retry_hint: appended to the query string on retry, so the search refines itself
based on what went wrong (e.g., "price gold" + "current 2026" on retry).

## MEDIA-2: PDF documents from Telegram (superseded by MEDIA-3)
PDF documents sent to Telegram bot were saved to a temp file and passed to the planner
as task text with "[PDF файл сохранён по пути: /tmp/xxx.pdf]". Superseded by MEDIA-3 which
uses Vision API for richer understanding of PDF content including tables, charts, layouts.

## MEDIA-3: PDF pages to Vision API via pdf2image
PDF documents from Telegram are now converted to images page-by-page using pdf2image + poppler,
then sent as Vision API content blocks (same pipeline as photos). `_pdf_to_images()` static
method: `convert_from_bytes()` at 200 DPI, max 10 pages, JPEG quality 85. Graceful fallback
to MEDIA-2 temp-file approach if pdf2image or poppler-utils is not installed. Dependencies:
pdf2image>=1.16.0 in pyproject.toml, poppler-utils + ffmpeg in sandbox/Dockerfile.

## FIX-31: PDF fallback — honest error instead of temp-file path
Previously, if pdf2image/poppler was unavailable, the PDF handler fell back to saving a temp
file and injecting the path into task text. This silently produced broken plans on systems
without poppler. Replaced with an honest Telegram message telling the user to install
poppler-utils or send the document content as text. No temp file created, early return.

## FIX-32: pymupdf instead of pdf2image+poppler
Replaced pdf2image+poppler with pymupdf (fitz) for PDF-to-image conversion. pymupdf is a
pure-wheel Python package — no system dependencies (poppler-utils), works on Windows/Linux/Mac
out of the box. `_pdf_to_images()` uses `fitz.open(stream=bytes)`, 2x zoom matrix (~144 DPI),
`pix.tobytes("jpeg")`. Removed poppler-utils from sandbox/Dockerfile, replaced pdf2image
with pymupdf>=1.24.0 in pyproject.toml.

## Q-10.1: Universal Planner
Replaced 6 specialized planner prompts (PLAN_WRITING, PLAN_CODE, PLAN_RESEARCH,
PLAN_PRESENTATION, PLAN_MIXED, SPECIALIZED_PROMPTS dict) with a single PLAN_UNIVERSAL prompt.
The planner now chooses tools based on what the task actually needs, not by matching a category
template. The Haiku classifier (`_classify`) is kept for `task_type_hint` labeling and few-shot
store indexing in `loop.py`. Fallback chain: `_universal_plan` \u2192 `_fast_plan` \u2192 `_react_plan`.
Deleted method: `_specialized_plan`. Added: `_universal_plan`, `VALID_TASK_TYPES` set.

## Q-10.2: Writing fast path under LLM control
Before `_run_writing_task()` a Haiku gate `_needs_planner()` now checks whether a writing task
is self-contained (WRITE) or needs data gathering / multiple tools (PLAN). Tasks like
"\u043d\u0430\u043f\u0438\u0448\u0438 \u043e\u0442\u0447\u0451\u0442 \u043f\u043e \u0434\u043e\u0431\u044b\u0447\u0435 \u0437\u0430 \u043c\u0430\u0440\u0442" now route to the planner instead of the text_writer fast path.
Fallback on Haiku error: keep fast path (safe default). Cost: ~10 tokens per writing task.

## Q-10.3: MAX_PLAN_STEPS = 10
Permanent fix for FIX-17. Plan step limit raised from 7 to 10 in `CoreLoop.MAX_PLAN_STEPS`.
`_validate_plan()` already used `self.MAX_PLAN_STEPS` (no hardcoded numbers to change).

## Q-9.1: Task Decomposer
New `src/organism/core/decomposer.py`. Haiku analyzes the task: if it has multiple distinct
phases (gather data + process + write report) it breaks it into 2-5 subtasks. Each subtask
executes sequentially through `CoreLoop.run()` with context passing (last 2 results injected).
Results aggregated by Haiku into one final answer. Gate: tasks under 100 chars skip the check.
Graceful degradation: if decomposition fails, continues with normal planning.

## Q-9.9: Subtask progress in Telegram
`progress_callback` passed through `IncomingMessage.metadata` \u2192 `Gateway` \u2192
`CoreLoop.run()`. During decomposition, Telegram shows "\u0427\u0430\u0441\u0442\u044c X/Y: ..."
instead of the static ticker. The callback is fire-and-forget (try/except), so rate-limiting
or deleted messages do not crash the execution.

## Q-9.7: Docker production deployment
Production-ready containerization:
- `Dockerfile`: python:3.11-slim, system deps, pip install from pyproject.toml, HEALTHCHECK
- `docker-compose.yml`: bot + postgres (pgvector/pgvector:pg15), healthchecks, named volumes,
  DATABASE_URL injected, docker.sock mounted for sandbox
- `.env.production.example`: template with all required/optional env vars
- `scripts/deploy.sh`: git pull \u2192 docker-compose build \u2192 up -d --no-deps bot \u2192 health check

## Q-9.6: Multi-tenancy (artel_id isolation)
All DB queries in memory layer filtered by `settings.artel_id` (from ARTEL_ID env var).
Since `artel_id` column added via migration `_m002_artel_id` (not in ORM model), filtering
uses `text("artel_id = :artel_id")` with `.params()` for ORM queries and raw SQL conditions.
- `longterm.py`: save_task sets artel_id after INSERT; search_similar filters in vector,
  BM25, and fallback queries; get_stats filtered
- `solution_cache.py`: get() filters by artel_id; put() sets artel_id on INSERT;
  get_stats() uses raw SQL with artel_id filter
- `knowledge_base.py`: get_rules() filters by artel_id; add_rule() sets artel_id on INSERT

## FIX-33: Unified conversation+action mode
Removed hard TASK/CHAT classification (`_classify_intent` deleted). `_handle_conversation`
upgraded from plain `llm.complete()` to `llm.complete_with_tools()`. Tools obtained via
`self.registry.to_json_schema()` (all registered tools including MCP). Agentic loop: max 3
rounds of tool calls before forcing final text response.

Flow after FIX-33: media \u2192 `_handle_conversation` (with tools), everything else \u2192
planner path. Conversational messages that fail planning are caught by FIX-16 fallback and
routed to `_handle_conversation` (with tools) \u2014 so they get tool access too.
`_build_tool_definitions()` wraps `registry.to_json_schema()`.

Impact: eliminates hallucinated actions in conversation mode. Previously the LLM would
describe actions without executing. Now it can call tools directly via complete_with_tools.

## FIX-34: Recent Work Context in Conversation

**Problem**: User asks "\u0447\u0442\u043e \u043f\u043e \u0444\u0430\u0439\u043b\u0443 \u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u0442\u044b \u0441\u043e\u0437\u0434\u0430\u043b?" \u2014 agent responds "\u0443 \u043c\u0435\u043d\u044f \u043d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u0444\u0430\u0439\u043b\u0443".
Root cause: `_handle_conversation` injects only semantically similar past tasks (vector search).
A query like "\u0447\u0442\u043e \u043f\u043e \u0444\u0430\u0439\u043b\u0443" doesn't semantically match "\u0441\u043e\u0437\u0434\u0430\u0439 Excel \u043e\u0442\u0447\u0451\u0442" \u2192 0 results.

**Solution**: Add chronological recent tasks as a third context layer (alongside chat history
and semantic memory). `LongTermMemory.get_recent_tasks(limit=3)` returns last N completed
tasks ordered by `created_at DESC`, filtered by `artel_id`. Injected into
`_handle_conversation` system prompt as "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043d\u044b\u0435 \u0437\u0430\u0434\u0430\u0447\u0438:" section,
placed before semantic memory hits (more likely relevant for self-referential questions).

**Design**: No keyword detection, no if-chains. Always fetched (like chat_history).
Result capped at 3 tasks, preview 300 chars. Wrapped in try/except \u2014 graceful degradation.

Files changed: `memory/longterm.py`, `memory/manager.py`, `core/loop.py`.

## FIX-35: confirm_with_user Description Tightening

**Problem**: After FIX-33 gave `_handle_conversation` access to all tools, the LLM started
calling `confirm_with_user` for ordinary conversational responses (e.g. "I can't send video")
where no real action was being taken. The old description ("Ask user for approval before a
critical action") was vague enough that the LLM interpreted uncertainty as a reason to confirm.

**Solution**: Rewrote the tool description to be precise about the trigger condition:
"irreversible action on an external system". The LLM now reasons: "I'm explaining a limitation
\u2192 that's not an action on an external system \u2192 tool not needed."

No system prompt changes, no routing logic \u2014 just a clearer tool description.

File changed: `tools/confirm_user.py`.

## Q-10.4: _handle_conversation as Primary Execution Path

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
- `MAX_TOOL_ROUNDS`: 3 \u2192 7 (handles complex multi-step tasks: search+compute+save+verify)
- `on_task_end()`: saves results to memory after every response (was missing before)
- Chat history: saves user message and assistant response after every interaction

**New flow in run()**:
1. Memory init
2. Media \u2192 `_handle_conversation` (unchanged)
3. Memory search + user facts + personality + few-shot (unchanged)
4. Cache check (unchanged)
5. Decomposer check for tasks > 100 chars (unchanged)
6. Everything else \u2192 `_handle_conversation(task_id, task, user_context, memory_context, user_id)`

**Benchmark**: 5/5 quick (100%), no regression. Score 0.93 avg quality.

Files changed: `core/loop.py`.

## SKILL-1: Technical Skills System

**Problem**: Agent creates basic, unformatted files (Excel without styling, plain text Word docs).
No expert knowledge about HOW to create professional-quality documents.

**Solution**: Static skill files in `config/skills/*.md` \u2014 expert templates written once by a human.
`SkillMatcher` in `core/skill_matcher.py` selects relevant skills via Haiku (~50 tokens)
and injects content into `_handle_conversation` system prompt as `skill_context`.

**Components**:
- `config/skills/excel.md` \u2014 openpyxl formatting: dark headers, alternating rows, auto-width
- `config/skills/docx.md` \u2014 Node.js `docx` library: A4, Arial, proper margins
- `config/skills/charts.md` \u2014 matplotlib: Agg backend, clean styling, dpi=150
- `core/skill_matcher.py` \u2014 `SkillMatcher.get_skill_context(task)`: Haiku selects 0-2 skills

**Docker changes**: Node.js + npm `docx` + matplotlib added to `sandbox/Dockerfile`.

**Integration**: `skill_context` injected first in system_parts (before user_context) as
"## How to create this file" section. Graceful degradation: any failure = empty string.

Files changed: `sandbox/Dockerfile`, `config/skills/*.md`, `core/skill_matcher.py`, `core/loop.py`.

## FIX-36: File delivery from _handle_conversation

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

## FIX-37: Plain text output — no Markdown in Telegram

**Problem**: Agent formats responses with Markdown (##, ---, |tables|, **bold**).
Telegram renders it partially — looks messy with raw symbols.

**Solution**: Added formatting instruction to `_handle_conversation` system prompt:
"Never use Markdown. No ##, no ---, no |tables|, no **bold**, no ```code blocks```.
Use plain text only. Structure with line breaks and emoji if needed."
Exception for file creation (Excel, Word, PDF) where internal formatting is fine.

Files changed: `core/loop.py`.

## FIX-38: Sandbox reads previously created files

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

## FIX-39: Fix save path in sandbox after FIX-38

**Problem**: After FIX-38, agent reads from `/data/outputs/` correctly but tries to
save updated files back to `/data/outputs/` — which is read-only. File never written,
"Saved files:" never printed, file never delivered.

**Solution**: Two changes to make the path rule unmissable:
1. `code_executor.py` description: explicit PATHS section — "Read from /data/outputs/,
   ALWAYS save to /output/"
2. `config/skills/excel.md`: added "Важно: пути в sandbox" section with read/write
   path rules and an update-existing-file example

Files changed: `tools/code_executor.py`, `config/skills/excel.md`.

## FIX-41: Decomposer signature mismatch

**Problem**: `TaskDecomposer.run()` passes `user_context` to `loop.run()`, but `run()`
did not accept it as a parameter. The context built from memory (user facts, personality,
chat history, few-shot examples) was always rebuilt from scratch for each subtask instead
of reusing the one already prepared by the parent `run()` call.

**Solution**: Added `user_context: str = ""` to `CoreLoop.run()` signature. When a
non-empty `user_context` is passed (from decomposer), the memory facts fetch is skipped
(`if not user_context:`). Personality, chat history, and few-shot are still appended
since they extend rather than replace.

Files changed: `core/loop.py`.

## FIX-43: Epistemic honesty in system prompt

**Problem**: LLM sometimes fabricated explanations for failures it hadn't observed —
e.g. "file didn't attach" when it actually received and read the file. This erodes
user trust.

**Solution**: Added an "Epistemic honesty" section to `_handle_conversation` system
prompt. Instructs the LLM to only describe what it actually observed (tool results,
chat history, user context) and never invent unseen causes. Includes concrete examples
of honest vs dishonest answers.

Files changed: `core/loop.py`.

## FIX-44: Disable decomposer from main execution path

**Problem**: TaskDecomposer (Q-9.1) added an extra Haiku LLM call on every task >100
chars. In practice `_handle_conversation` with tool-use loops handles complex tasks
natively — the decomposer added latency without clear benefit and could split tasks
that the LLM handles better as a single conversation.

**Solution**: Commented out the decomposer block in `CoreLoop.run()`. Raised
`MAX_TOOL_ROUNDS` in `_handle_conversation` from 7 to 10 so the agent has enough
rounds for genuinely complex tasks. `TaskDecomposer` class and `decomposer.py` are
kept intact for future orchestrator use.

Files changed: `core/loop.py`.

## FIX-45: Universal document handling in Telegram

**Problem**: Non-image, non-PDF documents sent to the Telegram bot (e.g. `.html`,
`.json`, `.csv`, `.txt`) were handled by only prepending the filename to the task.
The agent never saw the file content.

**Solution**: Download the document into BytesIO, attempt `decode("utf-8", errors="replace")`.
If the result contains no null bytes (`\x00`), treat it as readable text and inject
the first 8000 characters into the task string. Binary files fall back to filename-only.
Everything wrapped in try/except with the old behavior as fallback.

Files changed: `channels/telegram.py`.

## FIX-47: Remove BLOCKED_DOMAINS from web_fetch

**Problem**: A hardcoded blocklist in `web_fetch.py` prevented fetching from specific
domains (g2.com, statista.com, forbes.com, etc.). This was over-protective — the agent
should see real HTTP responses and decide itself how to proceed.

**Solution**: Removed `BLOCKED_DOMAINS` constant and the pre-request check. The existing
HTTP error handling (403/404/429 → `exit_code=1` with descriptive message) gives the
agent honest feedback. Updated tool description to mention that some sites may block bots.

Files changed: `tools/web_fetch.py`.

## FIX-48: LLM-based cache time-sensitivity gate

**Problem**: A keyword heuristic (`any(w in task.lower() for w in [...])`) decided
whether to skip the solution cache. This missed nuanced cases (e.g. "latest best
practices" should cache, "current gold price" should not) and triggered false positives.

**Solution**: Replace keywords with a Haiku LLM call: "Does this task require real-time
or current data that would be wrong if cached? Reply only: yes or no." Costs ~5 tokens,
adds minimal latency. Graceful fallback: if Haiku fails or times out,
`_time_sensitive = True` (skip cache — safer than serving stale data).

Files changed: `core/loop.py`.

## FIX-49: SkillMatcher relaxed prompt

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

## FIX-50: Docker warm container pool

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

## FIX-57c: fpdf2 replaces reportlab for PDF creation

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

## FIX-58: Remove hard cutoff in memory search

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
