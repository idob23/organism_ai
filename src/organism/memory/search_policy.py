"""Q-5.5: SearchPolicy — intent classification and weighted multi-source memory search.

Intent is classified by pure regex (no LLM, zero cost).
Each intent maps to a weight vector across memory sources:
  vector   — pgvector semantic similarity
  temporal — graph edges of type "temporal"
  causal   — graph edges of type "causal"
  entity   — graph entity subgraph
  template — procedural templates

Intents:
  factual    — general knowledge question (default)
  temporal   — time-based: когда, история, вчера, ...
  causal     — why/because: почему, причина, из-за
  entity     — who/whose: кто, чей, ответственный
  procedural — how-to: каким образом, способ, инструкция, or starts with "как "
"""
import re


class SearchPolicy:

    # All Russian keywords stored as unicode escapes (Windows PowerShell encoding rule)
    _WHEN_KEYWORDS = [
        "\u043a\u043e\u0433\u0434\u0430",                 # когда
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439",  # последний
        "\u0438\u0441\u0442\u043e\u0440\u0438\u044f",     # история
        "\u0434\u0430\u0442\u0430",                        # дата
        "\u0432\u0447\u0435\u0440\u0430",                  # вчера
        "\u043d\u0435\u0434\u0435\u043b\u044e",            # неделю
    ]
    _WHY_KEYWORDS = [
        "\u043f\u043e\u0447\u0435\u043c\u0443",            # почему
        "\u043f\u0440\u0438\u0447\u0438\u043d\u0430",      # причина
        "\u0438\u0437-\u0437\u0430",                        # из-за
    ]
    _WHO_KEYWORDS = [
        "\u043a\u0442\u043e",                               # кто
        "\u0447\u0435\u0439",                               # чей
        "\u043e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439",  # ответственный
    ]
    _HOW_KEYWORDS = [
        "\u043a\u0430\u043a\u0438\u043c \u043e\u0431\u0440\u0430\u0437\u043e\u043c",  # каким образом
        "\u0441\u043f\u043e\u0441\u043e\u0431",             # способ
        "\u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f",  # инструкция
    ]

    WEIGHTS: dict[str, dict[str, float]] = {
        "factual":    {"vector": 1.0, "temporal": 0.2, "causal": 0.0, "entity": 0.3, "template": 0.5},
        "temporal":   {"vector": 0.3, "temporal": 1.0, "causal": 0.2, "entity": 0.3, "template": 0.0},
        "causal":     {"vector": 0.3, "temporal": 0.3, "causal": 1.0, "entity": 0.5, "template": 0.0},
        "entity":     {"vector": 0.5, "temporal": 0.2, "causal": 0.2, "entity": 1.0, "template": 0.0},
        "procedural": {"vector": 0.5, "temporal": 0.0, "causal": 0.0, "entity": 0.2, "template": 1.0},
    }

    def classify_intent(self, task: str) -> str:
        """Pure regex classification — no LLM, fast and free.

        Priority order: temporal > causal > entity > procedural > factual (default).
        "как" is checked last as a standalone prefix to avoid over-matching.
        """
        task_lower = task.lower()

        if any(kw in task_lower for kw in self._WHEN_KEYWORDS):
            return "temporal"
        if any(kw in task_lower for kw in self._WHY_KEYWORDS):
            return "causal"
        if any(kw in task_lower for kw in self._WHO_KEYWORDS):
            return "entity"
        if any(kw in task_lower for kw in self._HOW_KEYWORDS):
            return "procedural"
        # "\u043a\u0430\u043a " = "как " — too broad, only match as a leading prefix
        if task_lower.startswith("\u043a\u0430\u043a "):
            return "procedural"
        return "factual"

    def get_weights(self, intent: str) -> dict[str, float]:
        return self.WEIGHTS.get(intent, self.WEIGHTS["factual"])

    # ------------------------------------------------------------------
    # Entity extraction helper (used by MemoryManager.on_task_start)
    # ------------------------------------------------------------------

    _STOPWORDS: frozenset[str] = frozenset({
        "\u0441\u043e\u0437\u0434\u0430\u0439",    # создай
        "\u043d\u0430\u043f\u0438\u0448\u0438",    # напиши
        "\u0441\u0434\u0435\u043b\u0430\u0439",    # сделай
        "\u043d\u0430\u0439\u0434\u0438",           # найди
        "\u043f\u043e\u043a\u0430\u0436\u0438",    # покажи
        "\u043e\u0442\u0447\u0451\u0442",           # отчёт
        "\u0442\u0430\u0431\u043b\u0438\u0446\u0443",  # таблицу
        "\u0444\u0430\u0439\u043b",                 # файл
        "\u0434\u0430\u043d\u043d\u044b\u0435",    # данные
    })

    def extract_entities(self, task: str) -> list[str]:
        """Simple heuristic entity extraction — no LLM, speed over precision.

        Splits task on whitespace, keeps tokens that are:
          - longer than 4 characters (after stripping punctuation)
          - not in the stop-list
          - unique (case-insensitive)

        Preserves hyphenated tokens (КамАЗ-65115) and alphanumeric codes (PC-300)
        by only stripping characters that are not word chars, digits, or hyphens.
        """
        seen: set[str] = set()
        entities: list[str] = []

        for word in re.split(r"\s+", task.strip()):
            # Strip leading/trailing punctuation, keep internal hyphens and digits
            clean = re.sub(r"^[^\w\-]+|[^\w\-]+$", "", word, flags=re.UNICODE)
            if len(clean) > 4 and clean.lower() not in self._STOPWORDS and clean.lower() not in seen:
                seen.add(clean.lower())
                entities.append(clean)

        return entities
