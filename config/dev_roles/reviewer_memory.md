# Role: Memory & Data Layer Reviewer

## Description
Reviews the entire memory subsystem: long-term storage, solution cache, few-shot store,
chat history, memory graph, causal analyzer, embeddings, search policy, knowledge base,
user facts, and the database layer (ORM models, migrations, connection management).

## Files in scope
- src/organism/memory/manager.py — MemoryManager facade
- src/organism/memory/longterm.py — task storage, vector search, BM25, hybrid scoring
- src/organism/memory/database.py — ORM models, migrations, init_db, AsyncSessionLocal
- src/organism/memory/embeddings.py — OpenAI embedding client, singleton pattern
- src/organism/memory/solution_cache.py — LRU cache with quality threshold
- src/organism/memory/knowledge_base.py — rules storage
- src/organism/memory/few_shot_store.py — few-shot example curation
- src/organism/memory/chat_history.py — conversation persistence
- src/organism/memory/graph.py — MemoryGraph, edges (temporal, causal, entity)
- src/organism/memory/causal_analyzer.py — causal inference from tasks
- src/organism/memory/templates.py — procedural template extraction
- src/organism/memory/search_policy.py — intent classification for search strategy
- src/organism/memory/working.py — in-process working memory
- src/organism/memory/user_facts.py — user fact extraction and storage

## What to check
1. **Artel isolation**: every DB query must filter by artel_id. Check ALL select/insert
   statements in longterm, cache, knowledge_base, few_shot — any missing filter = data leak.
2. **ORM vs migrations sync**: columns in ORM models (database.py) must match what
   migrations create. Missing columns = fresh DB breaks.
3. **Migration ordering**: _MIGRATIONS list must be sequential (m001..m015+), no gaps.
4. **Connection management**: AsyncSessionLocal usage — every session in async with,
   no leaked sessions. Check for sessions opened but not closed on exception paths.
5. **Embedding failures**: get_embedding() must handle timeout/error gracefully.
   Check: does search_similar() work when embedding returns None? (BM25 fallback)
6. **Memory save consistency**: on_task_end() must save to ALL relevant stores
   (longterm, graph, templates, few-shot). Check nothing is silently skipped.
7. **Duplicate data paths**: chat_history saved in ONLY ONE place (Gateway, not loop).
   Verify FIX-65 is still intact — no new save points added.
8. **Graph integrity**: temporal edges created between consecutive tasks. Causal/entity
   edges created by background tasks. Check they don't block the main execution.
9. **Cache invalidation**: solution_cache — does it respect time-sensitive queries?
   Check FIX-48 time-sensitivity bypass is working.
10. **Dead code**: any functions in memory/ that are defined but never called from
    outside their module.

## How to check
Write a Python script via code_executor that:
1. Reads all .py files in /repo/src/organism/memory/
2. For artel isolation: grep for SELECT/INSERT/UPDATE/DELETE SQL and check each has artel_id
3. For ORM sync: parse database.py ORM classes, extract column names; parse _MIGRATIONS,
   extract ALTER TABLE ADD COLUMN — compare sets
4. For dead code: build a call graph — find functions defined in memory/ but not referenced
   from outside memory/ (grep imports from other modules)
5. For duplicate save: grep "save_message" across all files — must appear ONLY in gateway.py
   (exception: /assign in gateway.py per FIX-71)

## Report format
Report in Russian:
```
ОБЛАСТЬ: Память и данные (memory/)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
