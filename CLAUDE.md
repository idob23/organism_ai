# CLAUDE.md — Organism AI Project Context

## Project Overview
Organism AI is an autonomous AI task executor for Russian gold mining cooperatives (artels).
User sends a task via Telegram or CLI → system plans → executes via tools → returns result.

**Key principle**: This is NOT a chatbot. It's an autonomous executor that plans, acts, learns.

## Architecture

```
CoreLoop → Planner → ToolRegistry → Executor → Evaluator
                                                    ↓
                                              MemoryManager (pgvector)
```

### Core Components
| Component | File | Purpose |
|-----------|------|---------|
| CoreLoop | src/organism/core/loop.py | Main execution loop, fast path for writing tasks |
| Planner | src/organism/core/planner.py | Two-phase: Haiku classifier → specialized Sonnet plan |
| Evaluator | src/organism/core/evaluator.py | Gradient quality_score (0-1), not binary |
| ToolRegistry | src/organism/tools/registry.py | Tool registration, lookup, MCP server management |
| MemoryManager | src/organism/memory/manager.py | pgvector, on_task_start / on_task_end |
| SafetyValidator | src/organism/safety/validator.py | Block dangerous operations |

### Tools (7 total)
| Tool | File | Notes |
|------|------|-------|
| code_executor | tools/code_executor.py | Docker sandbox, tmpfile + volume mount |
| web_search | tools/web_search.py | Tavily API |
| web_fetch | tools/web_fetch.py | BLOCKED: g2.com, statista.com, forbes.com, gartner.com |
| file_manager | tools/file_manager.py | Short plain text only, NOT for CSV |
| text_writer | tools/text_writer.py | Long text generation + save to file |
| pptx_creator | tools/pptx_creator.py | PowerPoint via python-pptx |
| confirm_with_user | tools/confirm_user.py | Human approval via Telegram (Q-6.3), only in Telegram mode |

### Agents (multi mode)
| Agent | File | Purpose |
|-------|------|---------|
| Orchestrator | agents/orchestrator.py | Routes tasks between agents |
| Coder | agents/coder.py | Code tasks via CoreLoop |
| Researcher | agents/researcher.py | Search via CoreLoop |
| Writer | agents/writer.py | Text via CoreLoop |
| Analyst | agents/analyst.py | Data analysis via CoreLoop |

## Tech Stack
- Python 3.11+
- LLM: Claude API (Anthropic) — Sonnet=balanced, Haiku=fast
- Memory: pgvector (PostgreSQL), text-embedding-3-small (OpenAI)
- Search: Tavily API
- Sandbox: Docker (code_executor)
- Presentations: python-pptx
- Logging: structlog
- Config: .env + pydantic-settings
- Prompts: config/prompts/*.txt
- Personality: config/personality/*.md (per-artel personality configs)

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

## CLI Commands
```
python main.py --task "..."       # Single task
python main.py --multi --task "..." # Multi-agent orchestrator
python main.py --telegram         # Telegram bot mode
python main.py --stats            # Memory statistics
python main.py --analyze          # Performance analysis
python main.py --improve --days 7 # Auto-improvement cycle
python main.py --optimize-prompts # Benchmark-driven prompt optimization
python main.py --evolve-prompts  # Evolutionary prompt search cycle
python main.py --cache            # Solution cache stats
python benchmark.py               # Full benchmark (23 tasks)
python benchmark.py --quick       # Quick check (5 tasks, no web/multi-agent)
```

### Bot/Chat Commands
```
/remember <key> <value>   — save a personal fact
/forget <key>             — delete a fact by key
/profile                  — show all saved personal facts
/history <key>            — show change history for a fact
/style <style>            — set writing style (formal/informal/technical/brief)
/stats                    — show system statistics
/improve [days]           — run auto-improvement cycle
/prompts                  — show active prompt versions and quality stats
/schedule                 — show scheduled tasks
/schedule_enable <name>   — enable a scheduled task
/schedule_disable <name>  — disable a scheduled task
/approve <id>             — approve a pending action
/reject <id>              — reject a pending action
/personality              — show current personality config
/help                     — show available commands
```

## File Structure
```
organism_ai/
├── src/organism/
│   ├── core/          # loop.py, planner.py, evaluator.py, context_budget.py
│   │                  # scheduler.py, human_approval.py, personality.py
│   ├── tools/         # registry.py, code_executor.py, web_search.py, confirm_user.py, etc.
│   ├── agents/        # base.py, orchestrator.py, coder.py, researcher.py, writer.py, analyst.py
│   ├── memory/        # manager.py, longterm.py, embeddings.py, database.py, working.py
│   │                  # solution_cache.py, knowledge_base.py, user_facts.py
│   │                  # graph.py, causal_analyzer.py, templates.py, search_policy.py
│   ├── commands/      # handler.py — /remember /forget /profile /style /stats /improve /prompts
│   │                  #   /schedule /schedule_enable /schedule_disable /approve /reject /personality
│   ├── channels/      # base.py (IncomingMessage, OutgoingMessage), gateway.py
│   │                  # telegram.py (progress ticker, file attachments), cli_channel.py
│   ├── llm/           # base.py (TemperatureLocked), claude.py
│   ├── logging/       # logger.py, error_handler.py
│   ├── safety/        # validator.py
│   └── self_improvement/ # optimizer.py, metrics.py, auto_improver.py, prompt_versioning.py
│                          # benchmark_optimizer.py, evolutionary_search.py
│   mcp_1c/            # server.py — MCP server for 1C integration (demo + live modes)
├── config/
│   ├── settings.py    # artel_id (ARTEL_ID env var)
│   ├── personality/   # default.md (per-artel personality configs)
│   └── prompts/       # planner_fast.txt, planner_react.txt, evaluator.txt
│                      # causal_analyzer.txt, template_extractor.txt
├── data/              # logs/, outputs/, sandbox/
├── main.py            # CLI entry: --task, --multi, --stats, --improve, --days
├── benchmark.py       # 23-task benchmark suite (10 baseline + 4 Sprint 5 + 5 Sprint 6 + 4 Sprint 7)
├── CONTEXT.md         # Brief context for VS Code plugin (auto-generated)
├── organism_architecture_principles.md  # Canonical architecture principles
└── pyproject.toml
```

## Coding Conventions
- All files: UTF-8 encoding
- Russian strings in code: use unicode escapes, not Cyrillic literals
- Async everywhere: all IO operations are async/await
- LLM tiers: "fast" = Haiku, "balanced" = Sonnet, "powerful" = Opus
- Error handling: graceful degradation, never crash on LLM/API failures
- Memory: always try/except around memory operations
- Imports: absolute from src.organism.*
- git commits: prefix with task ID (e.g., "Q-1.1: Evaluator 2.0")

## Current Metrics (March 2026)
- Benchmark: 23 tasks total (20/23 success without Docker/DB, ~87%)
- With Docker+DB: expected 19/19 (100%) — failures are environmental only
- Average Quality Score: 0.85
- Cache hit rate: 36% (5/14 on warm DB, 0% without DB)
- All 6 sprints complete (Q-1.1 through Q-6.5)
- Sprint 7 (Self-Improvement 2.0) — NEXT

## Development Roadmap — Quality Plan ✅ COMPLETE

### Sprint 6 ✅ (Orchestration Upgrade) — COMPLETE
- Q-6.1: State machine — graph-based orchestrator workflow (INIT→ROUTING→EXECUTING→EVALUATING→DONE) ✅
- Q-6.2: Proactive scheduler — ScheduledJob (daily/weekly/interval), DEFAULT_ARTEL_JOBS, /schedule commands ✅
- Q-6.3: Human-in-the-loop — HumanApproval + ConfirmUserTool, asyncio.Event + 300s timeout, /approve /reject ✅
- Q-6.4: Configurable personality — PersonalityConfig from config/personality/{artel_id}.md, sections + terms ✅
- Q-6.5: Gateway abstraction — IncomingMessage/OutgoingMessage, Gateway router, CLIChannel, TelegramChannel refactor ✅

### Sprint 7 (Self-Improvement 2.0) — NEXT
- Q-7.1: Structured reflections — upgrade from {score, insight} to {failure_type, root_cause, corrective_action, confidence} \u2705
- Q-7.2: Benchmark-driven prompt optimization — auto-pipeline: generate variants -> run benchmark.py -> select winner -> deploy via PVC \u2705
- Q-7.3: Few-shot example curation — store successful task-result pairs as demonstrations, top-3 injected into planner prompts \u2705
- Q-7.4: Evolutionary prompt search — population of 3-5 variants per component, weekly evaluate-mutate-select cycle ✅
- Q-7.5: Cross-agent knowledge sharing — reflection insights from one agent automatically inform planning of others ✅

### Sprint 8 (Integration — MCP + 1C)
- Q-8.1: MCP client in ToolRegistry — discover and invoke tools from external MCP servers ✅
- Q-8.2: MCP server for 1C — read operations: search counterparties, fuel data, equipment registry. Read-only first ✅
- Q-8.3: Duplicate search service — semantic search across 1C entities via MCP. Key artel use case
- Q-8.4: Organism AI as MCP server — expose task execution capabilities for other AI systems
- Q-8.5: Agent-to-Agent protocol — prepare architecture for multi-system collaboration

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

### Sprint 5 ✅ (Memory Enhancement — Graph + Temporal) — COMPLETE
- Q-5.1: Temporal fact tracking — valid_from/valid_until in user_profile and knowledge_rules ✅
- Q-5.2: Memory edges table — memory_edges with temporal|causal|entity|procedural edges ✅
- Q-5.3: Causal inference — async background worker analyzes task relationships via Haiku ✅
- Q-5.4: Procedural templates — extract and reuse successful tool+code patterns ✅
- Q-5.5: Adaptive search policy — intent classification and weighted multi-source memory search ✅

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