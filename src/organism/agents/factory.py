"""Agent Factory — role template management and agent configuration (Q-9.2, Q-9.3).

Reads role templates from config/roles/*.md, manages created agent
configurations in config/agents/*.json, and auto-generates personality
files from role descriptions via LLM.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.organism.llm.base import LLMProvider, Message
from src.organism.logging.error_handler import get_logger

_log = get_logger("agent.factory")

# Resolve project root (4 levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class AgentFactory:
    """Manages role templates and agent configurations on disk."""

    ROLES_DIR = _PROJECT_ROOT / "config" / "roles"
    AGENTS_DIR = _PROJECT_ROOT / "config" / "agents"
    PERSONALITY_DIR = _PROJECT_ROOT / "config" / "personality"

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

    # ── Agent creation (Q-9.3) ──────────────────────────────────────────

    async def create_from_role(
        self, role_id: str, agent_name: str, llm: LLMProvider,
    ) -> dict | None:
        """Create an agent from a role template, generating personality via LLM."""
        role_template = self.get_role_template(role_id)
        if role_template is None:
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        agent_id = f"{role_id}_{ts}"

        personality_md = await self._generate_personality(
            role_template, agent_name, llm,
        )
        personality_rel = f"config/personality/{agent_id}.md"

        # Extract tools from role template
        tools_section = self._extract_section(role_template, "Tools") or ""
        tools = [
            line.lstrip("- ").strip()
            for line in tools_section.splitlines()
            if line.strip().startswith("-")
        ]

        agent_data = {
            "agent_id": agent_id,
            "name": agent_name,
            "role_id": role_id,
            "personality_file": personality_rel,
            "tools": tools,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        try:
            self.PERSONALITY_DIR.mkdir(parents=True, exist_ok=True)
            (self.PERSONALITY_DIR / f"{agent_id}.md").write_text(
                personality_md, encoding="utf-8",
            )
        except Exception as exc:
            _log.warning(f"Failed to write personality file: {exc}")

        try:
            self.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
            (self.AGENTS_DIR / f"{agent_id}.json").write_text(
                json.dumps(agent_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            _log.warning(f"Failed to write agent config: {exc}")

        return agent_data

    async def create_from_description(
        self, description: str, agent_name: str, llm: LLMProvider,
    ) -> dict:
        """Create an agent from a free-text description (no role template)."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        agent_id = f"custom_{ts}"

        personality_md = await self._generate_personality(
            description, agent_name, llm,
        )
        personality_rel = f"config/personality/{agent_id}.md"

        agent_data = {
            "agent_id": agent_id,
            "name": agent_name,
            "role_id": "custom",
            "personality_file": personality_rel,
            "tools": [],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

        try:
            self.PERSONALITY_DIR.mkdir(parents=True, exist_ok=True)
            (self.PERSONALITY_DIR / f"{agent_id}.md").write_text(
                personality_md, encoding="utf-8",
            )
        except Exception as exc:
            _log.warning(f"Failed to write personality file: {exc}")

        try:
            self.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
            (self.AGENTS_DIR / f"{agent_id}.json").write_text(
                json.dumps(agent_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            _log.warning(f"Failed to write agent config: {exc}")

        return agent_data

    async def _generate_personality(
        self, context: str, agent_name: str, llm: LLMProvider,
    ) -> str:
        """Generate personality markdown via Haiku, with fallback template."""
        system_prompt = (
            "\u0422\u044b \u0441\u043e\u0437\u0434\u0430\u0451\u0448\u044c "
            "\u0444\u0430\u0439\u043b \u043a\u043e\u043d\u0444\u0438\u0433\u0443\u0440\u0430\u0446\u0438\u0438 "
            "\u043b\u0438\u0447\u043d\u043e\u0441\u0442\u0438 AI-\u0430\u0433\u0435\u043d\u0442\u0430.\n"
            f"\u0418\u043c\u044f \u0430\u0433\u0435\u043d\u0442\u0430: {agent_name}\n"
            f"\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0440\u043e\u043b\u0438:\n{context}\n\n"
            "\u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0439 PERSONALITY.md "
            "\u0432 \u0442\u043e\u0447\u043d\u043e \u0442\u0430\u043a\u043e\u043c \u0444\u043e\u0440\u043c\u0430\u0442\u0435:\n\n"
            f"# Personality: {agent_name}\n\n"
            "## Style\n"
            "[\u0441\u0442\u0438\u043b\u044c \u043e\u0431\u0449\u0435\u043d\u0438\u044f "
            "\u0430\u0433\u0435\u043d\u0442\u0430, 3-5 \u043f\u0443\u043d\u043a\u0442\u043e\u0432]\n\n"
            "## Terminology\n"
            "[\u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0435 \u0442\u0435\u0440\u043c\u0438\u043d\u044b "
            "\u043f\u0440\u0435\u0434\u043c\u0435\u0442\u043d\u043e\u0439 \u043e\u0431\u043b\u0430\u0441\u0442\u0438, "
            "5-8 \u043f\u0430\u0440 '\u0442\u0435\u0440\u043c\u0438\u043d: \u043e\u0431\u044a\u044f\u0441\u043d\u0435\u043d\u0438\u0435']\n\n"
            "## Escalation\n"
            "[\u043a\u043e\u0433\u0434\u0430 \u043f\u0440\u043e\u0441\u0438\u0442\u044c "
            "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435 "
            "\u0438\u043b\u0438 \u044d\u0441\u043a\u0430\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c, "
            "2-3 \u043f\u0443\u043d\u043a\u0442\u0430]\n\n"
            "## Report Preferences\n"
            "[\u043f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0435\u043d\u0438\u044f \u043f\u043e "
            "\u0444\u043e\u0440\u043c\u0430\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044e "
            "\u0438 \u0441\u0442\u0438\u043b\u044e \u043e\u0442\u0447\u0451\u0442\u043e\u0432, "
            "2-3 \u043f\u0443\u043d\u043a\u0442\u0430]\n\n"
            "\u041e\u0442\u0432\u0435\u0447\u0430\u0439 \u0422\u041e\u041b\u042c\u041a\u041e "
            "\u0441\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u044b\u043c \u0444\u0430\u0439\u043b\u0430, "
            "\u0431\u0435\u0437 \u043e\u0431\u044a\u044f\u0441\u043d\u0435\u043d\u0438\u0439."
        )
        try:
            resp = await llm.complete(
                messages=[Message(role="user", content=context[:3000])],
                system=system_prompt,
                model_tier="fast",
                max_tokens=500,
            )
            text = resp.content.strip()
            if text and "## Style" in text:
                return text
        except Exception as exc:
            _log.warning(f"LLM personality generation failed: {exc}")

        # Fallback: basic template without LLM
        return (
            f"# Personality: {agent_name}\n\n"
            "## Style\n"
            f"\u041f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0439 "
            "\u0441\u0442\u0438\u043b\u044c \u043e\u0431\u0449\u0435\u043d\u0438\u044f.\n\n"
            "## Terminology\n"
            "\u0422\u0435\u0440\u043c\u0438\u043d\u043e\u043b\u043e\u0433\u0438\u044f "
            "\u0430\u0434\u0430\u043f\u0442\u0438\u0440\u0443\u0435\u0442\u0441\u044f "
            "\u043f\u043e\u0434 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442.\n\n"
            "## Escalation\n"
            "- \u041a\u0440\u0438\u0442\u0438\u0447\u0435\u0441\u043a\u0438\u0435 "
            "\u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044f \u2014 "
            "\u0437\u0430\u043f\u0440\u0430\u0448\u0438\u0432\u0430\u0442\u044c "
            "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435\n\n"
            "## Report Preferences\n"
            "\u0421\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 "
            "\u043e\u0442\u0447\u0451\u0442\u044b \u0441 \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430\u043c\u0438.\n"
        )

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
