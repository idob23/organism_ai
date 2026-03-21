# Role: Infrastructure & Operations Reviewer

## Description
Reviews infrastructure: Scheduler, error monitoring, logging, MCP servers, A2A protocol,
Docker config, deployment scripts, and settings. Focus on persistence and config safety.

## Context files
- src/organism/core/scheduler.py — ProactiveScheduler, ScheduledJob
- src/organism/monitoring/error_notifier.py — ErrorNotifier (Telegram alerts)
- src/organism/logging/logger.py — Logger (task logging)
- src/organism/logging/error_handler.py — get_logger, log_exception
- src/organism/mcp_1c/server.py — 1C MCP server
- src/organism/mcp_serve/server.py — Organism as MCP server
- src/organism/a2a/protocol.py — Agent-to-Agent protocol
- config/settings.py — all env vars, pydantic-settings
- config/jobs/*.json — scheduled jobs config
- scripts/health_check.py, deploy.sh, backup.sh, restore.sh
- Dockerfile, docker-compose.yml
- .env.example, .env.production.example

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Scheduler DB persistence
**What**: All methods that modify ScheduledJob state call _save_job() to persist.
**How to verify**: `grep -n "_save_job\|enable_job\|disable_job" /repo/src/organism/core/scheduler.py`
— verify enable_job and disable_job both call _save_job().
**Violation = problem**: Enable/disable state lost on restart.

### INV-2: Settings completeness
**What**: Every `settings.attribute` used in src/ is defined in config/settings.py.
**How to verify**: `grep -rohn "settings\.\([a-z_]*\)" /repo/src/ --include="*.py" | sort -u`
— extract unique attribute names, compare with Field definitions in settings.py.
**Violation = problem**: AttributeError at runtime, silent config missing.

### INV-3: Docker isolation of dev files
**What**: .dockerignore excludes dev-only files: scripts/, config/dev_roles/.
**How to verify**: `cat /repo/.dockerignore` — verify scripts/ and config/dev_roles/ present.
**Violation = problem**: Dev tools shipped to production container.

## Contextual checks (within scope)
- Scheduler pending publications: DB-backed (FIX-92), atomic publish via DELETE...RETURNING.
- Job config sync: load_jobs_from_config() vs load_and_sync() — DB state wins for enabled/last_run.
- Healthcheck: Docker HEALTHCHECK tests real functionality (DB + bot), not just process alive.
- Error monitoring: ErrorNotifier.capture_error() called from gateway.py exception handler.
- MCP JSON-RPC: both servers implement initialize, tools/list, tools/call (Q-9.8).
- No raw os.environ: `grep -rn "os.environ\|os.getenv" /repo/src/ --include="*.py"` — should
  be zero (everything through settings).
- Deploy script: scripts/deploy.sh service names match docker-compose.yml.
- A2A protocol: verify it's importable and DelegateToAgentTool conditionally registered.
- Startup ordering: init_db() runs before scheduler.load_and_sync().

## How to verify
Script should:
1. Execute INV-1: grep _save_job and enable/disable in scheduler.py
2. Execute INV-2: extract settings.X usage, compare with settings.py fields
3. Execute INV-3: read .dockerignore, verify exclusions
4. Contextual: read scheduler.py for persistence, Dockerfile for healthcheck, deploy scripts

## Report format
Report in Russian:
```
OBLAST: Infrastructure and operations (scheduler, monitoring, MCP, Docker)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Scheduler persistence — details
  INV-2 [PASS/FAIL]: Settings completeness — details
  INV-3 [PASS/FAIL]: Docker isolation — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
