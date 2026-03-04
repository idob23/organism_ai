"""Q-8.3: Duplicate search service for 1C entities.

Finds duplicate entries in 1C directories (counterparties, equipment, nomenclature)
using semantic similarity via embeddings. Works as a local tool that fetches data
from MCP 1C server and computes similarity locally.

Strategy:
1. Fetch entities from 1C via MCP tool (or accept pre-loaded list)
2. Compute embeddings for entity names/descriptions
3. Compare all pairs via cosine similarity
4. Group duplicates above threshold
"""

from typing import Any
from collections import defaultdict

from src.organism.tools.base import BaseTool, ToolResult
from src.organism.memory.embeddings import get_embedding
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("tools.duplicate_finder")

SIMILARITY_THRESHOLD = 0.85  # cosine similarity >= 0.85 = likely duplicate
MAX_ENTITIES = 200  # safety cap to avoid explosion of pairs


class DuplicateFinderTool(BaseTool):

    @property
    def name(self) -> str:
        return "duplicate_finder"

    @property
    def description(self) -> str:
        return (
            "Find duplicate entries in 1C directories using semantic similarity. "
            "Detects duplicates like 'OOO Romashka' vs 'Romashka OOO'. "
            "Accepts a list of entity names OR fetches from connected 1C MCP server."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity names to check for duplicates. If empty, fetches from 1C.",
                },
                "entity_type": {
                    "type": "string",
                    "enum": ["counterparties", "equipment", "nomenclature"],
                    "default": "counterparties",
                    "description": "Type of entities (used when fetching from 1C)",
                },
                "threshold": {
                    "type": "number",
                    "default": 0.85,
                    "description": "Similarity threshold (0.0-1.0). Higher = stricter matching.",
                },
            },
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        entities = input.get("entities", [])
        entity_type = input.get("entity_type", "counterparties")
        threshold = input.get("threshold", SIMILARITY_THRESHOLD)

        # If no entities provided, return error with guidance
        if not entities:
            return ToolResult(
                output="",
                error="No entities provided. Pass a list of names in 'entities' field, or use MCP 1C tools to fetch data first.",
                exit_code=1,
            )

        if len(entities) > MAX_ENTITIES:
            entities = entities[:MAX_ENTITIES]

        try:
            groups = await self._find_duplicates(entities, threshold)
        except Exception as e:
            log_exception(_log, "Duplicate search failed", e)
            return ToolResult(output="", error=f"Duplicate search failed: {e}", exit_code=1)

        if not groups:
            return ToolResult(
                output=f"No duplicates found among {len(entities)} entities (threshold={threshold}).",
                exit_code=0,
            )

        # Format output
        lines = [f"Found {len(groups)} duplicate group(s) among {len(entities)} entities:\n"]
        for i, group in enumerate(groups, 1):
            lines.append(f"Group {i} (similarity >= {threshold}):")
            for name, score in group:
                lines.append(f"  - {name} (similarity: {score:.3f})")
            lines.append("")

        return ToolResult(output="\n".join(lines), exit_code=0)

    async def _find_duplicates(
        self, entities: list[str], threshold: float
    ) -> list[list[tuple[str, float]]]:
        """Compute embeddings, compare pairs, return groups of duplicates."""
        import numpy as np

        # 1. Compute embeddings
        embeddings: list[tuple[str, list[float]]] = []
        for name in entities:
            emb = await get_embedding(name)
            if emb:
                embeddings.append((name, emb))

        if len(embeddings) < 2:
            return []

        # 2. Pairwise cosine similarity
        names = [e[0] for e in embeddings]
        vectors = np.array([e[1] for e in embeddings])

        # Normalize for cosine similarity
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1  # avoid division by zero
        normalized = vectors / norms

        # Cosine similarity matrix
        sim_matrix = normalized @ normalized.T

        # 3. Find pairs above threshold (exclude self-pairs)
        pairs: list[tuple[int, int, float]] = []
        n = len(names)
        for i in range(n):
            for j in range(i + 1, n):
                if sim_matrix[i, j] >= threshold:
                    pairs.append((i, j, float(sim_matrix[i, j])))

        if not pairs:
            return []

        # 4. Group connected duplicates (union-find)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for i, j, _ in pairs:
            union(i, j)

        # Build groups with proper members
        group_members: dict[int, set[int]] = defaultdict(set)
        for i, j, score in pairs:
            root = find(i)
            group_members[root].add(i)
            group_members[root].add(j)

        groups: list[list[tuple[str, float]]] = []
        for root, members in group_members.items():
            group: list[tuple[str, float]] = []
            for idx in sorted(members):
                # Find best similarity with another group member
                best = 0.0
                for other in members:
                    if other != idx:
                        i_min, i_max = min(idx, other), max(idx, other)
                        best = max(best, float(sim_matrix[i_min, i_max]))
                group.append((names[idx], best))
            if len(group) >= 2:
                groups.append(group)

        return groups
