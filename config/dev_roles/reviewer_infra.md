# Role: Infrastructure & Operations Reviewer

## Description
Reviews infrastructure: ProactiveScheduler (jobs, persistence, cron logic), error
monitoring (ErrorNotifier), logging (structlog setup), MCP servers (1C and Organism),
A2A protocol, Docker configuration, deployment scripts, and application settings.

## Files in scope
- src/organism/core/scheduler.py — ProactiveScheduler, ScheduledJob, load_jobs_from_config
- src/organism/monitoring/error_notifier.py — ErrorNotifier (Telegram error alerts)
- src/organism/logging/logger.py — Logger (task logging)
- src/organism/logging/error_handler.py — get_logger, log_exception
- src/organism/mcp_1c/server.py — 1C MCP server (demo/live modes)
- src/organism/mcp_serve/server.py — Organism as MCP server
- src/organism/a2a/protocol.py — Agent-to-Agent delegation protocol
- config/settings.py — all env vars, pydantic-settings
- config/jobs/artel_zoloto.json, default.json — scheduled jobs config
- scripts/health_check.py, deploy.sh, backup.sh, restore.sh
- Dockerfile, docker-compose.yml
- .env.example, .env.production.example

## What to check
1. **Scheduler persistence**: enable/disable state survives restart (FIX-89, FIX-93).
   Check: _save_job() called on enable/disable, load_and_sync() restores from DB.
2. **Pending publications**: DB-backed (FIX-92). Check: add/get/remove all use
   AsyncSessionLocal, no in-memory state left.
3. **Atomic publish**: remove_pending_publication uses DELETE...RETURNING (FIX-94).
   Check: no separate get+delete pattern.
4. **Job config sync**: load_jobs_from_config() output matches what scheduler.jobs contains
   after load_and_sync(). Check: DB state wins for enabled/last_run.
5. **Healthcheck**: Docker HEALTHCHECK — does it test real functionality (DB + bot)?
   Or just process alive? Check health_check.py — what does it verify?
6. **Error monitoring**: ErrorNotifier — does it actually send to Telegram on errors?
   Check: capture_error() called from gateway.py exception handler.
7. **MCP JSON-RPC**: both MCP servers handle /jsonrpc endpoint (Q-9.8). Check: all
   required methods (initialize, tools/list, tools/call) are implemented.
8. **Settings completeness**: every env var used in code has a corresponding field in
   settings.py. Check: grep for os.environ or os.getenv — should NOT exist (everything
   through settings).
9. **Deploy script**: scripts/deploy.sh — does it match docker-compose.yml services?
   Any hardcoded paths that don't match reality?
10. **A2A protocol**: is it used in production? Or just infrastructure placeholder?
    Check: imports of a2a.protocol from other modules.

## How to check
Write a Python script via code_executor that:
1. Read scheduler.py — verify _save_job() exists in enable_job/disable_job
2. Grep "DELETE.*RETURNING" in scheduler.py — verify atomic publish
3. Read Dockerfile — check HEALTHCHECK instruction
4. Grep "os.environ\|os.getenv" across all .py in src/ — should be zero (use settings)
5. Read scripts/deploy.sh — check referenced service names match docker-compose.yml
6. Grep "from.*a2a" across all .py files — find real usage

## Report format
Report in Russian:
```
ОБЛАСТЬ: Инфраструктура и операции (scheduler, monitoring, MCP, A2A, Docker)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
