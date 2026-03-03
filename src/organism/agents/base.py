import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.organism.llm.base import LLMProvider, TemperatureLocked, Message
from src.organism.tools.registry import ToolRegistry
from src.organism.logging.error_handler import get_logger

if TYPE_CHECKING:
    from src.organism.memory.manager import MemoryManager

_log = get_logger("agent.base")


@dataclass
class AgentResult:
    agent: str
    task: str
    output: str
    success: bool
    duration: float
    error: str = ""


class BaseAgent(ABC):

    # Subclasses override these to specialize behavior
    temperature: float = 0.5
    max_iterations: int = 3

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        memory: "MemoryManager | None" = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.memory = memory

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """What this agent specializes in."""

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """Tool names this agent uses."""

    @abstractmethod
    async def run(self, task: str) -> AgentResult:
        """Execute a task."""

    async def _reflect(self, task: str, result: AgentResult) -> dict | None:
        """Call Haiku for structured self-evaluation (Q-7.1).

        Returns dict with keys: score, insight, failure_type, root_cause,
        corrective_action, confidence.  Falls back gracefully if Haiku
        returns the old {score, insight} format.
        """
        error_text = (result.error[:200] if result.error else "none")
        prompt = (
            f"Task: {task[:300]}\n"
            f"Status: {'SUCCESS' if result.success else 'FAILURE'}\n"
            f"Output: {result.output[:300]}\n"
            f"Error: {error_text}\n\n"
            "Analyze this task execution. Respond with ONLY a JSON object:\n"
            "{\n"
            '  "score": <1-5 quality rating>,\n'
            '  "failure_type": "<tool_error|plan_error|llm_error|timeout|validation|none>",\n'
            '  "root_cause": "<one sentence: what specifically went wrong, or none if success>",\n'
            '  "corrective_action": "<one sentence: specific actionable rule for next time>",\n'
            '  "confidence": <0.0-1.0 how confident you are in this analysis>\n'
            "}\n"
            "Return ONLY the JSON, no explanation."
        )
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=prompt)],
                model_tier="fast",
                max_tokens=200,
            )
            raw = resp.content.strip()
            match = re.search(r"\{[\s\S]*\}", raw)
            if not match:
                return None
            data = json.loads(match.group(0))
            score = int(data.get("score", 0))
            if not (1 <= score <= 5):
                return None

            # Structured format (Q-7.1)
            if "failure_type" in data or "corrective_action" in data:
                failure_type = str(data.get("failure_type", "unknown")).strip()
                root_cause = str(data.get("root_cause", "")).strip()
                corrective_action = str(data.get("corrective_action", "")).strip()
                confidence = float(data.get("confidence", 0.5))
                confidence = max(0.0, min(1.0, confidence))
                return {
                    "score": score,
                    "insight": corrective_action or root_cause,
                    "failure_type": failure_type,
                    "root_cause": root_cause,
                    "corrective_action": corrective_action,
                    "confidence": confidence,
                }

            # Fallback: old {score, insight} format — fill defaults
            insight = str(data.get("insight", "")).strip()
            if not insight:
                return None
            return {
                "score": score,
                "insight": insight,
                "failure_type": "unknown",
                "root_cause": insight,
                "corrective_action": insight,
                "confidence": 0.5,
            }
        except Exception:
            pass
        return None

    async def _save_reflection(self, task: str, result: AgentResult) -> None:
        """Reflect on result and persist to memory if available."""
        reflection = await self._reflect(task, result)
        if not reflection:
            return
        _log.info(
            f"[{result.agent}] Reflection score={reflection['score']}"
            f" type={reflection.get('failure_type', '?')}:"
            f" {reflection.get('corrective_action', reflection.get('insight', ''))[:80]}"
        )
        if self.memory:
            try:
                await self.memory.save_reflection(
                    self.name, task, reflection["score"], reflection.get("insight", ""),
                    failure_type=reflection.get("failure_type"),
                    root_cause=reflection.get("root_cause"),
                    corrective_action=reflection.get("corrective_action"),
                    reflection_confidence=reflection.get("confidence"),
                )
            except Exception:
                pass
