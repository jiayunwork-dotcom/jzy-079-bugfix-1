"""服务依赖图：聚合 + 环检测。

聚合：从已拼好的调用树提取的调用边 (caller, callee, duration) 中，
按服务对聚合计数与平均耗时。

环检测：Tarjan 强连通分量。大小 > 1 的分量内部的所有边都是环边；
自环（A 调 A）单独标记。不存在环的图不会被误标。

本模块纯函数实现，不依赖 FastAPI / 存储，可独立单测。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

# 边: (caller_service, callee_service, duration_ms)
Edge = tuple[str, str, float]


def aggregate_edges(edges: Iterable[Edge]) -> dict:
    """把原始调用边聚合成 {nodes, edges} 结构的图。

    节点带 call_count（作为被调方被调用的次数）；
    边带 count / avg_duration / in_cycle。
    """
    edge_stats: dict[tuple[str, str], list[float]] = defaultdict(list)
    node_in_calls: dict[str, int] = defaultdict(int)
    node_out_calls: dict[str, int] = defaultdict(int)
    services: set[str] = set()

    for caller, callee, duration in edges:
        edge_stats[(caller, callee)].append(duration)
        node_in_calls[callee] += 1
        node_out_calls[caller] += 1
        services.add(caller)
        services.add(callee)

    cyclic_pairs = find_cyclic_edges(edge_stats.keys())

    return {
        "nodes": [
            {
                "id": service,
                "call_count": node_in_calls.get(service, 0)
                + node_out_calls.get(service, 0),
            }
            for service in sorted(services)
        ],
        "edges": [
            {
                "source": caller,
                "target": callee,
                "count": len(durations),
                "avg_duration": round(sum(durations) / len(durations), 3),
                "in_cycle": (caller, callee) in cyclic_pairs,
            }
            for (caller, callee), durations in sorted(edge_stats.items())
        ],
    }


def find_cyclic_edges(pairs: Iterable[tuple[str, str]]) -> set[tuple[str, str]]:
    """返回所有处于环上的 (caller, callee) 边集合。

    判定：Tarjan 强连通分量，分量大小 > 1 时分量内所有边成环；
    另外 A->A 自环直接算环。
    """
    pair_set = set(pairs)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for caller, callee in pair_set:
        adjacency[caller].append(callee)
        adjacency.setdefault(callee, [])

    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    index_counter = [0]
    components: list[list[str]] = []

    # 迭代版 Tarjan，避免深图递归爆栈
    for root in adjacency:
        if root in index_of:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, child_idx = work[-1]
            if child_idx == 0:
                index_of[node] = lowlink[node] = index_counter[0]
                index_counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            neighbors = adjacency[node]
            for i in range(child_idx, len(neighbors)):
                nxt = neighbors[i]
                if nxt not in index_of:
                    work[-1] = (node, i + 1)
                    work.append((nxt, 0))
                    recurse = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if recurse:
                continue
            if node != work[-1][0]:  # pragma: no cover - 防御
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    cyclic: set[tuple[str, str]] = set()
    for component in components:
        if len(component) > 1:
            members = set(component)
            for caller, callee in pair_set:
                if caller in members and callee in members:
                    cyclic.add((caller, callee))
    for caller, callee in pair_set:
        if caller == callee:
            cyclic.add((caller, callee))
    return cyclic
