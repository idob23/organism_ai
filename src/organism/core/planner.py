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


FAST_PROMPT = Path("config/prompts/planner_fast.txt").read_text(encoding="utf-8")
REACT_PROMPT = Path("config/prompts/planner_react.txt").read_text(encoding="utf-8")


def _is_complex(task: str) -> bool:
    if len(task) > 200:
        return True
    keywords = ["and then", "after that", "first", "several steps",
                "pptx", "powerpoint", "prezentaci"]
    return any(kw in task.lower() for kw in keywords)


def _sanitize_json(text: str) -> str:
    """Fix control characters inside JSON strings."""
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"' and (i == 0 or text[i-1] != '\\'):
            in_string = not in_string
            result.append(c)
        elif in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\r':
            result.append('\\r')
        elif in_string and c == '\t':
            result.append('\\t')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _extract_json(text: str) -> str:
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    if text.startswith('['):
        return text
    matches = list(re.finditer(r'\[[\s\S]*\]', text))
    if matches:
        return matches[-1].group(0)
    return text


def _parse_steps(raw: str) -> list[PlanStep]:
    json_str = _extract_json(raw)
    json_str = _sanitize_json(json_str)
    data = json.loads(json_str)
    steps = []
    for i, item in enumerate(data):
        inp = item.get('input') or item.get('params') or {}
        steps.append(PlanStep(
            id=item.get('id', i + 1),
            tool=item['tool'],
            description=item.get('description', item['tool']),
            input=inp,
            depends_on=item.get('depends_on', []),
        ))
    return steps


class Planner:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def plan(self, task: str, memory_context: str = '') -> list[PlanStep]:
        full_task = task
        if memory_context:
            full_task = f"{task}\n\n[Memory: {memory_context[:200]}]"

        use_react = _is_complex(task)

        if not use_react:
            steps = await self._fast_plan(full_task)
            if steps:
                return steps
            use_react = True

        return await self._react_plan(full_task)

    async def _fast_plan(self, task: str) -> list[PlanStep]:
        for attempt in range(2):
            hint = '' if attempt == 0 else '\nReturn ONLY valid JSON array, no explanation.'
            response = await self.llm.complete(
                messages=[Message(role='user', content=task + hint)],
                system=FAST_PROMPT,
                model_tier='balanced',
                max_tokens=4096,
            )
            try:
                return _parse_steps(response.content)
            except (json.JSONDecodeError, KeyError):
                continue
        return []

    async def _react_plan(self, task: str) -> list[PlanStep]:
        response = await self.llm.complete(
            messages=[Message(role='user', content=task)],
            system=REACT_PROMPT,
            model_tier='balanced',
            max_tokens=4096,
        )
        try:
            return _parse_steps(response.content)
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f'Planner failed: {e}\nResponse: {response.content[:500]}')

