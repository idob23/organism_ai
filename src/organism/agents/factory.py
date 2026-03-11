"""Agent Factory — role template management and agent configuration (Q-9.2).

Reads role templates from config/roles/*.md and manages created agent
configurations in config/agents/*.json.  Pure file-based, no LLM calls.
"""

import json
import re
from pathlib import Path

from src.organism.logging.error_handler import get_logger

_log = get_logger("agent.factory")

# Resolve project root (4 levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class AgentFactory:
    """Manages role templates and agent configurations on disk."""

    ROLES_DIR = _PROJECT_ROOT / "config" / "roles"
    AGENTS_DIR = _PROJECT_ROOT / "config" / "agents"

    # ── Role templates ────────────────────────────────────────────────────

    def list_role_templates(self) -> list[dict]:
        """Return [{role_id, name, description}] from config/roles/*.md."""
        if not self.ROLES_DIR.is_dir():
            return []
        result = []
        for path in sorted(self.ROLES_DIR.glob("*.md")):
            role_id = path.stem
            content = self._read_file(path)
            if content is None:
                continue
            name = self._extract_section(content, "Role") or role_id
            description = self._extract_section(content, "Description") or ""
            result.append({
                "role_id": role_id,
                "name": name.strip(),
                "description": description.strip(),
            })
        return result

    def get_role_template(self, role_id: str) -> str | None:
        """Return raw markdown content of config/roles/{role_id}.md."""
        path = self.ROLES_DIR / f"{role_id}.md"
        return self._read_file(path)

    # ── Created agents ────────────────────────────────────────────────────

    def list_created_agents(self) -> list[dict]:
        """Return list of agent configs from config/agents/*.json."""
        if not self.AGENTS_DIR.is_dir():
            return []
        result = []
        for path in sorted(self.AGENTS_DIR.glob("*.json")):
            data = self._read_json(path)
            if data is not None:
                result.append(data)
        return result

    def get_agent(self, agent_id: str) -> dict | None:
        """Return agent config from config/agents/{agent_id}.json."""
        path = self.AGENTS_DIR / f"{agent_id}.json"
        return self._read_json(path)

    def delete_agent(self, agent_id: str) -> bool:
        """Delete agent config and its personality file. Returns True if deleted."""
        path = self.AGENTS_DIR / f"{agent_id}.json"
        if not path.is_file():
            return False
        # Remove personality file if referenced
        try:
            data = self._read_json(path)
            if data and data.get("personality_file"):
                personality_path = _PROJECT_ROOT / data["personality_file"]
                if personality_path.is_file():
                    personality_path.unlink()
                    _log.info(f"Deleted personality file: {data['personality_file']}")
        except Exception:
            pass
        try:
            path.unlink()
            _log.info(f"Deleted agent config: {agent_id}")
            return True
        except Exception as exc:
            _log.warning(f"Failed to delete agent {agent_id}: {exc}")
            return False

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _read_file(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _extract_section(content: str, section_name: str) -> str | None:
        """Extract text under ## {section_name} until next ## or EOF."""
        pattern = rf"^## {re.escape(section_name)}\s*\n(.*?)(?=^## |\Z)"
        match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return None
