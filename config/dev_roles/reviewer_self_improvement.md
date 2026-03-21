# Role: Self-Improvement Pipeline Reviewer

## Description
Reviews the self-improvement subsystem: auto-improvement cycle, prompt versioning,
benchmark optimization, evolutionary search, and metrics. Focus on loop integrity.

## Context files
- src/organism/self_improvement/optimizer.py — PromptOptimizer
- src/organism/self_improvement/auto_improver.py — AutoImprover cycle
- src/organism/self_improvement/prompt_versioning.py — PromptVersionControl (PVC)
- src/organism/self_improvement/benchmark_optimizer.py — BenchmarkPromptOptimizer
- src/organism/self_improvement/evolutionary_search.py — evolutionary prompt search
- src/organism/self_improvement/metrics.py — metrics collection
- config/prompts/*.txt — optimizable prompt files

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Artel isolation in raw SQL
**What**: Every raw SQL query in self_improvement/*.py to tables with artel_id filters by artel_id.
**How to verify**: `python scripts/code_health.py` — check_artel_id_coverage() result.
**Violation = problem**: Metrics/improvements based on other tenant's data.

### INV-2: No dead modules
**What**: Every .py in self_improvement/ (except __init__.py) is imported from at least
one file outside self_improvement/.
**How to verify**: For each file — `grep -rn "from.*self_improvement.*import" /repo/ --include="*.py"`
excluding self_improvement/ directory itself. Each module must have at least one external import.
**Violation = problem**: Dead code, maintenance burden.

### INV-3: Prompt files referenced
**What**: Every .txt file in config/prompts/ is used by at least one .py file.
**How to verify**: List all config/prompts/*.txt filenames. For each, grep the filename
(without path) across /repo/src/ --include="*.py". Unreferenced = orphan.
**Violation = problem**: Orphan prompt file, wasted maintenance.

## Contextual checks (within scope)
- PVC integration: evaluator.py uses get_active() to load prompt version, not hardcoded file.
- Auto-improver cycle: analyze_failures → generate_rules → pending_insights → approval → KB.
  All links connected, no broken step.
- Evolutionary search: PromptPopulationMember table used, evolve() method callable.
- Benchmark optimizer: no circular import risk (benchmark.py ↔ src/).
- INSIGHT-1 loop: pending insights accumulate confirmations, sent for approval at 3+.
- Metrics collection: MetricsAnalyzer used from commands handler (/stats).

## How to verify
Script should:
1. Run `python scripts/code_health.py` — use result for INV-1
2. Execute INV-2: for each .py in self_improvement/, grep external imports
3. Execute INV-3: list config/prompts/*.txt, grep for each in src/
4. Contextual: trace PVC usage in evaluator, auto-improver cycle links, metrics usage

## Report format
Report in Russian:
```
OBLAST: Self-improvement (self_improvement/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Artel isolation — details
  INV-2 [PASS/FAIL]: No dead modules — details
  INV-3 [PASS/FAIL]: Prompt files referenced — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
