# Role: Channels, Gateway & Commands Reviewer

## Description
Reviews the channel abstraction layer (Gateway, Telegram, CLI), command handler
(23+ commands), BotSender, and message flow: IncomingMessage → Gateway → CoreLoop →
OutgoingMessage → Channel. Ensures commands are documented, chat history saved
correctly, file delivery works, and error handling is consistent.

## Files in scope
- src/organism/channels/base.py — IncomingMessage, OutgoingMessage, BaseChannel
- src/organism/channels/gateway.py — Gateway (message router, file delivery)
- src/organism/channels/telegram.py — TelegramChannel (progress, cancel/retry, media)
- src/organism/channels/cli_channel.py — CLIChannel
- src/organism/channels/bot_sender.py — BotSender (unified Telegram send)
- src/organism/commands/handler.py — CommandHandler (23+ commands, HELP_TEXT)

## What to check
1. **Command documentation**: every command in handler.py must be in HELP_TEXT AND
   in CONVENTIONS.md "Команды бота". code_health.py checks this — verify findings.
2. **Chat history single source**: save_message() must be called ONLY in gateway.py
   handle_message() for regular tasks, and for /assign (FIX-71). Nowhere else.
3. **File delivery chain**: TaskResult.created_files → gateway._prepare_response() →
   metadata["files"] → telegram.py send as document. Verify entire chain works.
4. **Error propagation**: exceptions in CoreLoop should produce user-friendly error
   messages in Telegram, not raw tracebacks. Check exception handling in gateway.
5. **Cancel/retry**: FIX-87 — asyncio.Task.cancel() used, CancelledError caught in
   telegram.py. Verify: cancel button works, retry button re-runs with original text.
6. **HTML escaping**: all Telegram output uses HTML parse_mode. Check: _escape_html()
   applied to user-generated content. Missing = potential HTML injection.
7. **BotSender session management**: Bot() created per call, session closed always.
   Check: no session leak on exception in send().
8. **Progress callback**: tool_progress_callback reaches telegram.py ticker.
   Check: exception in callback doesn't crash task execution.
9. **Long text handling**: gateway._TEXT_LIMIT=3500. Text > 3500 → .txt file.
   Check: edge cases (exactly 3500, 0 length, only whitespace).
10. **Command routing**: is_command() checks startswith("/"). But what about
    "/unknown_command"? Does it produce a helpful error or silent failure?

## How to check
Write a Python script via code_executor that:
1. Parse HELP_TEXT from handler.py — extract all /command names
2. Parse CONVENTIONS.md — extract all /command names from "Команды бота"
3. Compare sets — find mismatches
4. Grep "save_message" across all .py files — list locations
5. Trace created_files: from base.py ToolResult → loop.py → gateway.py → telegram.py
6. Check all callback handlers in telegram.py have try/except

## Report format
Report in Russian:
```
ОБЛАСТЬ: Каналы, Gateway и команды (channels/, commands/)
ПРОВЕРЕНО ФАЙЛОВ: N
НАЙДЕНО ПРОБЛЕМ: N (критических: N, средних: N, мелких: N)

ПРОБЛЕМЫ:
1. [КРИТИЧЕСКАЯ] ... → рекомендация
2. [СРЕДНЯЯ] ... → рекомендация

ЧТО МОЖНО УЛУЧШИТЬ:
- ...

ЗАКЛЮЧЕНИЕ: {общая оценка состояния подсистемы}
```
