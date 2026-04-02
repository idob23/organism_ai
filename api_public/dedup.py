"""Standalone deduplication logic for public API.

Adapted from src/organism/tools/duplicate_finder.py — no internal imports.
"""

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import structlog

from embeddings import get_embeddings_batch

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


_NUM_RE = re.compile(r'[\d]+(?:[,.][\d]+)?')


def _name_skeleton(name: str) -> tuple[str, list[str]]:
    """Replace numeric tokens with '#', return (skeleton, raw_number_strings)."""
    nums = _NUM_RE.findall(name)
    skeleton = _NUM_RE.sub('#', name).lower().strip()
    skeleton = re.sub(r'\s+', ' ', skeleton)
    return skeleton, nums


def _filter_numeric_false_positives(
    pairs: list[tuple[int, int, float]],
    names: list[str],
) -> list[tuple[int, int, float]]:
    """Remove pairs where names share the same text skeleton but differ in numbers.

    Catches false positives like:
      "\u0410\u0412\u0412 3\u0420 0,5\u0410" vs "\u0410\u0412\u0412 3\u0420 8\u0410"  (different amperage)
      "\u0428\u043f\u043b\u0438\u043d\u0442 23310CA020020" vs "\u0428\u043f\u043b\u0438\u043d\u0442 23310CA020025"  (different article)
    While keeping real duplicates:
      "\u041e\u041e\u041e \u0420\u043e\u043c\u0430\u0448\u043a\u0430" vs "\u0420\u043e\u043c\u0430\u0448\u043a\u0430 \u041e\u041e\u041e"  (same company, word reorder)
    """
    filtered = []
    for i, j, sim in pairs:
        skel_i, nums_i = _name_skeleton(names[i])
        skel_j, nums_j = _name_skeleton(names[j])
        # Same skeleton but different numbers -> false positive
        if skel_i == skel_j and nums_i != nums_j:
            continue
        filtered.append((i, j, sim))
    return filtered


# Smoke test:
# _filter_numeric_false_positives(
#     [(0, 1, 0.95), (2, 3, 0.93)],
#     ["\u0410\u0412\u0412 3\u0420 0,5\u0410", "\u0410\u0412\u0412 3\u0420 8\u0410",
#      "\u041e\u041e\u041e \u0420\u043e\u043c\u0430\u0448\u043a\u0430", "\u0420\u043e\u043c\u0430\u0448\u043a\u0430 \u041e\u041e\u041e"]
# ) -> [(2, 3, 0.93)]  # first pair removed, second kept


async def find_duplicates(
    entities: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> DeduplicationResult:
    """Compute embeddings, compare pairs, return grouped duplicates."""
    start = time.monotonic()

    # 1. Filter empty/whitespace, then batch-embed in one API call
    valid_names = [name for name in entities if name and name.strip()]
    raw_vectors = await get_embeddings_batch(valid_names) if valid_names else []

    embeddings: list[tuple[str, list[float]]] = [
        (name, vec)
        for name, vec in zip(valid_names, raw_vectors)
        if vec
    ]

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

    # 3b. Filter numeric false positives
    pairs = _filter_numeric_false_positives(pairs, names)
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
