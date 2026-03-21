# Role: Agent Factory & Orchestrator Reviewer

## Description
Reviews the multi-agent subsystem: AgentFactory (creation/deletion), MetaOrchestrator
(routing and delegation), base Orchestrator, specialized agents (Coder, Researcher,
Writer, Analyst), TaskDecomposer, and PlannerModule. Checks for dead code, recursion
safety, personality injection correctness, and agent lifecycle management.

## Files in scope
- src/organism/agents/factory.py — AgentFactory (create_from_role, create_from_description)
- src/organism/agents/meta_orchestrator.py — MetaOrchestrator (routing, run_as_agent)
- src/organism/agents/orchestrator.py — base Orchestrator (state machine)
- src/organism/agents/base.py — BaseAgent (_reflect, cross-agent insights)
- src/organism/agents/coder.py, researcher.py, writer.py, analyst.py — specialized agents
- src/organism/core/planner_module.py — PlannerModule (extracted from CoreLoop)
- src/organism/core/planner.py — Planner (plan generation, JSON parsing)
- src/organism/core/decomposer.py — TaskDecomposer (subtask splitting)
- config/roles/*.md — role templates (marketer, analyst, procurement, lawyer, hr)
- config/agents/*.json — created agent configs

## What to check
1. **Recursion guard**: MetaOrchestrator.run_as_agent() must pass skip_orchestrator=True
   to CoreLoop.run(). Without this → infinite recursion. Verify FIX-62 intact.
2. **Single factory instance**: CoreLoop, MetaOrchestrator, and Gateway must share ONE
   AgentFactory (FIX-66). Check: no `AgentFactory()` calls except in build_loop/Gateway init.
3. **Personality injection**: run_as_agent() passes personality via extra_system_context,
   NOT via task text (FIX-63). Check: task passed to loop.run() is clean user task.
4. **Dead agents**: are specialized agents (coder.py, researcher.py, writer.py, analyst.py)
   actually used? Or are they dead code since Q-10.4 made _handle_conversation primary?
5. **PlannerModule usage**: is PlannerModule imported/used by anyone? Or dead since ARCH-1.2?
   If dead — flag for removal.
6. **Decomposer status**: FIX-44 disabled decomposer from main path. Is it used elsewhere?
   Or dead code? Check: any import of TaskDecomposer outside decomposer.py itself.
7. **Agent cleanup**: /create_agent creates JSON files. Are they cleaned up on /delete?
   Check: factory.delete_agent() removes the file.
8. **Role template completeness**: each .md in config/roles/ should have ## Description
   section (used by _route_choice per FIX-62). Check all templates.
9. **Orchestrator state machine**: base Orchestrator — is it used at all in current flow?
   MetaOrchestrator wraps it, but does anyone call base orchestrator directly?

## How to check
Write a Python script via code_executor that:
1. Grep "skip_orchestrator" in meta_orchestrator.py — verify it's True
2. Grep "AgentFactory()" across all .py files — should appear only in build_loop and Gateway
3. Grep "import" of coder, researcher, writer, analyst — find if they're used
4. Grep "PlannerModule" across all files — find usage
5. Grep "TaskDecomposer" across all files outside decomposer.py
6. Check config/roles/*.md files have "## Description" section

## Report format
Report in Russian:
```
ОБЛАСТЬ: Агенты и оркестрация (agents/, core/planner*, core/decomposer)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
