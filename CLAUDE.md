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
| ToolRegistry | src/organism/tools/registry.py | Tool registration and lookup |
| MemoryManager | src/organism/memory/manager.py | pgvector, on_task_start / on_task_end |
| SafetyValidator | src/organism/safety/validator.py | Block dangerous operations |

### Tools (6 total)
| Tool | File | Notes |
|------|------|-------|
| code_executor | tools/code_executor.py | Docker sandbox, tmpfile + volume mount |
| web_search | tools/web_search.py | Tavily API |
| web_fetch | tools/web_fetch.py | BLOCKED: g2.com, statista.com, forbes.com, gartner.com |
| file_manager | tools/file_manager.py | Short plain text only, NOT for CSV |
| text_writer | tools/text_writer.py | Long text generation + save to file |
| pptx_creator | tools/pptx_creator.py | PowerPoint via python-pptx |

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

## File Structure
```
organism_ai/
├── src/organism/
│   ├── core/          # loop.py, planner.py, evaluator.py, context_budget.py
│   ├── tools/         # registry.py, code_executor.py, web_search.py, etc.
│   ├── agents/        # base.py, orchestrator.py, coder.py, researcher.py, writer.py, analyst.py
│   ├── memory/        # manager.py, longterm.py, embeddings.py, database.py, working.py
│   │                  # solution_cache.py, knowledge_base.py, user_facts.py
│   │                  # graph.py, causal_analyzer.py, templates.py, search_policy.py
│   ├── commands/      # handler.py — /remember /forget /profile /style /stats /improve /prompts
│   ├── channels/      # telegram.py, base.py
│   ├── llm/           # base.py (TemperatureLocked), claude.py
│   ├── logging/       # logger.py, error_handler.py
│   ├── safety/        # validator.py
│   └── self_improvement/ # optimizer.py, metrics.py, auto_improver.py, prompt_versioning.py
├── config/
│   ├── settings.py
│   └── prompts/       # planner_fast.txt, planner_react.txt, evaluator.txt
├── data/              # logs/, outputs/, sandbox/
├── main.py            # CLI entry: --task, --multi, --stats, --improve, --days
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
- Benchmark: 14/14 tasks, 100% success rate (was 90.6% before Quality Plan)
- Average Quality Score: 0.78
- Cache hit rate: 36% (5/14 on full benchmark)
- All 5 Quality Plan sprints complete (Q-1.1 through Q-5.5)
- Sprint 6 (Orchestration Upgrade) in progress

## Development Roadmap — Quality Plan ✅ COMPLETE

### Sprint 6 (Orchestration Upgrade) — NEXT
- Q-6.1: State machine — replace sequential loop with graph-based control, conditional edges, parallel execution
- Q-6.2: Proactive scheduler — cron-triggered tasks, configurable per-artel schedules for reports, alerts, monitoring
- Q-6.3: Human-in-the-loop — confirm_with_user sends to Telegram, waits for approval before critical actions
- Q-6.4: Configurable personality — PERSONALITY.md per artel: communication style, terminology, escalation rules
- Q-6.5: Gateway abstraction — channel-agnostic gateway for Telegram, CLI, future web UI via single interface

### Sprint 7 (Self-Improvement 2.0)
- Q-7.1: Structured reflections — upgrade from {score, insight} to {failure_type, root_cause, corrective_action, confidence}
- Q-7.2: Benchmark-driven prompt optimization — auto-pipeline: generate variants -> run benchmark.py -> select winner -> deploy via PVC
- Q-7.3: Few-shot example curation — store successful task-result pairs as demonstrations, top-3 injected into planner prompts
- Q-7.4: Evolutionary prompt search — population of 3-5 variants per component, weekly evaluate-mutate-select cycle
- Q-7.5: Cross-agent knowledge sharing — reflection insights from one agent automatically inform planning of others

### Sprint 8 (Integration — MCP + 1C)
- Q-8.1: MCP client in ToolRegistry — discover and invoke tools from external MCP servers
- Q-8.2: MCP server for 1C — read operations: search counterparties, fuel data, equipment registry. Read-only first
- Q-8.3: Duplicate search service — semantic search across 1C entities via MCP. Key artel use case
- Q-8.4: Organism AI as MCP server — expose task execution capabilities for other AI systems
- Q-8.5: Agent-to-Agent protocol — prepare architecture for multi-system collaboration

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