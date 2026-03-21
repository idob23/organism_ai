# Role: Channels & Commands Reviewer

## Description
Reviews the communication layer: Gateway, Telegram channel, CLI channel,
command handler, and bot sender. Focus on command sync, HTML safety, and file delivery.

## Context files
- src/organism/channels/gateway.py — Gateway: dispatch, response routing, chat_history
- src/organism/channels/telegram.py — TelegramChannel: aiogram, send/receive, HTML
- src/organism/channels/cli_channel.py — CLIChannel: interactive mode
- src/organism/channels/base.py — IncomingMessage, OutgoingMessage
- src/organism/channels/bot_sender.py — BotSender for unified Telegram sending
- src/organism/commands/handler.py — CommandHandler, HELP_TEXT, all /commands
- src/organism/monitoring/error_notifier.py — error notification via Telegram

## INVARIANTS (verify exhaustive across ENTIRE codebase)

### INV-1: Command sync between HELP_TEXT and CONVENTIONS.md
**What**: All commands in handler.py HELP_TEXT exist in CONVENTIONS.md and vice versa.
**How to verify**: `python scripts/code_health.py` — check_command_sync() result.
**Violation = problem**: Commands visible to user but undocumented, or docs list phantom commands.

### INV-2: HTML escape coverage
**What**: Every `bot.send_message` or `send()` with `parse_mode="HTML"` uses escaped content.
**How to verify**: `grep -n "parse_mode" /repo/src/organism/channels/telegram.py` — trace
that content for each HTML send call goes through `_escape_html` or equivalent.
**Violation = problem**: HTML injection, broken formatting from user/LLM content.

### INV-3: File delivery chain intact
**What**: `created_files` from ToolResult reaches telegram send_document end-to-end.
**How to verify**: `grep -rn "created_files" /repo/src/organism/ --include="*.py"` — trace
chain: tool execute() -> loop.py -> gateway.py -> telegram.py send_document.
**Violation = problem**: Files created by tools but never delivered to user.

## Contextual checks (within scope)
- Cancel/retry: asyncio.Task.cancel() + CancelledError handling correct (FIX-87).
- Long text handling: messages >4096 chars split correctly, no truncation.
- BotSender session lifecycle: aiohttp session created/closed, no leaks on exception.
- Progress callback: fire-and-forget, error doesn't crash main execution.
- Gateway dispatch: correct routing for commands vs tasks vs media.
- Chat history single save point: save_message only in gateway.py (cross-ref INV-4 memory).
- Unknown command handling: /invalid_command produces helpful error, not silent failure.

## How to verify
Script should:
1. Run `python scripts/code_health.py` — use result for INV-1
2. Execute INV-2: grep parse_mode in telegram.py, trace content through _escape_html
3. Execute INV-3: grep created_files across codebase, verify chain continuity
4. Contextual: read telegram.py for cancel/retry, text splitting, session management

## Report format
Report in Russian:
```
OBLAST: Channels and commands (channels/, commands/)
CHECKED FILES: N
ISSUES FOUND: N (critical: N, medium: N, minor: N)

INVARIANTS:
  INV-1 [PASS/FAIL]: Command sync — details
  INV-2 [PASS/FAIL]: HTML escape coverage — details
  INV-3 [PASS/FAIL]: File delivery chain — details

CONTEXTUAL ISSUES:
1. [CRITICAL/MEDIUM/MINOR] ... -> recommendation

IMPROVEMENTS:
- ...

CONCLUSION: {overall subsystem assessment}
```
