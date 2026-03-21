# Role: Memory & Data Layer Reviewer

## Description
Reviews the memory subsystem: storage, caching, graph, embeddings, search, migrations,
and database layer. Focus on data isolation, session safety, and migration integrity.

## Context files
- src/organism/memory/manager.py — MemoryManager facade
- src/organism/memory/longterm.py — task storage, vector search, BM25
- src/organism/memory/database.py — ORM models, migrations, init_db
- src/organism/memory/embeddings.py — embedding client
- src/organism/memory/solution_cache.py — LRU cache
- src/organism/memory/knowledge_base.py — rules storage
- src/organism/memory/few_shot_store.py — few-shot examples
- src/organism/memory/chat_history.py — conversation persistence
- src/organism/memory/graph.py — MemoryGraph, edges
- src/organism/memory/causal_analyzer.py — causal inference
- src/organism/memory/templates.py — procedural templates
- src/organism/memory/search_policy.py — intent classification
- src/organism/memory/working.py — in-process working memory
- src/organism/memory/user_facts.py — user fact extraction

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Artel isolation
**What**: Every file querying tables with artel_id column must reference artel_id.
**How to verify**: `python scripts/code_health.py` — check_artel_id_coverage() result.
Tables: task_memories, solution_cache, agent_reflections, user_profile, knowledge_rules,
procedural_templates, chat_messages, few_shot_examples, memory_edges.
**Violation = problem**: Data leak between tenants.

### INV-2: ORM-migration column sync
**What**: Every column added via ALTER TABLE in _MIGRATIONS must exist in the ORM model.
**How to verify**: Parse ORM classes in database.py (Column definitions), parse ALTER TABLE
ADD COLUMN in _MIGRATIONS. Compare sets per table. Missing column in ORM = fresh DB works
but migrated DB has extra columns that ORM ignores (or vice versa).
**Violation = problem**: Fresh install vs migrated install divergence (FIX-91 lesson).

### INV-3: Session management safety
**What**: Every `AsyncSessionLocal()` call must be inside `async with`.
**How to verify**: `grep -rn "AsyncSessionLocal()" /repo/src/ --include="*.py"` — every
result line must contain `async with`.
**Violation = problem**: Leaked DB sessions, connection pool exhaustion.

### INV-4: Single save point for chat history
**What**: `save_message()` is called ONLY from gateway.py (FIX-65).
**How to verify**: `grep -rn "save_message" /repo/src/ --include="*.py"` — results only
in chat_history.py (definition) and gateway.py (calls).
**Violation = problem**: Duplicate message storage, inconsistent history.

## Contextual checks (within scope)
- Embedding fallback: does search_similar() work when get_embedding() returns None/[]?
- Cache invalidation: solution_cache respects time-sensitive queries (FIX-48 bypass).
- Graph integrity: temporal edges between consecutive tasks, causal/entity edges non-blocking.
- Migration ordering: _MIGRATIONS sequential with no gaps (code_health.py verifies).
- Memory save completeness: on_task_end() saves to all stores (longterm, graph, templates, few-shot).
- Dead code: functions in memory/ defined but never called from outside their module.

## How to verify
Script should:
1. Run `python scripts/code_health.py` — use results for INV-1, migration order
2. Execute INV-2: parse database.py ORM classes + _MIGRATIONS ALTER TABLEs, compare column sets
3. Execute INV-3: `grep -rn "AsyncSessionLocal()" /repo/src/ --include="*.py"` — verify async with
4. Execute INV-4: `grep -rn "save_message" /repo/src/ --include="*.py"` — verify single save point
5. Contextual: read scope files, analyze embedding fallback paths, cache behavior, graph integrity

## Report format
Report in Russian:
```
OBLAST: Memory and data (memory/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Artel isolation — details
  INV-2 [PASS/FAIL]: ORM-migration sync — details
  INV-3 [PASS/FAIL]: Session management — details
  INV-4 [PASS/FAIL]: Single save point — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation
2. ...

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
