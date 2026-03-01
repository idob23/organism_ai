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

## File Structure
```
organism_ai/
├── src/organism/
│   ├── core/          # loop.py, planner.py, evaluator.py
│   ├── tools/         # registry.py, code_executor.py, web_search.py, etc.
│   ├── agents/        # base.py, orchestrator.py, coder.py, researcher.py, writer.py, analyst.py
│   ├── memory/        # manager.py, longterm.py, embeddings.py, database.py, working.py
│   ├── llm/           # base.py, claude.py
│   ├── logging/       # logger.py, error_handler.py
│   ├── safety/        # validator.py
│   └── self_improvement/ # optimizer.py, metrics.py
├── config/
│   ├── settings.py
│   └── prompts/       # planner_fast.txt, planner_react.txt, evaluator.txt
├── data/              # logs/, outputs/, sandbox/
├── main.py            # CLI entry: --task, --multi, --stats
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
- Success Rate: ~93%+ (was 90.6%)
- Average Quality Score: ~0.80+
- Stages 1-5 complete, Stage 6 (commercialization) in progress
- Sprint 1 of Quality Plan complete (Q-1.1 through Q-1.5)

## Development Roadmap — Quality Plan
### Sprint 1 ✅ (Foundation) — COMPLETE
- Q-1.1: Evaluator 2.0 — gradient quality_score ✅
- Q-1.2: Two-phase Planner — Haiku classifier + specialized prompts ✅
- Q-1.3: Smart fast path — search keywords bypass writing shortcut ✅
- Q-1.4: Plan Validation Layer ✅
- Q-1.5: Enriched embeddings ✅

### Sprint 2 (Multi-level RAG) — NEXT
- Q-2.1: L1 Solution Cache — hash + task normalization
- Q-2.2: Hybrid Search (vector + BM25 ts_vector in PostgreSQL)
- Q-2.3: Metadata filtering + Adaptive K
- Q-2.4: LLM Reranking (Haiku) for top-10 → top-3
- Q-2.5: L3 Knowledge Base — rules table + extraction mechanism

### Sprint 3 (Smart Agents)
- Q-3.1: Agent specialization (different strategies, temperature)
- Q-3.2: Writer 3-phase (outline → draft → polish)
- Q-3.3: Inter-agent context summarization (Haiku)
- Q-3.4: Agent Self-Reflection + save to memory
- Q-3.5: Context Window Budget

### Sprint 4 (Personalization & Automation)
- Q-4.1: User Facts Extraction
- Q-4.2: Personal context in system prompt
- Q-4.3: Commands /remember, /forget, /profile, /style
- Q-4.4: Automatic improvement cycle
- Q-4.5: Prompt Version Control + auto-rollback

## Strategic Vision
Organism AI is the foundation for a one-person + AI-agents unicorn company.
All architectural decisions should consider scaling to an autonomous AI team
that could eventually replace entire departments while maintaining one human as architect.