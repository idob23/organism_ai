"""Q-6.4: Configurable personality per artel.

Loads PERSONALITY.md with communication style, terminology, escalation rules.
Injects into system prompts so LLM adapts to each artel's preferences.

CAPABILITY-1: YAML front-matter support for tool filtering.
Files may start with ``---`` delimited YAML block containing allowed_tools
and denied_tools lists. The rest of the file is the markdown prompt.
"""
from __future__ import annotations

from pathlib import Path

from src.organism.logging.error_handler import get_logger

_log = get_logger("core.personality")

_PERSONALITY_DIR = Path("config/personality")


class PersonalityConfig:
    """Per-artel personality loaded from a markdown file."""

    def __init__(self, artel_id: str = "default") -> None:
        self.artel_id = artel_id
        self.raw_content: str = ""
        self.sections: dict[str, str] = {}
        self.style: dict[str, str] = {}
        self.terminology: dict[str, str] = {}
        self.escalation: list[str] = []
        self.report_prefs: dict[str, str] = {}
        self.working_hours: dict[str, str] = {}
        # CAPABILITY-1: tool access control
        self.allowed_tools: set[str] | None = None  # None = all allowed
        self.denied_tools: set[str] = set()

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is permitted by this personality.

        denied_tools is checked first and overrides allowed_tools.
        If allowed_tools is None (permissive), all non-denied tools pass.
        """
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def load(self, path: str | None = None) -> None:
        """Load personality from markdown file.

        Falls back to default.md if artel-specific file not found.
        Parses optional YAML front-matter (between --- delimiters) for
        allowed_tools / denied_tools.
        """
        if path:
            filepath = Path(path)
        else:
            filepath = _PERSONALITY_DIR / f"{self.artel_id}.md"

        if not filepath.exists():
            fallback = _PERSONALITY_DIR / "default.md"
            if fallback.exists():
                filepath = fallback
                _log.info(
                    "personality.fallback: %s not found, using default.md",
                    self.artel_id,
                )
            else:
                _log.warning("personality.not_found: no personality files found")
                return

        try:
            full_text = filepath.read_text(encoding="utf-8")
        except Exception as exc:
            _log.error("personality.read_error: %s: %s", type(exc).__name__, exc)
            return

        # CAPABILITY-1: Parse YAML front-matter if present
        self.raw_content = self._parse_front_matter(full_text)

        self._parse_sections(self.raw_content)
        _log.info(
            "personality.loaded: %s (%d terms, %d escalation rules)",
            self.artel_id, len(self.terminology), len(self.escalation),
        )

    def _parse_front_matter(self, text: str) -> str:
        """Extract YAML front-matter and return remaining markdown content."""
        if not text.startswith("---"):
            return text

        # Find closing ---
        end_idx = text.find("\n---", 3)
        if end_idx == -1:
            return text  # no closing delimiter, treat as plain markdown

        yaml_block = text[3:end_idx].strip()
        remaining = text[end_idx + 4:].lstrip("\n")

        try:
            import yaml
            data = yaml.safe_load(yaml_block)
        except Exception as exc:
            _log.warning("personality.yaml_parse_error: %s", exc)
            return text  # fallback: treat entire file as markdown

        if not isinstance(data, dict):
            return remaining

        # allowed_tools
        raw_allowed = data.get("allowed_tools")
        if isinstance(raw_allowed, list):
            self.allowed_tools = set(raw_allowed)
        # else stays None (permissive)

        # denied_tools
        raw_denied = data.get("denied_tools")
        if isinstance(raw_denied, list):
            self.denied_tools = set(raw_denied)

        return remaining

    def _parse_sections(self, content: str) -> None:
        """Parse markdown into sections by ## headings."""
        current_section = ""
        section_lines: dict[str, list[str]] = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                current_section = stripped[3:].strip().lower()
                section_lines.setdefault(current_section, [])
                continue
            if stripped.startswith("# "):
                continue
            if current_section and stripped:
                section_lines.setdefault(current_section, []).append(stripped)

            if not stripped:
                continue

            if stripped.startswith("- "):
                item = stripped[2:].strip()
            else:
                continue

            if current_section == "communication style":
                k, _, v = item.partition(":")
                if v:
                    self.style[k.strip().lower()] = v.strip()
            elif current_section == "terminology":
                k, _, v = item.partition(":")
                if v:
                    self.terminology[k.strip().lower()] = v.strip()
            elif current_section == "escalation rules":
                self.escalation.append(item)
            elif current_section == "report preferences":
                k, _, v = item.partition(":")
                if v:
                    self.report_prefs[k.strip().lower()] = v.strip()
            elif current_section == "working hours":
                k, _, v = item.partition(":")
                if v:
                    self.working_hours[k.strip().lower()] = v.strip()

        self.sections = {k: "\n".join(v) for k, v in section_lines.items()}

    def get_system_prompt_addition(self) -> str:
        """Return string to inject into LLM system prompt."""
        if not self.raw_content:
            return ""
        # "\n\n--- \u041b\u0438\u0447\u043d\u043e\u0441\u0442\u044c ---\n"
        return (
            "\n\n--- "
            "\u041b\u0438\u0447\u043d\u043e\u0441\u0442\u044c"
            " ---\n"
            f"{self.raw_content}\n"
        )

    def get_section(self, name: str) -> str:
        """Return a parsed section by name, or empty string."""
        return self.sections.get(name.lower(), "")

    def get_term(self, key: str) -> str:
        """Look up a terminology entry. Returns key itself if not found."""
        return self.terminology.get(key.lower(), key)
