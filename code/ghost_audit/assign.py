"""Optimal assignment (Hungarian algorithm) for small bipartite components."""
from __future__ import annotations

from typing import Optional

BIG = 1e6


def hungarian_max(weights: list[list[Optional[float]]]) -> list[tuple[int, int]]:
    """Maximum-weight matching that first maximizes the number of matched rows.

    `weights[i][j]` is the weight of matching row i to column j, or None when the pair is not allowed.
    Returns [(row, col)] for matched pairs. Implements the classic O(n^3) potentials method on a
    square matrix padded with forbidden/dummy cells.
    """
    n_r = len(weights)
    n_c = len(weights[0]) if weights else 0
    n = max(n_r, n_c)
    if n == 0:
        return []
    # cost to minimize: matching a real pair earns BIG + weight; padded/forbidden cells cost 0 reward.
    cost = [[0.0] * n for _ in range(n)]
    for i in range(n_r):
        for j in range(n_c):
            w = weights[i][j]
            if w is not None:
                cost[i][j] = -(BIG + w)
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = []
    for j in range(1, n + 1):
        i = p[j]
        if 1 <= i <= n_r and j <= n_c and weights[i - 1][j - 1] is not None:
            out.append((i - 1, j - 1))
    return out
