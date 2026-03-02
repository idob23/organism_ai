"""Q-5.2: MemoryGraph — directed graph of edges between memory nodes.

Nodes are rows in task_memories or user_profile.
Edges capture relationships: temporal, causal, entity, procedural.
All operations are wrapped in try/except in the callers so the rest of the
system keeps running even if the graph layer is unavailable.
"""
import json
import uuid

from sqlalchemy import select, or_

from src.organism.memory.database import MemoryEdge, TaskMemory, AsyncSessionLocal


class MemoryGraph:

    async def add_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: dict | None = None,
    ) -> None:
        """Insert a directed edge (from_id, to_id, edge_type).

        If an identical edge already exists (same triple), update its weight
        and metadata instead of inserting a duplicate.
        """
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with AsyncSessionLocal() as session:
            stmt = (
                select(MemoryEdge)
                .where(MemoryEdge.from_id == from_id)
                .where(MemoryEdge.to_id == to_id)
                .where(MemoryEdge.edge_type == edge_type)
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.weight = weight
                if meta_str is not None:
                    existing.meta_json = meta_str
            else:
                session.add(MemoryEdge(
                    id=str(uuid.uuid4()),
                    from_id=from_id,
                    to_id=to_id,
                    edge_type=edge_type,
                    weight=weight,
                    meta_json=meta_str,
                ))
            await session.commit()

    async def get_neighbors(
        self,
        node_id: str,
        edge_type: str | None = None,
        direction: str = "outgoing",
    ) -> list[dict]:
        """Return neighbors of node_id as a list of dicts.

        direction:
            "outgoing" — edges where from_id = node_id  (default)
            "incoming" — edges where to_id   = node_id
            "both"     — either direction
        Returns [{"node_id", "edge_type", "weight", "metadata"}, ...]
        """
        async with AsyncSessionLocal() as session:
            if direction == "outgoing":
                cond = MemoryEdge.from_id == node_id
            elif direction == "incoming":
                cond = MemoryEdge.to_id == node_id
            else:
                cond = or_(MemoryEdge.from_id == node_id, MemoryEdge.to_id == node_id)

            stmt = select(MemoryEdge).where(cond)
            if edge_type:
                stmt = stmt.where(MemoryEdge.edge_type == edge_type)

            result = await session.execute(stmt)
            edges = result.scalars().all()

        neighbors = []
        for edge in edges:
            if direction == "incoming":
                neighbor_id = edge.from_id
            elif direction == "outgoing":
                neighbor_id = edge.to_id
            else:
                neighbor_id = edge.to_id if edge.from_id == node_id else edge.from_id

            meta: dict = {}
            if edge.meta_json:
                try:
                    meta = json.loads(edge.meta_json)
                except Exception:
                    pass

            neighbors.append({
                "node_id": neighbor_id,
                "edge_type": edge.edge_type,
                "weight": edge.weight,
                "metadata": meta,
            })
        return neighbors

    async def get_related_tasks(
        self,
        task_id: str,
        edge_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return task_memories reachable from task_id via one edge hop.

        Queries edges involving task_id, then fetches the TaskMemory rows for
        the other endpoints. Sorted by edge weight DESC.
        Returns [{"task_id", "task", "result", "edge_type", "weight"}, ...]
        """
        async with AsyncSessionLocal() as session:
            edge_stmt = (
                select(MemoryEdge)
                .where(or_(
                    MemoryEdge.from_id == task_id,
                    MemoryEdge.to_id == task_id,
                ))
            )
            if edge_types:
                edge_stmt = edge_stmt.where(MemoryEdge.edge_type.in_(edge_types))
            edge_stmt = edge_stmt.order_by(MemoryEdge.weight.desc()).limit(limit)

            edge_result = await session.execute(edge_stmt)
            edges = edge_result.scalars().all()

            if not edges:
                return []

            # neighbor_id → (edge_type, weight)
            neighbor_map: dict[str, tuple[str, float]] = {}
            for edge in edges:
                nid = edge.to_id if edge.from_id == task_id else edge.from_id
                neighbor_map[nid] = (edge.edge_type, edge.weight)

            tasks_result = await session.execute(
                select(TaskMemory).where(TaskMemory.id.in_(list(neighbor_map)))
            )
            tasks = {tm.id: tm for tm in tasks_result.scalars().all()}

        return [
            {
                "task_id": tid,
                "task": tasks[tid].task if tid in tasks else "",
                "result": tasks[tid].result if tid in tasks else "",
                "edge_type": neighbor_map[tid][0],
                "weight": neighbor_map[tid][1],
            }
            for tid in neighbor_map
            if tid in tasks
        ]

    async def add_temporal_edge(self, prev_task_id: str, curr_task_id: str) -> None:
        """Convenience wrapper: temporal edge from the previous task to the current one."""
        await self.add_edge(prev_task_id, curr_task_id, "temporal", weight=1.0)

    async def get_entity_subgraph(self, entity: str, depth: int = 2) -> list[dict]:
        """Return edges whose metadata JSON contains *entity*, expanded to *depth* hops.

        Seed: edges where metadata LIKE '%entity%'.
        For depth > 1 each unique node in the seed is expanded one more hop
        via get_neighbors("both"), collecting newly-discovered nodes up to
        depth-1 additional rounds.
        """
        from sqlalchemy import text as sa_text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                sa_text(
                    "SELECT id, from_id, to_id, edge_type, weight, metadata "
                    "FROM memory_edges "
                    "WHERE metadata LIKE :pattern"
                ),
                {"pattern": f"%{entity}%"},
            )
            rows = result.all()

        edges: list[dict] = [
            {
                "id": r[0],
                "from_id": r[1],
                "to_id": r[2],
                "edge_type": r[3],
                "weight": float(r[4]),
                "metadata": json.loads(r[5]) if r[5] else {},
            }
            for r in rows
        ]

        if depth <= 1 or not edges:
            return edges

        seen_nodes: set[str] = set()
        for e in edges:
            seen_nodes.add(e["from_id"])
            seen_nodes.add(e["to_id"])

        for _ in range(depth - 1):
            new_nodes: set[str] = set()
            for node_id in list(seen_nodes):
                try:
                    for n in await self.get_neighbors(node_id, direction="both"):
                        nid = n["node_id"]
                        if nid not in seen_nodes:
                            new_nodes.add(nid)
                            edges.append({
                                "from_id": node_id,
                                "to_id": nid,
                                "edge_type": n["edge_type"],
                                "weight": n["weight"],
                                "metadata": n["metadata"],
                            })
                except Exception:
                    pass
            if not new_nodes:
                break
            seen_nodes.update(new_nodes)

        return edges
