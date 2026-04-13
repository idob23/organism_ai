# CONVENTIONS.md — Organism AI

> Конвенции кода, команды, чеклисты. Читай когда задача касается конкретной области.

## Стиль кода
- UTF-8 везде
- Русские строки в .py: unicode escapes (`\u043f\u0440\u0438\u0432\u0435\u0442`), НИКОГДА кириллица
- async/await для всех IO операций
- Type hints обязательны
- Docstrings: Google style, только "почему", не "что"
- snake_case для функций/переменных, PascalCase для классов
- Максимальная длина строки: 100 символов
- Логирование через structlog, НИКОГДА print()
- Ошибки: кастомные исключения от OrganismError

## LLM-тиры
- `"fast"` = Haiku (классификация, роутинг, outline)
- `"balanced"` = Sonnet (исполнение, генерация)
- `"powerful"` = Opus (не используется в проде)

## Паттерны
- ABC для расширяемых компонентов (BaseTool, BaseLLM, BaseChannel)
- Pydantic models для валидации
- Dependency injection через конструктор
- Импорты: абсолютные от src.organism.*
- Git commits: префикс задачи (`Q-9.5: Agent commands`, `FIX-83: Timezone support`)

## Антипаттерны (НЕ делать)
- Классы когда достаточно функций
- Хардкод URL, ключей, путей — всё через config/settings.py
- God-объекты — один модуль = одна ответственность
- `*args, **kwargs` без необходимости
- Правила поверх симптомов — ищи технический корень проблемы
- Secrets in config/ — use `data/secrets/<service>/` (see SEC-1 in ARCHITECTURE_DECISIONS.md)

## Чеклист: новый Tool
1. Создать `src/organism/tools/my_tool.py`, наследовать `BaseTool`
2. Реализовать `name`, `description`, `parameters` (JSON schema), `execute()`
3. Вернуть `ToolResult(output=..., created_files=[...])` если создаёт файлы
4. Зарегистрировать в `src/organism/tools/bootstrap.py` → `build_registry()`
5. Если нужна инъекция — добавить setter (паттерн: `set_memory()`, `set_factory()`)
6. Добавить tool name в `config/personality/*.md` YAML whitelist для каждой personality
7. Добавить benchmark-задачу если tool тестируем
8. Обновить CLAUDE.md (File Structure, Tools таблица)

## Чеклист: новая Команда
1. Добавить handler в `commands/handler.py`
2. Добавить в `HELP_TEXT`
3. Добавить в секцию "Команды бота" ниже
4. Обновить CLAUDE.md

## CLI
```
python main.py --task "..."          # Одна задача
python main.py --multi --task "..."  # Multi-agent
python main.py --telegram            # Telegram бот
python main.py --interactive         # Интерактивный CLI
python main.py --stats               # Статистика памяти
python main.py --improve --days 7    # Auto-improvement
python main.py --optimize-prompts    # Оптимизация промптов
python main.py --serve-mcp           # MCP-сервер (порт 8091)
python benchmark.py                  # Полный benchmark (31 задач)
python benchmark.py --quick          # Быстрый (8 задач)
```

## Команды бота
```
/remember <key> <value>    — сохранить факт
/forget <key>              — удалить факт
/profile                   — все факты
/history <key>             — история изменений факта
/style <style>             — стиль (formal/informal/technical/brief)
/stats                     — статистика системы
/improve [days]            — auto-improvement
/prompts                   — версии промптов
/schedule                  — задачи планировщика
/schedule_enable <n>       — включить задачу
/schedule_disable <n>      — выключить задачу
/approve <id>              — одобрить действие
/reject <id>               — отклонить действие
/personality               — текущая личность
/reset                     — сбросить профиль
/insights                  — инсайты на проверку
/cleanup                   — очистка БД
/errors [N]                — последние N ошибок (по умолчанию 5)
/test_error                — тестовая ошибка в мониторинг
/agents                    — шаблоны ролей и агенты
/create_agent <role> [name] — создать агента (legacy, предпочтительнее manage_agents)
/assign <agent> <task>     — задача агенту (legacy, предпочтительнее manage_agents)
/pending                   — показать посты на проверке
/publish <id>              — опубликовать пост в канал
/reject_post <id>          — отклонить пост (удалить без публикации)
/help                      — все команды
```

## Бизнес-контекст
- Organism AI — универсальная платформа, НЕ отраслевое решение
- Стратегия: 1 человек + команда AI-агентов → unicorn company
- Первый клиент — любая организация (не привязана к конкретной отрасли)
- Текущая тестовая конфигурация: personality/artel_zoloto.md, таймзона Asia/Vladivostok
- Для нового клиента достаточно новой personality конфигурации, код не меняется
- Пользователи: бизнес-пользователи без технического бэкграунда
- Язык интерфейса: русский

## Как добавить новую Personality (CAPABILITY-1)

Personality файлы: `config/personality/{name}.md`. Каждый файл может начинаться с YAML front-matter:

```yaml
---
# Whitelist mode (only these tools available):
allowed_tools:
  - code_executor
  - text_writer
  - web_search
# Blacklist (always denied, overrides whitelist):
denied_tools:
  - dev_review
---

# Personality: Client Name
## Style
...
```

**Режимы доступа к tools:**
- `allowed_tools: null` (или отсутствует) → permissive, все tools разрешены
- `allowed_tools: [list]` → strict whitelist, только перечисленные
- `denied_tools: [list]` → проверяется первым, override-ит whitelist
- Нет YAML-блока → backward compat, все tools разрешены

**Добавление personality для нового клиента:**
1. Создать `config/personality/client_name.md`
2. Добавить YAML front-matter с нужными tools (скопировать из artel_zoloto.md как шаблон)
3. Написать markdown-промпт (Style, Terminology, Escalation, Report Preferences)
4. Настроить `ARTEL_ID=client_name` в `.env`
5. При добавлении нового tool — добавить его в whitelist каждой personality где он нужен

## Архитектурные карты

Reference artifact в `docs/maps/` — пять SVG, описывающих устройство
платформы. См. `docs/maps/README.md` для реестра и правил.

### Правило обновления

При любом архитектурном изменении (новый модуль, новая таблица в БД,
новая точка маршрутизации, новый MCP-сервер, изменение границ
безопасности, новый personality, новое хранилище памяти) обновляются
соответствующие карты в docs/maps/. В коммите в разделе ОТЧЁТ
указывается, какие карты затронуты.

Если изменение поведенческое (fix бага без архитектурного сдвига,
новый FIX-NN в существующем модуле) — карты не трогаем, просто
в ОТЧЁТ коммита пишем "Архитектурных изменений нет".
