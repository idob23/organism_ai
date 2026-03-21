"""Meta-orchestrator — routes tasks to built-in or custom agents (Q-9.4).

Wraps the base Orchestrator and adds routing to custom agents created
via AgentFactory.  When no custom agents exist, delegates directly to the
base orchestrator with zero overhead (no LLM call).
"""

import json
import re
import time
from pathlib import Path

from src.organism.agents.orchestrator import Orchestrator, OrchestratorResult
from src.organism.agents.factory import AgentFactory
from src.organism.llm.base import LLMProvider, Message
from src.organism.logging.error_handler import get_logger

_log = get_logger("meta_orchestrator")

MAX_DELEGATE_DEPTH = 3

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_ROUTER_SYSTEM = (
    "\u0422\u044b \u0440\u043e\u0443\u0442\u0435\u0440 \u0437\u0430\u0434\u0430\u0447. "
    "\u0412\u044b\u0431\u0435\u0440\u0438 \u043a\u0442\u043e \u043b\u0443\u0447\u0448\u0435 "
    "\u0441\u043f\u0440\u0430\u0432\u0438\u0442\u0441\u044f \u0441 \u0437\u0430\u0434\u0430\u0447\u0435\u0439.\n"
    "\u0412\u0430\u0440\u0438\u0430\u043d\u0442\u044b:\n"
    "- base: \u0432\u0441\u0442\u0440\u043e\u0435\u043d\u043d\u044b\u0439 "
    "\u043e\u0440\u043a\u0435\u0441\u0442\u0440\u0430\u0442\u043e\u0440 "
    "(\u043a\u043e\u0434, \u0438\u0441\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u043d\u0438\u044f, "
    "\u0442\u0435\u043a\u0441\u0442\u044b, \u0430\u043d\u0430\u043b\u0438\u0437 \u0434\u0430\u043d\u043d\u044b\u0445)\n"
    "{agent_list}\n"
    "\u041e\u0442\u0432\u0435\u0442\u044c \u0422\u041e\u041b\u042c\u041a\u041e JSON: "
    '{"choice": "base"} \u0438\u043b\u0438 {"choice": "<agent_id>"}'
)


class MetaOrchestrator:
    """Routes tasks to the best available agent (built-in or custom)."""

    def __init__(
        self,
        base_orchestrator: Orchestrator,
        llm: LLMProvider,
        factory: AgentFactory,
    ) -> None:
        self.base = base_orchestrator
        self.llm = llm
        self.factory = factory
        self._loop = None  # injected via set_loop()
        self._current_depth: int = 0

    def set_loop(self, loop) -> None:
        """Inject CoreLoop reference after construction (avoids circular dep)."""
        self._loop = loop

    async def run(self, task: str, verbose: bool = True) -> OrchestratorResult:
        """Unified entry point — compatible with Orchestrator.run()."""
        agents = self.factory.list_created_agents()

        # Zero overhead path: no custom agents → base orchestrator directly
        if not agents:
            return await self.base.run(task, verbose=verbose)

        # Route via Haiku
        choice = await self._route_choice(task, agents)

        if choice == "base":
            return await self.base.run(task, verbose=verbose)

        # Find matching custom agent
        agent_dict = None
        for a in agents:
            if a.get("agent_id") == choice:
                agent_dict = a
                break

        if agent_dict is None:
            _log.warning(
                f"Router chose unknown agent_id={choice!r}, falling back to base"
            )
            return await self.base.run(task, verbose=verbose)

        if verbose:
            print(
                f"\n[META] Routing to custom agent: "
                f"{agent_dict.get('name', choice)} ({agent_dict.get('role_id', '?')})"
            )

        return await self.run_as_agent(task, agent_dict, verbose=verbose)

    async def run_as_agent(
        self, task: str, agent: dict, verbose: bool = True,
    ) -> OrchestratorResult:
        """Run a task as a custom agent via CoreLoop with personality context."""
        # FIX-95b: Prevent infinite delegate recursion
        if self._current_depth >= MAX_DELEGATE_DEPTH:
            _log.warning(
                "Delegate depth limit reached (%d), refusing delegation",
                self._current_depth,
            )
            return OrchestratorResult(
                task=task,
                success=False,
                output="",
                duration=0.0,
                error=(
                    "\u0414\u043e\u0441\u0442\u0438\u0433\u043d\u0443\u0442 "
                    "\u043b\u0438\u043c\u0438\u0442 \u0433\u043b\u0443\u0431\u0438\u043d\u044b "
                    "\u0434\u0435\u043b\u0435\u0433\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f "
                    f"({MAX_DELEGATE_DEPTH}). "
                    "\u0417\u0430\u0434\u0430\u0447\u0430 \u0441\u043b\u0438\u0448\u043a\u043e\u043c "
                    "\u0433\u043b\u0443\u0431\u043e\u043a\u043e \u0432\u043b\u043e\u0436\u0435\u043d\u0430."
                ),
            )

        self._current_depth += 1
        start = time.time()
        try:
            # Load personality file
            personality_content = ""
            pfile = agent.get("personality_file", "")
            if pfile:
                ppath = _PROJECT_ROOT / pfile
                try:
                    personality_content = ppath.read_text(encoding="utf-8")
                except Exception:
                    pass

            agent_name = agent.get("name", agent.get("agent_id", "agent"))
            role_id = agent.get("role_id", "custom")

            # FIX-63: Inject personality via system prompt, not task text
            # This keeps memory/cache clean — only the real task is stored
            agent_context = (
                f"[\u0410\u0433\u0435\u043d\u0442: {agent_name}, "
                f"\u0420\u043e\u043b\u044c: {role_id}]\n"
            )
            if personality_content:
                agent_context += (
                    "\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u0438 "
                    "\u043f\u043e \u0441\u0442\u0438\u043b\u044e "
                    "\u0438 \u043f\u043e\u0432\u0435\u0434\u0435\u043d\u0438\u044e:\n"
                    f"{personality_content}"
                )

            if self._loop is not None:
                result = await self._loop.run(
                    task, verbose=verbose,
                    skip_orchestrator=True,
                    extra_system_context=agent_context,
                )
                return OrchestratorResult(
                    task=task,
                    success=result.success,
                    output=result.output,
                    duration=time.time() - start,
                    error=result.error,
                )

            # Fallback: no loop reference, use base orchestrator
            enhanced_task = f"{agent_context}\n\n\u0417\u0430\u0434\u0430\u0447\u0430: {task}"
            return await self.base.run(enhanced_task, verbose=verbose)

        except Exception as exc:
            _log.warning(f"run_as_agent failed: {exc}")
            return OrchestratorResult(
                task=task,
                success=False,
                output="",
                duration=time.time() - start,
                error=str(exc),
            )
        finally:
            self._current_depth -= 1

    async def _route_choice(self, task: str, agents: list[dict]) -> str:
        """Ask Haiku which agent should handle the task. Returns agent_id or 'base'."""
        agent_lines = []
        for a in agents:
            agent_id = a.get("agent_id", "?")
            name = a.get("name", "?")
            role_id = a.get("role_id", "?")
            # FIX-62: include role description so Haiku can route intelligently
            description = ""
            tmpl = self.factory.get_role_template(role_id)
            if tmpl:
                desc_match = re.search(
                    r"^## Description\s*\n(.*?)(?=^## |\Z)",
                    tmpl, re.MULTILINE | re.DOTALL,
                )
                if desc_match:
                    description = desc_match.group(1).strip()[:100]
            suffix = f" \u2014 {description}" if description else ""
            agent_lines.append(f"- {agent_id}: {name} ({role_id}){suffix}")

        agent_list = "\n".join(agent_lines)
        system = _ROUTER_SYSTEM.replace("{agent_list}", agent_list)

        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=task[:2000])],
                system=system,
                model_tier="fast",
                max_tokens=30,
            )
            text = resp.content.strip()
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                data = json.loads(match.group(0))
                choice = data.get("choice", "base")
                if isinstance(choice, str) and choice:
                    return choice
        except Exception as exc:
            _log.warning(f"Route choice failed: {exc}")

        return "base"
