"""Standalone deduplication logic for public API.

Adapted from src/organism/tools/duplicate_finder.py — no internal imports.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import structlog

from embeddings import get_embedding

_log = structlog.get_logger("dedup")

DEFAULT_THRESHOLD = 0.85


@dataclass
class DuplicateGroup:
    items: list[str]
    similarity: float


@dataclass
class DeduplicationResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    total_entities: int = 0
    duplicates_found: int = 0
    processing_time_ms: int = 0


async def find_duplicates(
    entities: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> DeduplicationResult:
    """Compute embeddings, compare pairs, return grouped duplicates."""
    start = time.monotonic()

    # 1. Compute embeddings (skip empty/whitespace-only)
    embeddings: list[tuple[str, list[float]]] = []
    for name in entities:
        if not name or not name.strip():
            continue
        emb = await get_embedding(name)
        if emb:
            embeddings.append((name, emb))

    if len(embeddings) < 2:
        elapsed = int((time.monotonic() - start) * 1000)
        return DeduplicationResult(
            total_entities=len(entities),
            processing_time_ms=elapsed,
        )

    # 2. Pairwise cosine similarity
    names = [e[0] for e in embeddings]
    vectors = np.array([e[1] for e in embeddings])

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    normalized = vectors / norms

    sim_matrix = normalized @ normalized.T

    # 3. Find pairs above threshold (exclude self-pairs)
    n = len(names)
    pairs: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                pairs.append((i, j, float(sim_matrix[i, j])))

    if not pairs:
        elapsed = int((time.monotonic() - start) * 1000)
        return DeduplicationResult(
            total_entities=len(entities),
            processing_time_ms=elapsed,
        )

    # 4. Union-find grouping
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

    group_members: dict[int, set[int]] = defaultdict(set)
    for i, j, _score in pairs:
        root = find(i)
        group_members[root].add(i)
        group_members[root].add(j)

    # 5. Build result groups
    groups: list[DuplicateGroup] = []
    total_dup_count = 0
    for _root, members in group_members.items():
        if len(members) < 2:
            continue
        group_items: list[str] = []
        best_sim = 0.0
        for idx in sorted(members):
            group_items.append(names[idx])
            for other in members:
                if other != idx:
                    i_min, i_max = min(idx, other), max(idx, other)
                    best_sim = max(best_sim, float(sim_matrix[i_min, i_max]))
        groups.append(DuplicateGroup(items=group_items, similarity=round(best_sim, 4)))
        total_dup_count += len(group_items)

    elapsed = int((time.monotonic() - start) * 1000)
    return DeduplicationResult(
        groups=groups,
        total_entities=len(entities),
        duplicates_found=total_dup_count,
        processing_time_ms=elapsed,
    )
