"""SKILL-1: SkillMatcher \u2014 selects relevant skill files for a task.

Haiku analyzes the task and picks which config/skills/*.md files to load.
Content is injected into _handle_conversation system prompt as skill_context.
Graceful degradation: any failure returns empty string (no skill context).
"""
from pathlib import Path
from src.organism.llm.base import LLMProvider, Message
from src.organism.logging.error_handler import get_logger

_log = get_logger("core.skill_matcher")

SKILLS_DIR = Path("config/skills")

SKILL_SELECT_PROMPT = (
    "You select relevant skill files for a task. "
    "Available skills: {available}. "
    "Return ONLY a JSON array of filenames that are relevant. "
    "Example: [\"excel.md\"] or [\"docx.md\", \"charts.md\"] or [] if none relevant.\n"
    "Select a skill when the task requires creating a specific file type "
    "OR when the result is structured data best presented in that format:\n"
    "- Table, comparison, list of items with attributes \u2192 excel.md\n"
    "- Document, instruction, report, memo \u2192 docx.md\n"
    "- Chart, graph, visualization \u2192 charts.md\n"
    "- PDF report, certificate \u2192 pdf.md\n"
    "Return [] for search, conversation, simple calculations, or Q&A tasks."
)


class SkillMatcher:

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def _available_skills(self) -> list[str]:
        if not SKILLS_DIR.exists():
            return []
        return [f.name for f in SKILLS_DIR.glob("*.md")]

    def _load_skill(self, filename: str) -> str:
        path = SKILLS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    async def get_skill_context(self, task: str) -> str:
        """Return relevant skill content for injection into system prompt."""
        try:
            available = self._available_skills()
            if not available:
                return ""

            import json, re
            system = SKILL_SELECT_PROMPT.format(available=", ".join(available))
            response = await self.llm.complete(
                messages=[Message(role="user", content=task[:300])],
                system=system,
                model_tier="fast",
                max_tokens=60,
            )
            text = response.content.strip()
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if not match:
                return ""
            selected = json.loads(match.group(0))
            if not selected:
                return ""

            parts = []
            for fname in selected[:2]:  # max 2 skills at once
                if fname in available:
                    content = self._load_skill(fname)
                    if content:
                        parts.append(content)

            return "\n\n".join(parts) if parts else ""

        except Exception as e:
            _log.debug(f"SkillMatcher failed (graceful): {e}")
            return ""
