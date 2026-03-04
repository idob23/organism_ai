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
CLASSIFIER_PROMPT = """You are a task classifier for an autonomous AI executor. Classify the user task by its primary output type. Respond with ONLY a JSON object, no explanation.

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

PLAN_WRITING = """You are a document planner. Choose the most efficient tool for the requested text output. Return ONLY a JSON array.

AVAILABLE TOOLS:
- text_writer: write long text and save to file. input: {"prompt": "detailed instructions", "filename": "file.md"}
- file_manager: read/write SHORT plain text files only (under 30 lines). input: {"action": "write", "path": "file.txt", "content": "short content"}

RULES:
- Use text_writer for anything longer than a few lines
- file_manager ONLY for very short files (configs, notes)
- Maximum 2 steps

Example:
[{"id":1,"tool":"text_writer","description":"Write report","input":{"prompt":"Write a professional report about...","filename":"report.md"},"depends_on":[]}]"""

PLAN_CODE = """You are a computation planner. Write minimal, working Python code that produces clearly labeled output. Return ONLY a JSON array.

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

PLAN_RESEARCH = """You are a research planner. Find the most authoritative source with minimum search steps. Return ONLY a JSON array.

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

PLAN_PRESENTATION = """You are a presentation planner. Structure slides for clarity and business impact. Return ONLY a JSON array.

AVAILABLE TOOLS:
- pptx_creator: create PowerPoint presentation. input: {"filename": "name.pptx", "topic": "topic", "slides": [{"title": "...", "content": "brief key points"}]}

RULES:
- Create clear slide structure with concise content per slide
- 5-10 slides typically
- Maximum 1 step

Example:
[{"id":1,"tool":"pptx_creator","description":"Create presentation","input":{"filename":"report.pptx","topic":"Monthly report","slides":[{"title":"Overview","content":"Key metrics and results"}]},"depends_on":[]}]"""

PLAN_MIXED = """You are a multi-step task planner. Coordinate multiple tools with clear data flow between steps. Return ONLY a JSON array.

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
    """Extract the JSON array from text.

    Uses bracket-depth tracking so that any prefix/suffix text (e.g. "Thinking: ...")
    is ignored.  If no matching ']' is found (truncated response) the substring from
    the first '[' to end-of-text is returned so the truncation-recovery paths can try
    to complete it.
    """
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    start = text.find('[')
    if start == -1:
        return text  # No array found; let the caller deal with it

    # Walk from '[' to its matching ']' respecting string literals and nesting
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if not in_string:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    # Truncated response — return from '[' to end so recovery can close it
    return text[start:]


def _extract_objects(text: str) -> list[str]:
    """Extract complete top-level {...} objects from arbitrary text."""
    objects: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '{':
            depth = 0
            in_string = False
            escape = False
            start = i
            j = i
            while j < n:
                c = text[j]
                if escape:
                    escape = False
                elif c == '\\' and in_string:
                    escape = True
                elif c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            objects.append(text[start:j + 1])
                            i = j
                            break
                j += 1
        i += 1
    return objects


def _build_steps(data: list) -> list[PlanStep]:
    """Convert a parsed JSON list into PlanStep objects."""
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


def _parse_steps(raw: str) -> list[PlanStep]:
    """Parse plan steps with progressive fallbacks for broken/truncated JSON.

    Fallback chain:
    1. Direct json.loads on extracted+sanitised JSON array.
    2. raw_decode — handles "Extra data" (trailing text after valid JSON).
    3. Truncation recovery — append closing suffixes and retry json.loads.
    4. Object-level regex — extract complete {...} objects from the raw text.
    """
    json_str = _extract_json(raw)
    json_str = _sanitize_json(json_str)

    # 1. Direct parse
    try:
        return _build_steps(json.loads(json_str))
    except json.JSONDecodeError as e:
        first_err = str(e).lower()

    # 2. raw_decode — succeeds on "Extra data" by stopping at the first valid value
    try:
        data, _ = json.JSONDecoder().raw_decode(json_str)
        if isinstance(data, list):
            return _build_steps(data)
    except (json.JSONDecodeError, KeyError):
        pass

    # 3. Truncation recovery — try common closing suffixes
    if any(kw in first_err for kw in ('unterminated', 'expecting', 'end of data')):
        for suffix in ('"}]', '"}}]', '}}]', '"}]}', ']'):
            try:
                return _build_steps(json.loads(json_str + suffix))
            except (json.JSONDecodeError, KeyError):
                continue

    # 4. Object-level regex — extract whatever complete step objects exist
    steps: list[PlanStep] = []
    for i, blob in enumerate(_extract_objects(raw)):
        try:
            obj = json.loads(_sanitize_json(blob))
            if 'tool' not in obj:
                continue
            inp = obj.get('input') or obj.get('params') or {}
            steps.append(PlanStep(
                id=obj.get('id', i + 1),
                tool=obj['tool'],
                description=obj.get('description', obj['tool']),
                input=inp,
                depends_on=obj.get('depends_on', []),
            ))
        except (json.JSONDecodeError, KeyError):
            continue
    if steps:
        return steps

    raise json.JSONDecodeError('Could not parse plan from response', json_str, 0)


class Planner:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def plan(
        self,
        task: str,
        memory_context: str = '',
        knowledge_rules: list[str] | None = None,
        task_context: str | None = None,
        user_context: str = '',
        task_type_hint: str | None = None,
    ) -> list[PlanStep]:
        # Use pre-built context from ContextBudget if provided; otherwise build it here
        if task_context is not None:
            full_task = task_context
        else:
            full_task = task
            if knowledge_rules:
                rules_str = "\n".join(f"- {r}" for r in knowledge_rules)
                full_task = f"{full_task}\n\n[Rules:\n{rules_str}]"
            if memory_context:
                full_task = f"{full_task}\n\n[Memory: {memory_context[:200]}]"

        # Phase 1: Classify task type (Haiku — fast, cheap).
        # task_type_hint can override the classifier (e.g. force "writing" for memory-answer tasks).
        if task_type_hint and task_type_hint in SPECIALIZED_PROMPTS:
            task_type = task_type_hint
        else:
            task_type = await self._classify(task, user_context)

        # Phase 2: Plan with specialized prompt
        use_react = _is_complex(task)

        if not use_react:
            steps = await self._specialized_plan(full_task, task_type, user_context)
            if steps:
                return steps
            # Fallback to generic fast plan
            steps = await self._fast_plan(full_task, user_context)
            if steps:
                return steps
            use_react = True

        return await self._react_plan(full_task, user_context)

    async def _classify(self, task: str, user_context: str = '') -> str:
        """Phase 1: Classify task type using Haiku (fast, ~100 tokens)."""
        sys = f"{user_context}\n\n{CLASSIFIER_PROMPT}" if user_context else CLASSIFIER_PROMPT
        try:
            response = await self.llm.complete(
                messages=[Message(role='user', content=task)],
                system=sys,
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

    async def _specialized_plan(self, task: str, task_type: str, user_context: str = '') -> list[PlanStep]:
        """Phase 2: Plan with specialized prompt (only relevant tools)."""
        prompt = SPECIALIZED_PROMPTS.get(task_type)
        if not prompt:
            return []
        sys = f"{user_context}\n\n{prompt}" if user_context else prompt

        for attempt in range(2):
            hint = '' if attempt == 0 else '\nReturn ONLY valid JSON array, no explanation.'
            response = await self.llm.complete(
                messages=[Message(role='user', content=task + hint)],
                system=sys,
                model_tier='balanced',
                max_tokens=4096,
            )
            try:
                return _parse_steps(response.content)
            except (json.JSONDecodeError, KeyError):
                continue
        return []

    async def _fast_plan(self, task: str, user_context: str = '') -> list[PlanStep]:
        """Fallback: generic plan with all tools."""
        sys = f"{user_context}\n\n{FAST_PROMPT}" if user_context else FAST_PROMPT
        for attempt in range(2):
            hint = '' if attempt == 0 else '\nReturn ONLY valid JSON array, no explanation.'
            response = await self.llm.complete(
                messages=[Message(role='user', content=task + hint)],
                system=sys,
                model_tier='balanced',
                max_tokens=4096,
            )
            try:
                return _parse_steps(response.content)
            except (json.JSONDecodeError, KeyError):
                continue
        return []

    async def _react_plan(self, task: str, user_context: str = '') -> list[PlanStep]:
        sys = f"{user_context}\n\n{REACT_PROMPT}" if user_context else REACT_PROMPT
        response = await self.llm.complete(
            messages=[Message(role='user', content=task)],
            system=sys,
            model_tier='balanced',
            max_tokens=4096,
        )
        try:
            return _parse_steps(response.content)
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f'Planner failed: {e}\nResponse: {response.content[:500]}')