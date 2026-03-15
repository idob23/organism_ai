"""AGENT-UX: ManageAgentsTool \u2014 natural language agent management.

Allows LLM to list/create/delete/delegate agents via tool calls
instead of requiring slash commands.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from src.organism.logging.error_handler import get_logger
from .base import BaseTool, ToolResult

if TYPE_CHECKING:
    from src.organism.agents.factory import AgentFactory
    from src.organism.agents.meta_orchestrator import MetaOrchestrator
    from src.organism.llm.base import LLMProvider

_log = get_logger("tools.manage_agents")


class ManageAgentsTool(BaseTool):

    def __init__(self) -> None:
        self._factory: AgentFactory | None = None
        self._llm: LLMProvider | None = None
        self._orchestrator: MetaOrchestrator | None = None

    # ── Dependency injection (setter pattern) ────────────────────────────

    def set_factory(self, factory: AgentFactory) -> None:
        self._factory = factory

    def set_llm(self, llm: LLMProvider) -> None:
        self._llm = llm

    def set_orchestrator(self, orchestrator: MetaOrchestrator) -> None:
        self._orchestrator = orchestrator

    # ── BaseTool interface ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "manage_agents"

    @property
    def description(self) -> str:
        return (
            "Manage AI agents \u2014 list available role templates, list created agents, "
            "create a new agent from a role template or free description, delete an agent, "
            "or delegate a task to a specific agent. "
            "Use when the user wants to see, create, remove, or assign work to specialized agents."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_templates",
                        "list_agents",
                        "create",
                        "delete",
                        "delegate",
                    ],
                    "description": "Action to perform",
                },
                "role_id": {
                    "type": "string",
                    "description": (
                        "Role template ID (for 'create' action). "
                        "Available roles can be found via 'list_templates'"
                    ),
                },
                "agent_name": {
                    "type": "string",
                    "description": (
                        "Name for the new agent (for 'create') "
                        "or agent name/ID to target (for 'delete', 'delegate')"
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Free-text description to create agent without template "
                        "(for 'create' when no role_id)"
                    ),
                },
                "task": {
                    "type": "string",
                    "description": "Task text to delegate (for 'delegate' action)",
                },
            },
            "required": ["action"],
        }

    # ── Execute ──────────────────────────────────────────────────────────

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        action = input.get("action", "")

        if action == "list_templates":
            return self._action_list_templates()
        elif action == "list_agents":
            return self._action_list_agents()
        elif action == "create":
            return await self._action_create(input)
        elif action == "delete":
            return self._action_delete(input)
        elif action == "delegate":
            return await self._action_delegate(input)
        else:
            return ToolResult(
                output="",
                error=f"Unknown action: {action}. "
                      "Valid: list_templates, list_agents, create, delete, delegate",
                exit_code=1,
            )

    # ── Action handlers ──────────────────────────────────────────────────

    def _action_list_templates(self) -> ToolResult:
        if not self._factory:
            return ToolResult(output="", error="AgentFactory not configured", exit_code=1)
        try:
            templates = self._factory.list_role_templates()
            if not templates:
                return ToolResult(
                    output="\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0445 "
                           "\u0448\u0430\u0431\u043b\u043e\u043d\u043e\u0432 "
                           "\u0440\u043e\u043b\u0435\u0439 \u043d\u0435\u0442.",
                    error="", exit_code=0,
                )
            lines = ["\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435 "
                     "\u0448\u0430\u0431\u043b\u043e\u043d\u044b \u0440\u043e\u043b\u0435\u0439:"]
            for t in templates:
                desc = t["description"][:100] if t["description"] else ""
                lines.append(
                    f"- {t['role_id']}: {t['name']}"
                    + (f" \u2014 {desc}" if desc else "")
                )
            return ToolResult(output="\n".join(lines), error="", exit_code=0)
        except Exception as e:
            _log.warning("list_templates failed: %s", e)
            return ToolResult(output="", error=str(e), exit_code=1)

    def _action_list_agents(self) -> ToolResult:
        if not self._factory:
            return ToolResult(output="", error="AgentFactory not configured", exit_code=1)
        try:
            agents = self._factory.list_created_agents()
            if not agents:
                return ToolResult(
                    output="\u0421\u043e\u0437\u0434\u0430\u043d\u043d\u044b\u0445 "
                           "\u0430\u0433\u0435\u043d\u0442\u043e\u0432 \u043d\u0435\u0442. "
                           "\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439\u0442\u0435 "
                           "action='create' \u0434\u043b\u044f \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f.",
                    error="", exit_code=0,
                )
            lines = ["\u0421\u043e\u0437\u0434\u0430\u043d\u043d\u044b\u0435 \u0430\u0433\u0435\u043d\u0442\u044b:"]
            for a in agents:
                lines.append(
                    f"- {a.get('name', '?')} (role: {a.get('role_id', '?')}, "
                    f"id: {a.get('agent_id', '?')})"
                )
            return ToolResult(output="\n".join(lines), error="", exit_code=0)
        except Exception as e:
            _log.warning("list_agents failed: %s", e)
            return ToolResult(output="", error=str(e), exit_code=1)

    async def _action_create(self, input: dict[str, Any]) -> ToolResult:
        if not self._factory:
            return ToolResult(output="", error="AgentFactory not configured", exit_code=1)
        if not self._llm:
            return ToolResult(output="", error="LLM not configured", exit_code=1)

        role_id = input.get("role_id", "")
        agent_name = input.get("agent_name", "")
        description = input.get("description", "")

        try:
            if role_id:
                if not agent_name:
                    agent_name = role_id.capitalize()
                result = await self._factory.create_from_role(
                    role_id, agent_name, self._llm,
                )
                if result is None:
                    templates = self._factory.list_role_templates()
                    available = ", ".join(t["role_id"] for t in templates)
                    return ToolResult(
                        output="",
                        error=f"\u0420\u043e\u043b\u044c '{role_id}' "
                              f"\u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430. "
                              f"\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u044b\u0435: {available}",
                        exit_code=1,
                    )
            elif description:
                if not agent_name:
                    agent_name = "Custom Agent"
                result = await self._factory.create_from_description(
                    description, agent_name, self._llm,
                )
                if result is None:
                    return ToolResult(
                        output="",
                        error="\u041e\u0448\u0438\u0431\u043a\u0430 "
                              "\u0441\u043e\u0437\u0434\u0430\u043d\u0438\u044f \u0430\u0433\u0435\u043d\u0442\u0430",
                        exit_code=1,
                    )
            else:
                return ToolResult(
                    output="",
                    error="Specify role_id or description to create an agent",
                    exit_code=1,
                )

            return ToolResult(
                output=(
                    f"\u0410\u0433\u0435\u043d\u0442 \u0441\u043e\u0437\u0434\u0430\u043d: "
                    f"{result['name']} ({result['role_id']})\n"
                    f"ID: {result['agent_id']}\n"
                    f"Personality: {result['personality_file']}"
                ),
                error="", exit_code=0,
            )
        except Exception as e:
            _log.warning("create agent failed: %s", e)
            return ToolResult(output="", error=str(e), exit_code=1)

    def _action_delete(self, input: dict[str, Any]) -> ToolResult:
        if not self._factory:
            return ToolResult(output="", error="AgentFactory not configured", exit_code=1)

        agent_ref = input.get("agent_name", "")
        if not agent_ref:
            return ToolResult(
                output="", error="agent_name is required for delete", exit_code=1,
            )

        try:
            # Try as agent_id first
            if self._factory.delete_agent(agent_ref):
                return ToolResult(
                    output=f"\u0410\u0433\u0435\u043d\u0442 \u0443\u0434\u0430\u043b\u0451\u043d: {agent_ref}",
                    error="", exit_code=0,
                )

            # Try by name
            agents = self._factory.list_created_agents()
            ref_lower = agent_ref.lower()
            for a in agents:
                if a.get("name", "").lower() == ref_lower:
                    aid = a.get("agent_id", "")
                    if aid and self._factory.delete_agent(aid):
                        return ToolResult(
                            output=f"\u0410\u0433\u0435\u043d\u0442 \u0443\u0434\u0430\u043b\u0451\u043d: "
                                   f"{a.get('name', agent_ref)} (ID: {aid})",
                            error="", exit_code=0,
                        )

            return ToolResult(
                output="",
                error=f"\u0410\u0433\u0435\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d: {agent_ref}",
                exit_code=1,
            )
        except Exception as e:
            _log.warning("delete agent failed: %s", e)
            return ToolResult(output="", error=str(e), exit_code=1)

    async def _action_delegate(self, input: dict[str, Any]) -> ToolResult:
        if not self._factory:
            return ToolResult(output="", error="AgentFactory not configured", exit_code=1)
        if not self._orchestrator:
            return ToolResult(output="", error="MetaOrchestrator not configured", exit_code=1)

        agent_ref = input.get("agent_name", "")
        task_text = input.get("task", "")

        if not agent_ref:
            return ToolResult(
                output="", error="agent_name is required for delegate", exit_code=1,
            )
        if not task_text:
            return ToolResult(
                output="", error="task is required for delegate", exit_code=1,
            )

        try:
            # Find agent by ID or name
            agents = self._factory.list_created_agents()
            agent_dict = None
            for a in agents:
                if a.get("agent_id") == agent_ref:
                    agent_dict = a
                    break
            if agent_dict is None:
                ref_lower = agent_ref.lower()
                for a in agents:
                    if a.get("name", "").lower() == ref_lower:
                        agent_dict = a
                        break

            if agent_dict is None:
                return ToolResult(
                    output="",
                    error=f"\u0410\u0433\u0435\u043d\u0442 \u043d\u0435 "
                          f"\u043d\u0430\u0439\u0434\u0435\u043d: {agent_ref}",
                    exit_code=1,
                )

            result = await self._orchestrator.run_as_agent(task_text, agent_dict)
            agent_name = agent_dict.get("name", agent_ref)

            if result.success:
                return ToolResult(
                    output=f"\u0410\u0433\u0435\u043d\u0442 {agent_name}:\n\n{result.output}",
                    error="", exit_code=0,
                )
            return ToolResult(
                output="",
                error=f"\u041e\u0448\u0438\u0431\u043a\u0430 "
                      f"\u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f: {result.error}",
                exit_code=1,
            )
        except Exception as e:
            _log.warning("delegate failed: %s", e)
            return ToolResult(output="", error=str(e), exit_code=1)
