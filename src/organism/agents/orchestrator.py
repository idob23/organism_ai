import json
import re
import time
from dataclasses import dataclass, field

from src.organism.agents.base import BaseAgent, AgentResult
from src.organism.agents.coder import CoderAgent
from src.organism.agents.researcher import ResearcherAgent
from src.organism.agents.writer import WriterAgent
from src.organism.agents.analyst import AnalystAgent
from src.organism.llm.base import LLMProvider, Message
from src.organism.tools.registry import ToolRegistry
from src.organism.memory.manager import MemoryManager


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
            "coder":      CoderAgent(llm, registry),
            "researcher": ResearcherAgent(llm, registry),
            "writer":     WriterAgent(llm, registry),
            "analyst":    AnalystAgent(llm, registry),
        }

    async def run(self, task: str, verbose: bool = True) -> OrchestratorResult:
        start = time.time()

        if verbose:
            print(f"\n{'='*50}")
            print(f"Orchestrator: {task}")
            print(f"{'='*50}")

        # Step 1: decide which agents to use
        if verbose:
            print("Routing task to agents...")

        try:
            delegation = await self._route(task)
        except Exception as e:
            return OrchestratorResult(
                task=task, success=False, output="",
                error=f"Routing failed: {e}", duration=time.time() - start,
            )

        if verbose:
            for d in delegation:
                print(f"   [{d['agent']}] {d['task'][:80]}")

        # Step 2: execute agents sequentially, passing results forward
        agent_results: list[AgentResult] = []
        context = ""

        for d in delegation:
            agent_name = d["agent"]
            agent_task = d["task"]

            # Inject previous results into task context
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

            result = await agent.run(agent_task)
            agent_results.append(result)

            if verbose:
                status = "OK" if result.success else "FAIL"
                print(f"  [{status}] {result.duration:.1f}s | {result.output[:100]}")

            if result.success:
                context += f"\n[{agent_name}] result:\n{result.output[:800]}\n"
            else:
                if verbose:
                    print(f"  Error: {result.error[:100]}")

        # Step 3: compile final output
        successful = [r for r in agent_results if r.success]
        final_output = context.strip()
        success = len(successful) > 0

        duration = time.time() - start

        # Save to memory
        if self.memory and success:
            tools_used = list({t for a in agent_results for t in self._agents.get(a.agent, CoderAgent(None, None)).tools if self._agents.get(a.agent)})
            try:
                await self.memory.on_task_end(
                    task, final_output, success, duration,
                    len(agent_results), tools_used,
                )
            except Exception:
                pass

        if verbose:
            print(f"\n{'='*50}")
            print(f"Done in {duration:.1f}s | Agents used: {len(agent_results)}")
            print(f"{'='*50}")

        return OrchestratorResult(
            task=task,
            success=success,
            output=final_output,
            agent_results=agent_results,
            duration=duration,
        )

    async def _route(self, task: str) -> list[dict]:
        response = await self.llm.complete(
            messages=[Message(role="user", content=task)],
            system=ORCHESTRATOR_SYSTEM,
            model_tier="balanced",
        )
        text = response.content.strip()

        # Extract JSON
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            return json.loads(match.group(0))

        if text.startswith("["):
            return json.loads(text)

        raise ValueError(f"Could not parse routing response: {text[:200]}")
