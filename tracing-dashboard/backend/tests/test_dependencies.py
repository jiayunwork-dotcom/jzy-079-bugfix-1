"""依赖图聚合与环检测测试。"""
from __future__ import annotations

from app.dependencies import aggregate_edges, find_cyclic_edges


class TestAggregation:
    def test_counts_and_avg_duration(self) -> None:
        edges = [
            ("gateway", "auth", 100.0),
            ("gateway", "auth", 200.0),
            ("auth", "db", 50.0),
        ]
        graph = aggregate_edges(edges)
        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        ga = edge_map[("gateway", "auth")]
        assert ga["count"] == 2
        assert ga["avg_duration"] == 150.0
        assert edge_map[("auth", "db")]["count"] == 1

        node_map = {n["id"]: n for n in graph["nodes"]}
        assert node_map["gateway"]["call_count"] == 2
        assert node_map["auth"]["call_count"] == 3  # 被调2次 + 调出1次

    def test_empty(self) -> None:
        assert aggregate_edges([]) == {"nodes": [], "edges": []}


class TestCycleDetection:
    def test_simple_cycle_marked(self) -> None:
        """A->B->A 的两条边都必须标成环边。"""
        pairs = {("a", "b"), ("b", "a")}
        cyclic = find_cyclic_edges(pairs)
        assert cyclic == {("a", "b"), ("b", "a")}

    def test_three_node_cycle(self) -> None:
        pairs = {("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")}
        cyclic = find_cyclic_edges(pairs)
        assert ("a", "b") in cyclic
        assert ("b", "c") in cyclic
        assert ("c", "a") in cyclic
        # c->d 不在环上，不能误标
        assert ("c", "d") not in cyclic

    def test_acyclic_graph_not_marked(self) -> None:
        """正常的 DAG 一条边都不能被误标。"""
        pairs = {
            ("gateway", "auth"),
            ("gateway", "order"),
            ("order", "db"),
            ("auth", "db"),
            ("order", "notify"),
        }
        assert find_cyclic_edges(pairs) == set()

    def test_self_loop_is_cycle(self) -> None:
        assert find_cyclic_edges({("a", "a")}) == {("a", "a")}

    def test_cycle_flag_in_aggregated_graph(self) -> None:
        edges = [
            ("a", "b", 10.0),
            ("b", "a", 20.0),
            ("b", "c", 5.0),
        ]
        graph = aggregate_edges(edges)
        edge_map = {(e["source"], e["target"]): e for e in graph["edges"]}
        assert edge_map[("a", "b")]["in_cycle"]
        assert edge_map[("b", "a")]["in_cycle"]
        assert not edge_map[("b", "c")]["in_cycle"]

    def test_disconnected_components(self) -> None:
        """多个不相连的子图，各自独立判断。"""
        pairs = {
            ("a", "b"), ("b", "a"),   # 环
            ("x", "y"), ("y", "z"),   # 无环
        }
        cyclic = find_cyclic_edges(pairs)
        assert cyclic == {("a", "b"), ("b", "a")}
