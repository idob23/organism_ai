"""Q-9.1: Task Decomposer.

If a task is too complex for a single CoreLoop run (>10 steps or flagged
by Haiku as multi-phase), automatically breaks it into 2-5 ordered subtasks,
runs each sequentially through CoreLoop, passes context between them,
and returns an aggregated result. User sees one final answer.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from src.organism.llm.base import LLMProvider, Message
from src.organism.logging.error_handler import get_logger

_log = get_logger("core.decomposer")

DECOMPOSE_SYSTEM = (
    "You decide if a task is too complex for one execution and break it into subtasks.\n\n"
    "A task needs decomposition if it has MULTIPLE DISTINCT PHASES that must happen in sequence:\n"
    "  - Gather data + analyze + write report (3 phases)\n"
    "  - Research competitors + calculate metrics + create presentation (3 phases)\n"
    "  - Find info + process data + save to file (3 phases)\n\n"
    "A task does NOT need decomposition if it is:\n"
    "  - Single action: just write a document, just calculate something\n"
    "  - Already simple: 1-3 steps max\n\n"
    "Respond with ONLY JSON:\n"
    "{\"decompose\": true/false, \"subtasks\": [\"subtask 1\", \"subtask 2\", ...]}\n\n"
    "Rules for subtasks:\n"
    "  - 2 to 5 subtasks maximum\n"
    "  - Each subtask must be a complete, standalone instruction\n"
    "  - Order matters: later subtasks can reference 'results from previous step'\n"
    "  - All text in Russian\n"
    "  - If decompose=false: subtasks=[]"
)

AGGREGATE_SYSTEM = (
    "You receive results from multiple subtasks of a complex task. "
    "Synthesize them into ONE coherent final answer in Russian. "
    "Be concise but complete. Include all key facts, numbers, conclusions. "
    "Do not mention subtasks or decomposition \u2014 just give the final result."
)


@dataclass
class DecompositionPlan:
    should_decompose: bool
    subtasks: list[str] = field(default_factory=list)


class TaskDecomposer:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def analyze(self, task: str) -> DecompositionPlan:
        """Haiku check: should this task be decomposed? Returns plan."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=task[:400])],
                system=DECOMPOSE_SYSTEM,
                model_tier="fast",
                max_tokens=300,
            )
            text = resp.content.strip()
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                data = json.loads(match.group(0))
                should = bool(data.get("decompose", False))
                subtasks = [s for s in data.get("subtasks", []) if isinstance(s, str) and s.strip()]
                if should and len(subtasks) >= 2:
                    return DecompositionPlan(should_decompose=True, subtasks=subtasks[:5])
        except Exception as e:
            _log.warning(f"Decomposer analyze failed: {e}")
        return DecompositionPlan(should_decompose=False)

    async def run(
        self,
        task: str,
        subtasks: list[str],
        loop: "CoreLoop",  # type: ignore[name-defined]
        user_id: str = "default",
        user_context: str = "",
        progress_callback: Callable[[int, int, str], Awaitable[None]] | None = None,
    ) -> "TaskResult":  # type: ignore[name-defined]
        """Execute subtasks sequentially, pass context, aggregate results."""
        from src.organism.core.loop import TaskResult, StepLog

        start = time.time()
        subtask_results = []
        context_parts = []

        _log.info(f"Decomposing into {len(subtasks)} subtasks")

        for i, subtask in enumerate(subtasks, 1):
            # Notify progress
            if progress_callback:
                try:
                    await progress_callback(i, len(subtasks), subtask[:80])
                except Exception:
                    pass

            # Inject accumulated context into subtask
            enriched = subtask
            if context_parts:
                ctx_summary = "\n".join(context_parts[-2:])  # last 2 results
                enriched = (
                    f"{subtask}\n\n"
                    f"[\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b "
                    f"\u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0438\u0445 \u0448\u0430\u0433\u043e\u0432:\n"
                    f"{ctx_summary}]"
                )

            _log.info(f"Subtask {i}/{len(subtasks)}: {subtask[:60]}")

            result = await loop.run(
                enriched,
                verbose=False,
                user_id=user_id,
                user_context=user_context,
            )
            subtask_results.append(result)

            if result.success and result.output:
                # Keep compact summary for context passing
                summary = result.output[:600]
                context_parts.append(
                    f"\u0428\u0430\u0433 {i}: {summary}"
                )
            else:
                _log.warning(f"Subtask {i} failed: {result.error[:100] if result.error else 'unknown'}")

        # Aggregate
        successful = [r for r in subtask_results if r.success]
        if not successful:
            return TaskResult(
                task_id="decomposed",
                task=task,
                success=False,
                output="",
                error="\u0412\u0441\u0435 \u043f\u043e\u0434\u0437\u0430\u0434\u0430\u0447\u0438 \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u044b",
                duration=time.time() - start,
            )

        final_output = await self._aggregate(task, context_parts)
        all_steps = [s for r in subtask_results for s in r.steps]

        return TaskResult(
            task_id="decomposed",
            task=task,
            success=True,
            output=final_output,
            answer=final_output,
            steps=all_steps,
            duration=time.time() - start,
            quality_score=sum(r.quality_score for r in successful) / len(successful),
        )

    async def _aggregate(self, original_task: str, context_parts: list[str]) -> str:
        """Aggregate subtask results into one final answer via Haiku."""
        if not context_parts:
            return ""
        if len(context_parts) == 1:
            # Single result \u2014 return as-is, no aggregation needed
            return context_parts[0].split(":", 1)[-1].strip()

        combined = "\n\n".join(context_parts)
        prompt = (
            f"\u0418\u0441\u0445\u043e\u0434\u043d\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430: "
            f"{original_task[:200]}\n\n"
            f"\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b \u0448\u0430\u0433\u043e\u0432:\n"
            f"{combined[:3000]}"
        )
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=prompt)],
                system=AGGREGATE_SYSTEM,
                model_tier="fast",
                max_tokens=800,
            )
            return resp.content.strip()
        except Exception as e:
            _log.warning(f"Aggregation failed: {e}")
            return combined  # fallback: raw concatenation
