import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.organism.llm.base import LLMProvider, Message


@dataclass
class PlanStep:
    id: int
    tool: str
    description: str
    input: dict[str, Any]
    depends_on: list[int] = field(default_factory=list)


FAST_PROMPT = (Path("config/prompts/planner_fast.txt")).read_text(encoding="utf-8")
REACT_PROMPT = (Path("config/prompts/planner_react.txt")).read_text(encoding="utf-8")


def _is_complex(task: str) -> bool:
    if len(task) > 200:
        return True
    complex_keywords = [
        "и потом", "затем", "после этого", "сначала", "во-первых",
        "and then", "after that", "first", "multiple", "several steps",
        "сравни", "проанализируй и", "найди и", "создай и отправь",
    ]
    return any(kw in task.lower() for kw in complex_keywords)


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("["):
        return text
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        return match.group(0)
    return text


def _parse_steps(raw: str) -> list[PlanStep]:
    json_str = _extract_json(raw)
    data = json.loads(json_str)
    steps = []
    for item in data:
        steps.append(PlanStep(
            id=item["id"],
            tool=item["tool"],
            description=item["description"],
            input=item["input"],
            depends_on=item.get("depends_on", []),
        ))
    return steps


class Planner:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def plan(self, task: str, memory_context: str = "") -> list[PlanStep]:
        use_react = _is_complex(task)

        # Add memory context to task if available
        full_task = task
        if memory_context:
            full_task = f"{task}\n\n[MEMORY CONTEXT]\n{memory_context}"

        if not use_react:
            steps = await self._fast_plan(full_task)
            if steps:
                return steps
            use_react = True

        if use_react:
            return await self._react_plan(full_task)

        return []

    async def _fast_plan(self, task: str) -> list[PlanStep]:
        for attempt in range(2):
            hint = "" if attempt == 0 else "\nIMPORTANT: Return ONLY a valid JSON array, nothing else."
            response = await self.llm.complete(
                messages=[Message(role="user", content=task + hint)],
                system=FAST_PROMPT,
                model_tier="balanced",
            )
            try:
                return _parse_steps(response.content)
            except (json.JSONDecodeError, KeyError):
                continue
        return []

    async def _react_plan(self, task: str) -> list[PlanStep]:
        response = await self.llm.complete(
            messages=[Message(role="user", content=task)],
            system=REACT_PROMPT,
            model_tier="balanced",
        )
        try:
            return _parse_steps(response.content)
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"Planner failed: {e}\nResponse: {response.content}")
