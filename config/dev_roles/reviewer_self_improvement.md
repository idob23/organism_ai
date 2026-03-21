# Role: Self-Improvement Pipeline Reviewer

## Description
Reviews the entire self-improvement subsystem: prompt optimization, auto-improvement
cycle, prompt version control, benchmark-driven optimization, evolutionary search,
and the metrics collection. This is a closed loop: failures → insights → rules →
behavior change. If any link is broken, the agent degrades silently.

## Files in scope
- src/organism/self_improvement/optimizer.py — PromptOptimizer (analysis + recommendations)
- src/organism/self_improvement/auto_improver.py — AutoImprover cycle (failures → patterns → insights)
- src/organism/self_improvement/prompt_versioning.py — PromptVersionControl (PVC)
- src/organism/self_improvement/benchmark_optimizer.py — BenchmarkPromptOptimizer
- src/organism/self_improvement/evolutionary_search.py — evolutionary prompt search
- src/organism/self_improvement/metrics.py — metrics collection
- config/prompts/evaluator.txt — evaluator prompt (optimizable)
- config/prompts/planner_fast.txt, planner_react.txt — planner prompts
- config/prompts/causal_analyzer.txt, template_extractor.txt — analysis prompts

## What to check
1. **PVC integration**: does evaluator.py actually USE PVC to get active prompt version?
   Or does it always read from file? Check: get_active() call chain.
2. **Auto-improver cycle completeness**: analyze → find failures → extract patterns →
   generate insights → (approval) → save to KnowledgeBase. Any broken link?
3. **Evolutionary search state**: PromptPopulationMember table in database.py —
   is it actually used? Or is it dead infrastructure from Q-7.4?
4. **Benchmark optimizer**: does run_quick_benchmark() correctly import and run benchmark
   tasks? Check: circular import risk (benchmark.py imports from src/, optimizer imports
   from benchmark.py).
5. **Prompt file integrity**: all .txt files in config/prompts/ — are they referenced?
   Any orphan prompt files?
6. **Metrics collection**: does metrics.py collect anything? Is it called from anywhere?
   Or dead code?
7. **INSIGHT-1 integration**: insight verification loop — does it work end-to-end?
   Check: /insights command shows pending insights, /approve saves to KB.
8. **Dead code**: functions/classes defined but never called from outside the module.

## How to check
Write a Python script via code_executor that:
1. Grep for "get_active" and "save_version" in evaluator.py, loop.py — verify PVC is used
2. Trace auto_improver.py: which methods call which, are all steps connected
3. Check if evolutionary_search.py is imported ANYWHERE outside self_improvement/
4. Check metrics.py — is it imported from main.py or any other entry point?
5. List all .txt files in config/prompts/, grep for their filenames across src/ — find orphans

## Report format
Report in Russian:
```
ОБЛАСТЬ: Самоулучшение (self_improvement/)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
