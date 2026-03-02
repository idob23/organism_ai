"""Q-4.4: Automatic improvement cycle.

Analyzes recent task failures → groups by tool-pattern → Haiku generates
improvement suggestions → converts to KnowledgeBase rules → better planning.

Flow: analyze_failures → generate_rules → run_cycle (save to KnowledgeBase)
"""
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

from src.organism.llm.base import LLMProvider, Message
from src.organism.memory.database import AsyncSessionLocal
from src.organism.logging.error_handler import get_logger, log_exception

if TYPE_CHECKING:
    from src.organism.memory.manager import MemoryManager
    from src.organism.memory.knowledge_base import KnowledgeBase

_log = get_logger("self_improvement.auto_improver")

_ANALYZE_SYSTEM = (
    "Analyze this group of AI agent failures. "
    "Identify the common error pattern and suggest ONE specific actionable rule "
    "that would prevent or reduce similar failures. "
    "Respond with ONLY a JSON object: "
    '{"pattern": "brief pattern name", "suggestion": "specific rule, starts with a verb"} '
    "Return ONLY the JSON, no explanation."
)

_RULES_SYSTEM = (
    "Convert these failure patterns into specific planning rules. "
    "Each rule must be concise, actionable, and start with a verb (Always/Never/When/Prefer/Avoid). "
    "Assign confidence: 0.70 for 2 occurrences, 0.75 for 3-4, 0.80 for 5+. "
    "Return ONLY a JSON array: "
    '[{"rule_text": "...", "confidence": 0.75}] '
    "Return ONLY the JSON array, no explanation."
)


class AutoImprover:

    async def analyze_failures(
        self,
        memory: "MemoryManager",
        llm: LLMProvider,
        days: int = 7,
    ) -> list[dict]:
        """Query failed tasks from the last N days, group by tool pattern,
        call Haiku per group to generate improvement suggestions.

        Returns list of {pattern, count, suggestion, tool_pattern}.
        Returns [] when no failures found or on error.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        try:
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(
                    text("""
                        SELECT tools_used, task, result
                        FROM task_memories
                        WHERE success = false
                          AND created_at >= :cutoff
                        ORDER BY created_at DESC
                        LIMIT 100
                    """),
                    {"cutoff": cutoff},
                )).fetchall()
        except Exception as e:
            log_exception(_log, "Failed to query failures from task_memories", e)
            return []

        if not rows:
            _log.info(f"No failed tasks in the last {days} days")
            return []

        # Group samples by tools_used pattern
        groups: dict[str, list[dict]] = defaultdict(list)
        for tools_used, task, result in rows:
            key = (tools_used or "unknown").strip() or "unknown"
            groups[key].append({"task": (task or "")[:200], "result": (result or "")[:200]})

        _log.info(f"Found {len(rows)} failed tasks across {len(groups)} tool-pattern group(s)")

        results = []
        for tool_pattern, samples in groups.items():
            count = len(samples)
            sample_lines = "\n".join(
                f"- Task: {s['task']}\n  Result: {s['result']}"
                for s in samples[:3]
            )
            prompt = (
                f"Tool pattern: {tool_pattern}\n"
                f"Failure count: {count}\n"
                f"Sample failures:\n{sample_lines}"
            )
            try:
                resp = await llm.complete(
                    messages=[Message(role="user", content=prompt)],
                    system=_ANALYZE_SYSTEM,
                    model_tier="fast",
                    max_tokens=150,
                )
                raw = resp.content.strip()
                m = re.search(r"\{[\s\S]*\}", raw)
                if m:
                    data = json.loads(m.group(0))
                    pattern = str(data.get("pattern", tool_pattern)).strip()
                    suggestion = str(data.get("suggestion", "")).strip()
                    if suggestion:
                        results.append({
                            "pattern": pattern,
                            "count": count,
                            "suggestion": suggestion,
                            "tool_pattern": tool_pattern,
                        })
                        _log.info(f"Pattern '{pattern}' (n={count}): {suggestion[:80]}")
            except Exception as e:
                log_exception(_log, f"Haiku analysis failed for pattern '{tool_pattern}'", e)

        _log.info(f"analyze_failures complete: {len(results)} patterns with suggestions")
        return results

    async def generate_rules(
        self,
        failures: list[dict],
        llm: LLMProvider,
    ) -> list[dict]:
        """Convert failure patterns with count >= 2 into KnowledgeBase rules.

        Calls Haiku once with all qualifying patterns.
        Returns list of {rule_text, confidence}.
        """
        candidates = [f for f in failures if f.get("count", 0) >= 2]
        if not candidates:
            _log.info("No patterns with count >= 2 — skipping rule generation")
            return []

        patterns_text = "\n".join(
            f"- Pattern: {f['pattern']} (count={f['count']})\n  Suggestion: {f['suggestion']}"
            for f in candidates
        )
        try:
            resp = await llm.complete(
                messages=[Message(role="user", content=patterns_text)],
                system=_RULES_SYSTEM,
                model_tier="fast",
                max_tokens=400,
            )
            raw = resp.content.strip()
            m = re.search(r"\[[\s\S]*\]", raw)
            if not m:
                _log.warning("generate_rules: no JSON array found in Haiku response")
                return []
            data = json.loads(m.group(0))
            rules = []
            for item in data:
                rule_text = str(item.get("rule_text", "")).strip()
                try:
                    confidence = float(item.get("confidence", 0.70))
                except (TypeError, ValueError):
                    confidence = 0.70
                confidence = max(0.0, min(1.0, confidence))
                if rule_text:
                    rules.append({"rule_text": rule_text, "confidence": confidence})
            _log.info(f"generate_rules: {len(rules)} rule(s) from {len(candidates)} pattern(s)")
            return rules
        except Exception as e:
            log_exception(_log, "generate_rules failed", e)
            return []

    async def run_cycle(
        self,
        memory: "MemoryManager",
        llm: LLMProvider,
        knowledge_base: "KnowledgeBase",
        days: int = 7,
    ) -> dict:
        """Full improvement cycle: failures → patterns → rules → KnowledgeBase.

        Returns summary {failures_found, patterns_analyzed, rules_saved}.
        """
        _log.info(f"Auto-improvement cycle started (window={days}d)")

        failures = await self.analyze_failures(memory, llm, days=days)
        rules = await self.generate_rules(failures, llm)

        saved = 0
        for rule in rules:
            try:
                await knowledge_base.add_rule(
                    rule_text=rule["rule_text"],
                    confidence=rule["confidence"],
                    source_task_hash="auto_improve",
                )
                _log.info(
                    f"Rule saved (conf={rule['confidence']:.2f}): {rule['rule_text'][:80]}"
                )
                saved += 1
            except Exception as e:
                log_exception(_log, "Failed to save rule to KnowledgeBase", e)

        summary = {
            "failures_found": sum(f["count"] for f in failures),
            "patterns_analyzed": len(failures),
            "rules_saved": saved,
        }
        _log.info(f"Auto-improvement cycle complete: {summary}")
        return summary
