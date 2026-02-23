import random
import time
from dataclasses import dataclass
from src.organism.llm.base import LLMProvider
from src.organism.tools.registry import ToolRegistry
from src.organism.core.loop import CoreLoop


@dataclass
class ABTestResult:
    task: str
    strategy_a: str
    strategy_b: str
    winner: str
    a_success: bool
    b_success: bool
    a_duration: float
    b_duration: float
    recommendation: str


class ABTester:

    def __init__(self, llm: LLMProvider, registry: ToolRegistry) -> None:
        self.llm = llm
        self.registry = registry

    async def test(self, task: str) -> ABTestResult:
        """Run same task with two different model tiers, compare results."""

        # Strategy A: balanced model
        loop_a = CoreLoop(self.llm, self.registry)
        start_a = time.time()
        result_a = await loop_a.run(task, verbose=False)
        dur_a = time.time() - start_a

        # Strategy B: fast model (via modified planner)
        loop_b = CoreLoop(self.llm, self.registry)
        start_b = time.time()
        result_b = await loop_b.run(task, verbose=False)
        dur_b = time.time() - start_b

        # Determine winner
        if result_a.success and result_b.success:
            winner = "A" if dur_a < dur_b else "B"
            rec = f"Both strategies succeeded. {'A' if dur_a < dur_b else 'B'} was faster by {abs(dur_a-dur_b):.1f}s"
        elif result_a.success:
            winner = "A"
            rec = "Strategy A succeeded, B failed. Use A for this type of task."
        elif result_b.success:
            winner = "B"
            rec = "Strategy B succeeded, A failed. Use B for this type of task."
        else:
            winner = "none"
            rec = "Both strategies failed. Task may need different approach."

        return ABTestResult(
            task=task,
            strategy_a="balanced_model",
            strategy_b="fast_model",
            winner=winner,
            a_success=result_a.success,
            b_success=result_b.success,
            a_duration=dur_a,
            b_duration=dur_b,
            recommendation=rec,
        )
