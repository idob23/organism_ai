# CLAUDE.md — Organism AI Project Context

> Detailed architecture decisions, sprint history, and business context: see ARCHITECTURE_DECISIONS.md

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

### Tools (8 built-in + MCP dynamic + A2A conditional)
| Tool | File | Notes |
|------|------|-------|
| code_executor | tools/code_executor.py | Docker sandbox, tmpfile + volume mount |
| web_search | tools/web_search.py | Tavily API |
| web_fetch | tools/web_fetch.py | BLOCKED: g2.com, statista.com, forbes.com, gartner.com |
| file_manager | tools/file_manager.py | Short plain text only, NOT for CSV |
| text_writer | tools/text_writer.py | Long text generation + save to file |
| pptx_creator | tools/pptx_creator.py | PowerPoint via python-pptx |
| confirm_with_user | tools/confirm_user.py | Human approval via Telegram (Q-6.3), only in Telegram mode |
| pdf_tool | tools/pdf_tool.py | Create/read PDF files via reportlab/pypdf2 (TOOL-1) |
| duplicate_finder | tools/duplicate_finder.py | Semantic duplicate search in 1C entities via embeddings (Q-8.3) |
| mcp_* | tools/mcp_client.py | Dynamic tools from MCP servers (MCP_SERVERS env) |
| delegate_to_agent | a2a/protocol.py | Peer delegation (A2A_PEERS env), only when peers configured |

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
- Memory: pgvector (PostgreSQL), text-embedding-3-small (OpenAI/proxy), BM25 fallback when unavailable
- Search: Tavily API
- Sandbox: Docker (code_executor)
- Presentations: python-pptx
- MCP/HTTP: aiohttp
- Math: numpy (cosine similarity for duplicate_finder)
- Logging: structlog
- Config: .env + pydantic-settings
- Prompts: config/prompts/*.txt
- Personality: config/personality/*.md (per-artel personality configs)

## Coding Conventions
- All files: UTF-8 encoding
- Russian strings in code: use unicode escapes, not Cyrillic literals
- Async everywhere: all IO operations are async/await
- LLM tiers: "fast" = Haiku, "balanced" = Sonnet, "powerful" = Opus
- Error handling: graceful degradation, never crash on LLM/API failures
- Memory: always try/except around memory operations
- Imports: absolute from src.organism.*
- git commits: prefix with task ID (e.g., "Q-1.1: Evaluator 2.0")

## File Structure
```
organism_ai/
├── src/organism/
│   ├── core/          # loop.py, planner.py, evaluator.py, context_budget.py, decomposer.py
│   │                  # scheduler.py, human_approval.py, personality.py
│   ├── tools/         # registry.py, code_executor.py, web_search.py, confirm_user.py
│   │                  # web_fetch.py, file_manager.py, text_writer.py, pptx_creator.py
│   │                  # duplicate_finder.py, pdf_tool.py, mcp_client.py
│   ├── agents/        # base.py, orchestrator.py, coder.py, researcher.py, writer.py, analyst.py
│   ├── memory/        # manager.py, longterm.py, embeddings.py, database.py, working.py
│   │                  # solution_cache.py, knowledge_base.py, user_facts.py
│   │                  # graph.py, causal_analyzer.py, templates.py, search_policy.py
│   │                  # few_shot_store.py
│   ├── commands/      # handler.py — all /commands (15 total)
│   ├── channels/      # base.py, gateway.py, telegram.py, cli_channel.py
│   ├── llm/           # base.py (TemperatureLocked), claude.py
│   ├── logging/       # logger.py, error_handler.py
│   ├── monitoring/    # error_notifier.py — ErrorNotifier background task, capture_error()
│   ├── safety/        # validator.py
│   ├── self_improvement/ # optimizer.py, metrics.py, auto_improver.py, prompt_versioning.py
│   │                     # benchmark_optimizer.py, evolutionary_search.py
│   ├── mcp_1c/        # server.py — MCP server for 1C integration (demo + live modes)
│   ├── mcp_serve/     # server.py — Organism AI as MCP server (Q-8.4)
│   └── a2a/           # protocol.py — Agent-to-Agent delegation (Q-8.5)
├── config/
│   ├── settings.py    # artel_id (ARTEL_ID env var)
│   ├── personality/   # default.md (per-artel personality configs)
│   └── prompts/       # planner_fast.txt, planner_react.txt, evaluator.txt
│                      # causal_analyzer.txt, template_extractor.txt
├── data/              # logs/, outputs/, sandbox/
├── main.py            # CLI entry point
├── benchmark.py       # 26-task benchmark suite
├── ARCHITECTURE_DECISIONS.md  # Detailed architecture reference
└── pyproject.toml
```

## CLI Commands
```
python main.py --task "..."        # Single task
python main.py --multi --task "..."  # Multi-agent orchestrator
python main.py --telegram          # Telegram bot mode
python main.py --interactive       # Interactive CLI mode
python main.py --stats             # Memory statistics
python main.py --analyze           # Performance analysis
python main.py --improve --days 7  # Auto-improvement cycle
python main.py --cache             # Solution cache stats
python main.py --optimize-prompts  # Benchmark-driven prompt optimization
python main.py --evolve-prompts    # Evolutionary prompt search cycle
python main.py --serve-mcp         # Start as MCP server (port 8091)
python benchmark.py                # Full benchmark (26 tasks)
python benchmark.py --quick        # Quick check (5 tasks, no web/multi-agent)
```
___
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
/reset                    — reset all saved profile data
/cleanup                  — run database cleanup (expired cache, old reflections, old errors)
/test_error               — send a test error to monitoring
/help                     — show available commands
```

## Current Metrics (March 2026)
- Benchmark: 26 tasks total (23/26 success without Docker/DB, 100% with Docker+DB)
- Average Quality Score: 0.85
- All 8 sprints complete (Q-1.1 through Q-8.5), DB-1 schema revision done
- Fixes: FIX-1 through FIX-30 ✅, HIST-1 ✅, TOOL-1 ✅, MEDIA-1 ✅, MEDIA-2 ✅, MEDIA-3 ✅
- Sprint 9 (Universal Planner + Agent Factory) — IN PROGRESS
  - Завершено: Q-9.0 ✅ (LLM intent classifier), Q-9.1 ✅ (task decomposer), Q-9.6 ✅ (multi-tenancy artel_id), Q-9.7 ✅ (Docker production), Q-9.9 ✅ (Telegram subtask progress), Q-10.1 ✅ (универсальный планировщик), Q-10.2 ✅ (writing gate), Q-10.3 ✅ (MAX_PLAN_STEPS=10), FIX-33 ✅ (unified conversation+action), FIX-34 ✅ (recent work context in conversation), MEDIA-1 ✅, MEDIA-2 ✅, MEDIA-3 ✅, FIX-29 ✅, FIX-30 ✅
  - Следующий: Q-10.4 (Agent Factory)

## Critical Rules for Claude Code
- **Before EVERY commit**: run `python pre_commit_check.py` — if it fails, fix errors first, NEVER commit broken code
- **After ANY change to loop.py, planner.py, evaluator.py, gateway.py**: run `python benchmark.py --quick` and confirm score ≥ previous
- **Commit only if**: pre_commit_check.py exits with code 0
- **Russian strings**: ALWAYS use unicode escapes (\u043d\u0430\u043f\u0438\u0448\u0438), NEVER Cyrillic literals in .py files
- **Memory operations**: ALWAYS wrap in try/except — one DB failure must not crash the system
- **New tools**: register in BOTH main.py AND benchmark.py `build_registry()`
- **New commands**: add to HELP_TEXT in handler.py AND Bot/Chat Commands in this file
- **Migrations**: APPEND to `_MIGRATIONS` list in database.py, NEVER reorder or remove entries
- **After sprint/task**: update this file + ARCHITECTURE_DECISIONS.md + git commit with task prefix
