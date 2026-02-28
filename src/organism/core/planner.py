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

# ---- Phase 1: Task classifier prompt (Haiku, ~100 tokens) ----
CLASSIFIER_PROMPT = """Classify the user task. Respond with ONLY a JSON object, no explanation.

{
  "type": "writing" | "code" | "research" | "data" | "presentation" | "mixed",
  "tools": ["tool1", "tool2"]
}

Classification rules:
- "writing": articles, reports, proposals, letters, emails, templates → tools: ["text_writer"]
- "code": calculations, scripts, CSV/tables, data processing → tools: ["code_executor"]
- "research": search for information, news, facts → tools: ["web_search", "web_fetch"]
- "data": analyze data, statistics, charts → tools: ["code_executor"]
- "presentation": slides, PowerPoint → tools: ["pptx_creator"]
- "mixed": complex tasks needing 2+ different tool types → tools: list all needed

Available tools: text_writer, code_executor, web_search, web_fetch, file_manager, pptx_creator"""

# ---- Phase 2: Specialized planner prompts ----

PLAN_WRITING = """You are a task planner. Return ONLY a JSON array.

AVAILABLE TOOLS:
- text_writer: write long text and save to file. input: {"prompt": "detailed instructions", "filename": "file.md"}
- file_manager: read/write SHORT plain text files only (under 30 lines). input: {"action": "write", "path": "file.txt", "content": "short content"}

RULES:
- Use text_writer for anything longer than a few lines
- file_manager ONLY for very short files (configs, notes)
- Maximum 2 steps

Example:
[{"id":1,"tool":"text_writer","description":"Write report","input":{"prompt":"Write a professional report about...","filename":"report.md"},"depends_on":[]}]"""

PLAN_CODE = """You are a task planner. Return ONLY a JSON array.

AVAILABLE TOOLS:
- code_executor: run Python code in Docker sandbox. input: {"code": "python code here", "domains": []}

RULES:
- Write actual working Python code, not stubs
- For CSV: use csv module, write with open('filename.csv','w',newline='',encoding='utf-8-sig')
- For calculations: print all results with labels
- All print() statements must be explicit
- Keep code under 30 lines
- Maximum 2 steps

Example for CSV:
[{"id":1,"tool":"code_executor","description":"Create CSV report","input":{"code":"import csv\\nrows=[['Item','Value'],['Gold','300kg']]\\nwith open('report.csv','w',newline='',encoding='utf-8-sig') as f:\\n    csv.writer(f).writerows(rows)\\nprint('CSV created')","domains":[]},"depends_on":[]}]

Example for calculation:
[{"id":1,"tool":"code_executor","description":"Calculate plan","input":{"code":"total=300*1000\\ndaily=total/150\\nprint(f'Daily plan: {daily:.1f} g')","domains":[]},"depends_on":[]}]"""

PLAN_RESEARCH = """You are a task planner. Return ONLY a JSON array.

AVAILABLE TOOLS:
- web_search: search internet for information. input: {"query": "search query", "max_results": 5}
- web_fetch: fetch a specific URL. input: {"url": "https://...", "max_chars": 3000}

RULES:
- Start with web_search, then web_fetch for specific pages if needed
- NEVER fetch: g2.com, statista.com, forbes.com, gartner.com
- Maximum 3 steps
- Use clear, specific search queries

Example:
[{"id":1,"tool":"web_search","description":"Search for AI news","input":{"query":"AI news today 2026","max_results":5},"depends_on":[]}]"""

PLAN_PRESENTATION = """You are a task planner. Return ONLY a JSON array.

AVAILABLE TOOLS:
- pptx_creator: create PowerPoint presentation. input: {"filename": "name.pptx", "topic": "topic", "slides": [{"title": "...", "content": "brief key points"}]}

RULES:
- Create clear slide structure with concise content per slide
- 5-10 slides typically
- Maximum 1 step

Example:
[{"id":1,"tool":"pptx_creator","description":"Create presentation","input":{"filename":"report.pptx","topic":"Monthly report","slides":[{"title":"Overview","content":"Key metrics and results"}]},"depends_on":[]}]"""

PLAN_MIXED = """You are a task planner. Return ONLY a JSON array.

AVAILABLE TOOLS:
- web_search: search internet. input: {"query": "...", "max_results": 5}
- web_fetch: fetch URL. input: {"url": "https://...", "max_chars": 3000}
- text_writer: write long text and save to file. input: {"prompt": "detailed instructions including context from previous steps", "filename": "file.md"}
- code_executor: run Python code. input: {"code": "python code", "domains": []}

RULES:
- For "find + write" tasks: first web_search, then text_writer with {{step_1_output}} in prompt
- For "research + calculate" tasks: first web_search, then code_executor
- text_writer prompt MUST include: the original task + "Use this research data: {{step_1_output}}"
- Maximum 3 steps
- NEVER use web_fetch on: g2.com, statista.com, forbes.com

Example (find + write):
[
  {"id":1,"tool":"web_search","description":"Search for information","input":{"query":"search query","max_results":5},"depends_on":[]},
  {"id":2,"tool":"text_writer","description":"Write document based on research","input":{"prompt":"Write a memo about X. Use this research data: {{step_1_output}}","filename":"memo.md"},"depends_on":[1]}
]"""


# Map task type → specialized prompt
SPECIALIZED_PROMPTS = {
    "writing": PLAN_WRITING,
    "code": PLAN_CODE,
    "data": PLAN_CODE,
    "research": PLAN_RESEARCH,
    "presentation": PLAN_PRESENTATION,
    "mixed": PLAN_MIXED,
}


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

        # Phase 1: Classify task type (Haiku — fast, cheap)
        task_type = await self._classify(task)

        # Phase 2: Plan with specialized prompt
        use_react = _is_complex(task)

        if not use_react:
            steps = await self._specialized_plan(full_task, task_type)
            if steps:
                return steps
            # Fallback to generic fast plan
            steps = await self._fast_plan(full_task)
            if steps:
                return steps
            use_react = True

        return await self._react_plan(full_task)

    async def _classify(self, task: str) -> str:
        """Phase 1: Classify task type using Haiku (fast, ~100 tokens)."""
        try:
            response = await self.llm.complete(
                messages=[Message(role='user', content=task)],
                system=CLASSIFIER_PROMPT,
                model_tier='fast',
                max_tokens=150,
            )
            text = response.content.strip()
            # Extract JSON
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                data = json.loads(match.group(0))
                task_type = data.get('type', 'mixed')
                if task_type in SPECIALIZED_PROMPTS:
                    return task_type
        except Exception:
            pass
        return 'mixed'

    async def _specialized_plan(self, task: str, task_type: str) -> list[PlanStep]:
        """Phase 2: Plan with specialized prompt (only relevant tools)."""
        prompt = SPECIALIZED_PROMPTS.get(task_type)
        if not prompt:
            return []

        for attempt in range(2):
            hint = '' if attempt == 0 else '\nReturn ONLY valid JSON array, no explanation.'
            response = await self.llm.complete(
                messages=[Message(role='user', content=task + hint)],
                system=prompt,
                model_tier='balanced',
                max_tokens=4096,
            )
            try:
                return _parse_steps(response.content)
            except (json.JSONDecodeError, KeyError):
                continue
        return []

    async def _fast_plan(self, task: str) -> list[PlanStep]:
        """Fallback: generic plan with all tools."""
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