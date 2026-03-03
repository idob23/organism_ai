"""Lightweight state machine for workflow orchestration (Q-6.1).

Each node is an async handler.  Edges are condition -> next_node mappings.
The machine runs until it hits a terminal node, exhausts max_steps, or fails
without a fallback edge.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable, Any


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StateNode:
    name: str                                           # e.g. "researcher", "writer", "evaluate"
    handler: Callable[..., Awaitable[Any]]              # async fn(WorkflowState) -> (result, condition)
    next_nodes: dict[str, str] = field(default_factory=dict)  # condition -> node_name
    status: NodeStatus = NodeStatus.PENDING


@dataclass
class WorkflowState:
    current_node: str = ""
    context: dict[str, Any] = field(default_factory=dict)  # shared state between nodes
    history: list[dict] = field(default_factory=list)       # execution log


class StateMachine:

    def __init__(self) -> None:
        self.nodes: dict[str, StateNode] = {}
        self.start_node: str = ""
        self.end_nodes: set[str] = set()

    def add_node(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        next_nodes: dict[str, str] | None = None,
    ) -> None:
        self.nodes[name] = StateNode(
            name=name, handler=handler, next_nodes=next_nodes or {},
        )

    def set_start(self, name: str) -> None:
        self.start_node = name

    def add_end(self, name: str) -> None:
        self.end_nodes.add(name)

    async def run(
        self,
        initial_context: dict[str, Any] | None = None,
        max_steps: int = 10,
    ) -> WorkflowState:
        state = WorkflowState(
            current_node=self.start_node,
            context=initial_context or {},
        )

        for _step in range(max_steps):
            node = self.nodes.get(state.current_node)
            if not node:
                break

            node.status = NodeStatus.RUNNING
            try:
                # Handler returns (result, condition_key)
                result, condition = await node.handler(state)
                node.status = NodeStatus.DONE
                state.history.append({
                    "node": node.name, "status": "done", "condition": condition,
                })
                state.context[f"{node.name}_result"] = result

                # Terminal?
                if node.name in self.end_nodes or not node.next_nodes:
                    break

                # Transition via condition key, fall back to "default"
                next_name = node.next_nodes.get(
                    condition, node.next_nodes.get("default", ""),
                )
                if not next_name or next_name not in self.nodes:
                    break
                state.current_node = next_name

            except Exception as e:
                node.status = NodeStatus.FAILED
                state.history.append({
                    "node": node.name,
                    "status": "failed",
                    "error": str(e)[:200],
                })
                # Try fallback "error" edge
                fallback = node.next_nodes.get("error", "")
                if fallback and fallback in self.nodes:
                    state.current_node = fallback
                else:
                    break

        return state
