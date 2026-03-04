"""Multi-agent orchestrator using a state machine workflow (Q-6.1).

The StateMachine graph replaces the old sequential loop:

    classify -> [researcher|writer|coder|analyst] -> evaluate -> done
                                                        |
                                                     (retry once if quality < 0.7)

Falls back to the legacy sequential method on any StateMachine-level error.
"""
import json
import re
import time
from dataclasses import dataclass, field

from src.organism.agents.base import BaseAgent, AgentResult
from src.organism.agents.coder import CoderAgent
from src.organism.agents.researcher import ResearcherAgent
from src.organism.agents.writer import WriterAgent
from src.organism.agents.analyst import AnalystAgent
from src.organism.core.state_machine import StateMachine, WorkflowState
from src.organism.llm.base import LLMProvider, Message
from src.organism.tools.registry import ToolRegistry
from src.organism.memory.manager import MemoryManager
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("orchestrator")

# ── Prompts ──────────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM = """You are an orchestrator that delegates tasks to specialized agents.

Available agents:
- "coder": writes and runs Python code, algorithms, scripts, data processing
- "researcher": searches internet for news, facts, current data
- "writer": generates texts, articles, emails, social media content
- "analyst": analyzes data, statistics, builds reports from data

Given a user task, decide:
1. Which agent(s) should handle it
2. If multiple agents needed  break into sub-tasks

Respond with ONLY a JSON array:
[
  {"agent": "researcher", "task": "specific sub-task for this agent"},
  {"agent": "writer", "task": "specific sub-task using researcher results"}
]

Rules:
- Use the MINIMUM number of agents needed
- If one agent can do it  use one
- For sequential tasks where result of one feeds another  list in order
- Be specific in sub-task descriptions
"""

# Classify prompt: return the *first* agent to invoke + full delegation plan.
# unicode: \u0417\u0430\u0434\u0430\u0447\u0430 = "\u0417\u0430\u0434\u0430\u0447\u0430" (task in Russian)
_CLASSIFY_SYSTEM = (
    "You classify tasks for a multi-agent system.\n"
    "Available agents: researcher, writer, coder, analyst.\n"
    "Return JSON: {\"first_agent\": \"<name>\", \"plan\": [{\"agent\": \"<name>\", \"task\": \"<subtask>\"}]}\n"
    "Rules:\n"
    "- first_agent = the agent that should run first\n"
    "- plan = ordered list of all agents needed (including first)\n"
    "- Minimum agents possible\n"
    "- Be specific in sub-task descriptions"
)


@dataclass
class OrchestratorResult:
    task: str
    success: bool
    output: str
    agent_results: list[AgentResult] = field(default_factory=list)
    duration: float = 0.0
    error: str = ""


class Orchestrator:

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        memory: MemoryManager | None = None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self._agents: dict[str, BaseAgent] = {
            "coder":      CoderAgent(llm, registry, memory),
            "researcher": ResearcherAgent(llm, registry, memory),
            "writer":     WriterAgent(llm, registry, memory),
            "analyst":    AnalystAgent(llm, registry, memory),
        }

    # ── Public API (unchanged signature) ─────────────────────────────────────

    async def run(self, task: str, verbose: bool = True) -> OrchestratorResult:
        start = time.time()

        if verbose:
            print(f"\n{'='*50}")
            print(f"Orchestrator: {task}")
            print(f"{'='*50}")

        try:
            result = await self._sm_run(task, verbose)
        except Exception as e:
            log_exception(_log, "StateMachine run failed, falling back to legacy", e)
            if verbose:
                print("  [WARN] StateMachine error -- falling back to legacy orchestrator")
            result = await self._legacy_run(task, verbose)

        duration = time.time() - start
        result.duration = duration

        # Save to memory
        if self.memory and result.success:
            tools_used = list({
                t
                for a in result.agent_results
                for t in self._agents.get(a.agent, CoderAgent(None, None)).tools
                if self._agents.get(a.agent)
            })
            try:
                await self.memory.on_task_end(
                    task, result.output, result.success, duration,
                    len(result.agent_results), tools_used,
                )
            except Exception:
                pass

        if verbose:
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s | Agents used: {len(result.agent_results)}")
            print(f"{'='*50}")

        return result

    # ── StateMachine-based workflow ──────────────────────────────────────────

    async def _sm_run(self, task: str, verbose: bool) -> OrchestratorResult:
        """Build and execute the state machine workflow."""

        # Shared mutable state for closures
        agent_results: list[AgentResult] = []
        context_parts: list[str] = []
        retry_count = 0

        # --- Node handlers -----------------------------------------------

        async def classify_handler(state: WorkflowState) -> tuple[str, str]:
            """Classify task and build execution plan. Returns first agent name."""
            if verbose:
                print("Routing task to agents...")

            plan: list[dict] = []
            first = ""

            # Try Haiku classify first (fast, cheap)
            try:
                resp = await self.llm.complete(
                    messages=[Message(role="user", content=task)],
                    system=_CLASSIFY_SYSTEM,
                    model_tier="fast",
                    max_tokens=300,
                )
                text = resp.content.strip()
                match = re.search(r"\{[\s\S]*\}", text)
                if match:
                    data = json.loads(match.group(0))
                else:
                    data = json.loads(text)

                plan = data.get("plan", [])
                first = data.get("first_agent", "")

                # Validate plan entries have valid agent names
                plan = [
                    p for p in plan
                    if isinstance(p, dict) and p.get("agent") in self._agents
                ]

                # If first_agent is valid but plan is empty, build plan from first
                if first in self._agents and not plan:
                    plan = [{"agent": first, "task": task}]
            except Exception:
                plan = []
                first = ""

            # Fallback: use Sonnet _route() if classify produced nothing
            if not plan:
                _log.info("Haiku classify failed or empty, falling back to _route()")
                plan = await self._route(task)
                first = plan[0]["agent"] if plan else "researcher"

            # Final safety: always have at least one step
            if not plan:
                plan = [{"agent": "researcher", "task": task}]
                first = "researcher"

            if not first or first not in self._agents:
                first = plan[0]["agent"]

            state.context["plan"] = plan
            state.context["plan_index"] = 0

            if verbose:
                for d in plan:
                    print(f"   [{d['agent']}] {d.get('task', '')[:80]}")

            return plan, first

        async def agent_handler(state: WorkflowState) -> tuple[AgentResult, str]:
            """Execute the current agent in the plan sequence."""
            plan = state.context.get("plan", [])
            idx = state.context.get("plan_index", 0)

            if idx >= len(plan):
                return AgentResult(agent="none", task=task, output="", success=False, duration=0, error="No more agents"), "done"

            step = plan[idx]
            agent_name = step["agent"]
            agent_task = step.get("task", task)

            # Inject previous context
            if context_parts:
                agent_task = f"{agent_task}\n\nContext from previous steps:\n{''.join(context_parts)}"

            if verbose:
                print(f"\n[{agent_name.upper()}] {step.get('task', task)[:60]}...")

            agent = self._agents.get(agent_name)
            if not agent:
                result = AgentResult(
                    agent=agent_name, task=agent_task, output="",
                    success=False, duration=0,
                    error=f"Unknown agent: {agent_name}",
                )
                agent_results.append(result)
                state.context["plan_index"] = idx + 1
                return result, self._next_condition(plan, idx)

            # Q-7.5: Cross-agent insights for this specific agent
            if self.memory:
                try:
                    insights = await self.memory.get_cross_agent_insights(agent_name, agent_task)
                    if insights:
                        cross_ctx = agent._format_cross_insights(insights)
                        if cross_ctx:
                            agent_task = f"{agent_task}\n\n{cross_ctx}"
                            if verbose:
                                print(f"  [cross-agent] {len(insights)} insights injected for {agent_name}")
                except Exception:
                    pass

            result = await agent.run(agent_task)
            agent_results.append(result)

            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {result.duration:.1f}s | {result.output[:100]}")

            if result.success:
                summary = await self._summarize_context(result.output, agent_name)
                context_parts.append(f"\n[{agent_name}] result:\n{summary}\n")
            elif verbose:
                print(f"  Error: {result.error[:100]}")

            state.context["plan_index"] = idx + 1
            state.context["last_agent"] = agent_name
            state.context["last_quality"] = getattr(result, "quality_score", 0.75 if result.success else 0.0)

            return result, self._next_condition(plan, idx)

        async def evaluate_handler(state: WorkflowState) -> tuple[str, str]:
            """Check quality of accumulated results; decide retry or done."""
            nonlocal retry_count
            successful = [r for r in agent_results if r.success]
            if not successful:
                return "no successful results", "done"

            # Approximate quality from agent results
            quality = state.context.get("last_quality", 0.75)
            if verbose:
                print(f"\n  [EVAL] quality ~{quality:.2f}, retries={retry_count}")

            if quality >= 0.7 or retry_count >= 1:
                return f"quality={quality:.2f}", "done"

            # Retry: re-run the last agent
            retry_count += 1
            last = state.context.get("last_agent", "")
            plan = state.context.get("plan", [])
            # Reset plan_index to re-run last agent
            idx = state.context.get("plan_index", 1) - 1
            if idx < 0:
                idx = 0
            state.context["plan_index"] = idx
            if verbose:
                print(f"  [EVAL] Retrying {last} (attempt {retry_count + 1})")

            # Return to agent node
            return f"retry {last}", "retry"

        async def done_handler(state: WorkflowState) -> tuple[str, str]:
            """Terminal node -- just returns the accumulated output."""
            output = "".join(context_parts).strip()
            return output, "end"

        # --- Build graph --------------------------------------------------

        sm = StateMachine()

        sm.add_node("classify", classify_handler, next_nodes={
            "researcher": "agent",
            "writer": "agent",
            "coder": "agent",
            "analyst": "agent",
            "default": "agent",
            "error": "done",
        })

        sm.add_node("agent", agent_handler, next_nodes={
            "next_agent": "agent",       # more agents in plan
            "evaluate": "evaluate",      # last agent done -> evaluate
            "done": "done",
            "default": "evaluate",
            "error": "evaluate",
        })

        sm.add_node("evaluate", evaluate_handler, next_nodes={
            "done": "done",
            "retry": "agent",
            "default": "done",
        })

        sm.add_node("done", done_handler)

        sm.set_start("classify")
        sm.add_end("done")

        # --- Execute ------------------------------------------------------

        ws = await sm.run(initial_context={"task": task}, max_steps=15)

        # Extract final output
        output = ws.context.get("done_result", "".join(context_parts).strip())
        successful = [r for r in agent_results if r.success]

        return OrchestratorResult(
            task=task,
            success=len(successful) > 0,
            output=output if isinstance(output, str) else str(output),
            agent_results=agent_results,
        )

    def _next_condition(self, plan: list[dict], current_idx: int) -> str:
        """Determine the transition condition after an agent step."""
        if current_idx + 1 < len(plan):
            return "next_agent"
        return "evaluate"

    # ── Legacy sequential method (fallback) ──────────────────────────────────

    async def _legacy_run(self, task: str, verbose: bool) -> OrchestratorResult:
        """Original sequential orchestrator -- used as fallback."""
        if verbose:
            print("Routing task to agents...")

        try:
            delegation = await self._route(task)
        except Exception as e:
            return OrchestratorResult(
                task=task, success=False, output="",
                error=f"Routing failed: {e}",
            )

        if verbose:
            for d in delegation:
                print(f"   [{d['agent']}] {d['task'][:80]}")

        agent_results: list[AgentResult] = []
        context = ""

        for d in delegation:
            agent_name = d["agent"]
            agent_task = d["task"]

            if context:
                agent_task = f"{agent_task}\n\nContext from previous steps:\n{context}"

            if verbose:
                print(f"\n[{agent_name.upper()}] {d['task'][:60]}...")

            agent = self._agents.get(agent_name)
            if not agent:
                agent_results.append(AgentResult(
                    agent=agent_name, task=agent_task,
                    output="", success=False, duration=0,
                    error=f"Unknown agent: {agent_name}",
                ))
                continue

            # Q-7.5: Cross-agent insights
            if self.memory:
                try:
                    insights = await self.memory.get_cross_agent_insights(agent_name, agent_task)
                    if insights:
                        cross_ctx = agent._format_cross_insights(insights)
                        if cross_ctx:
                            agent_task = f"{agent_task}\n\n{cross_ctx}"
                except Exception:
                    pass

            result = await agent.run(agent_task)
            agent_results.append(result)

            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {result.duration:.1f}s | {result.output[:100]}")

            if result.success:
                summary = await self._summarize_context(result.output, agent_name)
                context += f"\n[{agent_name}] result:\n{summary}\n"
            elif verbose:
                print(f"  Error: {result.error[:100]}")

        successful = [r for r in agent_results if r.success]
        return OrchestratorResult(
            task=task,
            success=len(successful) > 0,
            output=context.strip(),
            agent_results=agent_results,
        )

    # ── Shared helpers ───────────────────────────────────────────────────────

    async def _summarize_context(self, output: str, agent_name: str) -> str:
        """Summarize agent output via Haiku (~100 tokens). Falls back to truncated raw output."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=output[:3000])],
                system=(
                    f"Summarize the key facts and results from this {agent_name} output "
                    f"in 3-5 bullet points. "
                    f"Keep all numbers, names, dates exactly as-is. "
                    f"Output only the bullet point summary, no intro."
                ),
                model_tier="fast",
                max_tokens=150,
            )
            summary = resp.content.strip()
            return summary if summary else output[:800]
        except Exception:
            return output[:800]

    async def _route(self, task: str) -> list[dict]:
        response = await self.llm.complete(
            messages=[Message(role="user", content=task)],
            system=ORCHESTRATOR_SYSTEM,
            model_tier="balanced",
        )
        text = response.content.strip()

        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            return json.loads(match.group(0))

        if text.startswith("["):
            return json.loads(text)

        raise ValueError(f"Could not parse routing response: {text[:200]}")
