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

# ---- Phase 2: Universal planner prompt (Q-10.1) ----

PLAN_UNIVERSAL = """You are an autonomous task planner. Given a task, choose the right tools and sequence to accomplish it. Think about what the task actually needs, not what category it belongs to. Return ONLY a JSON array of steps.

AVAILABLE TOOLS:
- web_search: search internet. input: {"query": "...", "max_results": 5}
- web_fetch: fetch specific URL. input: {"url": "https://...", "max_chars": 3000}
- text_writer: write and save long text/documents. input: {"prompt": "...", "filename": "file.md"}
- code_executor: run Python in Docker sandbox. input: {"code": "python code", "domains": []}
- file_manager: read/write short plain text files. input: {"action": "read|write", "path": "file.txt", "content": "..."}
- pptx_creator: create PowerPoint. input: {"filename": "name.pptx", "topic": "...", "slides": [...]}
- pdf_tool: create or read PDF. input: {"action": "create|read", "filename": "doc.pdf", "content": "...", "title": "..."}
- confirm_with_user: ask human approval before critical actions. input: {"description": "..."}
- duplicate_finder: find semantic duplicates in entity lists. input: {"entities": [...], "entity_type": "..."}
- delegate_to_agent: delegate to specialized agent. input: {"peer_name": "...", "task": "..."}

RULES:
- Pick tools based on what the task actually needs, not a template
- For data/calculations: code_executor with real working Python
- For documents/reports: text_writer or pdf_tool
- For current information: web_search first, never web_fetch as first step
- For write operations to external systems: confirm_with_user first
- For email: confirm_with_user required before mcp_email_send_email
- Use {{step_N_output}} to pass results between steps
- Maximum 10 steps
- All outputs in Russian

EXAMPLES:
Research + write: [web_search] \u2192 [text_writer with {{step_1_output}}]
Calculate: [code_executor]
Compare documents: [pdf_tool read doc1] \u2192 [pdf_tool read doc2] \u2192 [text_writer compare]"""

# Valid task types for classifier
VALID_TASK_TYPES = {"writing", "code", "data", "research", "presentation", "mixed"}


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
        # Use pre-built task_context if provided; otherwise build it here
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
        if task_type_hint and task_type_hint in VALID_TASK_TYPES:
            task_type = task_type_hint
        else:
            task_type = await self._classify(task, user_context)

        # Phase 2: Universal planner (Q-10.1)
        use_react = _is_complex(task)

        if not use_react:
            steps = await self._universal_plan(full_task, user_context)
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
                if task_type in VALID_TASK_TYPES:
                    return task_type
        except Exception:
            pass
        return 'mixed'

    async def _universal_plan(self, task: str, user_context: str = '') -> list[PlanStep]:
        """Phase 2: Plan with universal prompt (Q-10.1)."""
        sys = f"{user_context}\n\n{PLAN_UNIVERSAL}" if user_context else PLAN_UNIVERSAL

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