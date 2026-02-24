@'
# Organism AI — контекст для AI-ассистента

## Что это
Персональный AI-агент для бизнеса. Python 3.11, Claude API, Docker sandbox.
Репозиторий: https://github.com/idob23/organism_ai

## Архитектура
- CoreLoop → Planner → ToolRegistry → Executor → Evaluator
- Инструменты: code_executor (Docker), web_search (Tavily), web_fetch, file_manager, pptx_creator, telegram_sender
- Память: pgvector (PostgreSQL)
- Агенты: Orchestrator + Coder, Researcher, Writer, Analyst

## Текущий статус
Этапы 1-5 завершены. Идёт тестирование (Блоки А-Г пройдены).

## Ключевые решения
- code_executor передаёт код через tmpfile + volume mount (не через -c аргумент)
- web_fetch блокирует: g2.com, statista.com, forbes.com, gartner.com
- Evaluator не фейлит 403-ответы и данные из прошлых лет
- pptx_creator не требует Docker — работает напрямую через python-pptx
- Planner использует max_tokens=4096, JSON парсится из "Thought + JSON" формата

## Стек
Python 3.11, anthropic SDK, docker-py, httpx, pgvector, aiogram, python-pptx, structlog
'@ | Out-File -Encoding UTF8 CONTEXT.md