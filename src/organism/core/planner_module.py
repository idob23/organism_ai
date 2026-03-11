"""PlannerModule — groups Planner and TaskDecomposer for Orchestrator use.

Extracted from CoreLoop (ARCH-1.2) since Q-10.4 made _handle_conversation
the primary execution path, making Planner/Decomposer dead code in CoreLoop.
"""
from src.organism.core.planner import Planner
from src.organism.core.decomposer import TaskDecomposer
from src.organism.llm.base import LLMProvider


class PlannerModule:
    def __init__(self, llm: LLMProvider) -> None:
        self.planner = Planner(llm)
        self.decomposer = TaskDecomposer(llm)
