# CLAUDE.md — Organism AI

> Ядро контекста для Claude Code. Детали: CONVENTIONS.md, ARCHITECTURE_DECISIONS.md

## Что это
Organism AI — универсальная платформа автономных AI-агентов. Стратегическая цель:
фундамент для компании "1 человек + команда AI-агентов" (unicorn of one).
Telegram/CLI → планирование → выполнение через tools → оценка → память → ответ.
НЕ чат-бот. Думающий исполнитель, который планирует, действует, учится.

## Философия: думающий агент
Агент должен рассуждать как Claude, а не следовать спискам правил.
Красный флаг: "давай добавим ещё одно правило в промпт/логику."
Когда агент ведёт себя неправильно — ищи технический корень (обрезанные данные,
неправильный лимит, отсутствующий паттерн), а не добавляй правило поверх симптома.
Все механизмы (память, pgvector, self-improvement, few-shot, personality,
solution cache, human-in-the-loop) — неприкосновенны.

## Архитектура
```
User → Gateway → CoreLoop.run()
                      ↓
                 _classify_complex (Haiku, 5 токенов)
                      ↓                        ↓
                 простая задача          сложная задача
                      ↓                        ↓
         _handle_conversation         Orchestrator/MetaOrchestrator
          (основной путь)              (multi-agent, PlannerModule)
                      ↓
         LLM (Sonnet) + ToolRegistry → Tool.execute() → ToolResult
                      ↓                                      ↓
         Evaluator (quality 0-1)                      created_files[]
                      ↓
         MemoryManager (pgvector, on_task_start/end)
```
Haiku = классификация/роутинг. Sonnet = исполнение. MAX_TOOL_ROUNDS = 10.

## Стек
Python 3.11+ | Claude API (Sonnet/Haiku) | PostgreSQL + pgvector | aiogram 3.x
Docker (sandbox) | aiohttp (MCP) | Tavily (search) | numpy (duplicate_finder)
python-pptx | fpdf2 | structlog | pydantic-settings | proxyapi.ru (embeddings)

## Структура файлов
```
organism_ai/
├── src/organism/
│   ├── core/           # loop.py, planner.py, planner_module.py, evaluator.py,
│   │                   # decomposer.py, scheduler.py, personality.py,
│   │                   # skill_matcher.py, human_approval.py
│   ├── tools/          # registry.py + 13 tools:
│   │                   #   always: code_executor, pptx_creator, text_writer, web_fetch,
│   │                   #           file_manager, duplicate_finder, pdf_tool, memory_search,
│   │                   #           manage_agents, manage_schedule
│   │                   #   conditional: web_search (tavily), telegram_sender (telegram token)
│   │                   #   telegram-only: confirm_user (human approval)
│   │                   # + mcp_client.py (dynamic MCP tools)
│   ├── agents/         # factory.py, meta_orchestrator.py, orchestrator.py
│   │                   # base.py, coder.py, researcher.py, writer.py, analyst.py
│   ├── memory/         # manager.py, longterm.py, database.py, embeddings.py, working.py,
│   │                   # solution_cache.py, knowledge_base.py, few_shot_store.py,
│   │                   # user_facts.py, graph.py, causal_analyzer.py, templates.py,
│   │                   # search_policy.py
│   ├── commands/       # handler.py (23 команды)
│   ├── channels/       # base.py, gateway.py, telegram.py, cli_channel.py, bot_sender.py
│   ├── llm/            # base.py (TemperatureLocked), claude.py
│   ├── logging/        # logger.py, error_handler.py
│   ├── safety/         # validator.py (SafetyValidator)
│   ├── utils/          # timezone.py (now_local, to_local, today_local)
│   ├── self_improvement/ # optimizer.py, metrics.py, auto_improver.py,
│   │                     # prompt_versioning.py, benchmark_optimizer.py,
│   │                     # evolutionary_search.py
│   ├── mcp_1c/         # server.py (1С MCP, demo/live)
│   ├── mcp_serve/      # server.py (Organism как MCP-сервер)
│   ├── a2a/            # protocol.py (Agent-to-Agent delegation)
│   └── monitoring/     # error_notifier.py
├── config/
│   ├── settings.py     # ARTEL_ID, TIMEZONE, все env vars
│   ├── skills/         # excel.md, docx.md, charts.md, pdf.md
│   ├── roles/          # marketer.md, analyst.md, procurement.md, lawyer.md, hr.md
│   ├── agents/         # {agent_id}.json (created agents)
│   ├── jobs/           # {artel_id}.json (scheduled jobs config, FIX-89)
│   ├── personality/    # default.md, artel_zoloto.md, ai_media.md
│   ├── prompts/        # planner_fast.txt, planner_react.txt, evaluator.txt
│   │                   # causal_analyzer.txt, template_extractor.txt
│   └── fonts/          # DejaVuSans*.ttf (PDF)
├── scripts/            # health_check.py, deploy.sh, backup.sh, restore.sh
├── benchmark.py        # 30 задач
├── pre_commit_check.py # Обязателен перед каждым коммитом
└── CONVENTIONS.md      # Конвенции кода, чеклисты, команды
```

## Текущие метрики (март 2026)
- Benchmark: 30/30 success, quality 0.87 (quick: 7/7, 0.89)
- Спринты 1-9 завершены, FIX-1 → FIX-94, PERF-2, SCHED-1a, SCHED-1b, TG-UX, MEDIA-LAUNCH
- Полный список задач и фиксов → ARCHITECTURE_DECISIONS.md

## Критические правила
1. `python pre_commit_check.py` ПЕРЕД КАЖДЫМ коммитом. Упал → чини, НЕ коммить
2. После изменений в loop.py/planner.py/evaluator.py/gateway.py → `benchmark.py --quick`, скор ≥ предыдущего
3. Русские строки в .py → ТОЛЬКО unicode escapes (\u0442\u0435\u0441\u0442), НИКОГДА кириллица
4. Memory операции → ВСЕГДА try/except
5. Новый tool → регистрация в main.py И benchmark.py `build_registry()`
6. Новая команда → HELP_TEXT в handler.py И секция Commands в CONVENTIONS.md
7. Миграции → APPEND в `_MIGRATIONS` в database.py, НИКОГДА не переставлять
8. После задачи → обновить CLAUDE.md + ARCHITECTURE_DECISIONS.md + git commit с префиксом задачи

## Ссылки
- Конвенции кода, CLI, команды бота → **CONVENTIONS.md**
- Архитектурные решения Sprint 9+ → **ARCHITECTURE_DECISIONS.md**
- История Sprint 1-9 (early) → **ARCHITECTURE_DECISIONS_ARCHIVE.md**
- Архитектурные принципы → **organism_architecture_principles.md**
