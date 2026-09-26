"""调用树拼接测试：乱序等待、占位节点、去重、顺序无关性。"""
from __future__ import annotations

import random

import pytest

from app.models import SpanRecord
from app.tree import PLACEHOLDER_PREFIX, TraceAssembler


def make_span(
    trace_id: str,
    span_id: str,
    parent: str | None,
    service: str = "svc",
    start: float = 0.0,
    end: float = 100.0,
    status: int = 200,
) -> SpanRecord:
    return SpanRecord(trace_id, span_id, parent, service, start, end, status)


def find_node(nodes: list[dict], span_id: str) -> dict | None:
    for node in nodes:
        if node["span_id"] == span_id:
            return node
        found = find_node(node["children"], span_id)
        if found is not None:
            return found
    return None


def all_span_ids(nodes: list[dict]) -> list[str]:
    ids: list[str] = []
    for node in nodes:
        if node["type"] == "span":
            ids.append(node["span_id"])
        ids.extend(all_span_ids(node["children"]))
    return ids


def tree_signature(nodes: list[dict]) -> list:
    """把树结构转成可比较的规范形式（忽略顺序差异之外的字段）。"""
    sig = []
    for node in sorted(nodes, key=lambda n: n["span_id"]):
        sig.append(
            (node["span_id"], node["type"], tree_signature(node["children"]))
        )
    return sig


@pytest.fixture
def assembler() -> TraceAssembler:
    return TraceAssembler(max_wait_ms=10_000.0)


class TestOutOfOrderParent:
    def test_child_waits_for_late_parent(self, assembler: TraceAssembler) -> None:
        """子片段先到要挂起等待，父片段到了之后正确挂到父片段下。"""
        assembler.add_span(make_span("t1", "child", "parent"), now_ms=0.0)
        # 等待期间：子片段临时挂在未提交占位节点下，树标记为不完整
        tree = assembler.build_tree("t1", now_ms=1_000.0)
        assert tree is not None
        assert not tree["complete"]
        assert tree["pending_count"] == 1
        assert find_node(tree["roots"], "child") is not None  # 仍可见，只是暂挂

        assembler.add_span(make_span("t1", "parent", None), now_ms=2_000.0)
        tree = assembler.build_tree("t1", now_ms=3_000.0)
        assert tree is not None
        assert tree["complete"]
        parent_node = find_node(tree["roots"], "parent")
        assert parent_node is not None
        assert [c["span_id"] for c in parent_node["children"]] == ["child"]
        # 树里不应该再有任何占位节点
        assert all(
            not n["span_id"].startswith(PLACEHOLDER_PREFIX)
            for n in tree["roots"]
        )

    def test_timeout_goes_to_placeholder(self, assembler: TraceAssembler) -> None:
        """超过最长等待时间仍等不到父片段，归到「父片段缺失」占位节点。"""
        assembler.add_span(make_span("t1", "orphan", "ghost"), now_ms=0.0)
        tree = assembler.build_tree("t1", now_ms=11_000.0)
        assert tree is not None
        assert tree["complete"]  # 超时后不再 pending
        placeholder = find_node(tree["roots"], f"{PLACEHOLDER_PREFIX}ghost")
        assert placeholder is not None
        assert placeholder["type"] == "placeholder"
        assert placeholder["committed"]
        assert [c["span_id"] for c in placeholder["children"]] == ["orphan"]

    def test_not_placeholder_before_timeout(self, assembler: TraceAssembler) -> None:
        """等待时间没到，不能提前归到已提交占位节点。"""
        assembler.add_span(make_span("t1", "orphan", "ghost"), now_ms=0.0)
        tree = assembler.build_tree("t1", now_ms=5_000.0)
        assert tree is not None
        assert tree["pending_count"] == 1
        placeholder = find_node(tree["roots"], f"{PLACEHOLDER_PREFIX}ghost")
        assert placeholder is not None
        assert not placeholder["committed"]  # 只是临时展示，未提交

    def test_parent_arriving_after_timeout_reattaches(
        self, assembler: TraceAssembler
    ) -> None:
        """父片段在子片段超时归占位之后才到：子片段要重新挂回真实父节点。"""
        assembler.add_span(make_span("t1", "child", "parent"), now_ms=0.0)
        tree = assembler.build_tree("t1", now_ms=20_000.0)
        assert find_node(tree["roots"], f"{PLACEHOLDER_PREFIX}parent") is not None

        assembler.add_span(make_span("t1", "parent", None), now_ms=21_000.0)
        tree = assembler.build_tree("t1", now_ms=22_000.0)
        parent_node = find_node(tree["roots"], "parent")
        assert parent_node is not None
        assert [c["span_id"] for c in parent_node["children"]] == ["child"]
        assert find_node(tree["roots"], f"{PLACEHOLDER_PREFIX}parent") is None


class TestDuplicates:
    def test_duplicate_span_ignored(self, assembler: TraceAssembler) -> None:
        """同一 span_id 重复上报，树里只出现一次，且保留最早一份。"""
        first = make_span("t1", "s1", None, service="svc-a", start=0, end=100)
        dupe = make_span("t1", "s1", None, service="svc-b", start=50, end=999)
        assert assembler.add_span(first, now_ms=0.0) == "added"
        assert assembler.add_span(dupe, now_ms=1.0) == "duplicate"

        tree = assembler.build_tree("t1", now_ms=2.0)
        assert tree is not None
        ids = all_span_ids(tree["roots"])
        assert ids.count("s1") == 1
        node = find_node(tree["roots"], "s1")
        assert node["service"] == "svc-a"  # 只认最早到的那一份
        assert node["end_time"] == 100

    def test_duplicate_does_not_double_count_children(
        self, assembler: TraceAssembler
    ) -> None:
        assembler.add_span(make_span("t1", "p", None), now_ms=0.0)
        assembler.add_span(make_span("t1", "c", "p"), now_ms=1.0)
        assembler.add_span(make_span("t1", "c", "p"), now_ms=2.0)  # 重复
        tree = assembler.build_tree("t1", now_ms=3.0)
        parent = find_node(tree["roots"], "p")
        assert len(parent["children"]) == 1


class TestMissingParent:
    def test_missing_parent_does_not_break_tree(
        self, assembler: TraceAssembler
    ) -> None:
        """父编号指向不存在的片段：整批不报错，其它片段正常入树。"""
        assembler.add_span(make_span("t1", "root", None), now_ms=0.0)
        assembler.add_span(make_span("t1", "ok-child", "root"), now_ms=1.0)
        assembler.add_span(make_span("t1", "bad-child", "no-such-parent"), now_ms=2.0)

        tree = assembler.build_tree("t1", now_ms=20_000.0)
        assert tree is not None
        root = find_node(tree["roots"], "root")
        assert [c["span_id"] for c in root["children"]] == ["ok-child"]
        placeholder = find_node(tree["roots"], f"{PLACEHOLDER_PREFIX}no-such-parent")
        assert placeholder is not None
        assert [c["span_id"] for c in placeholder["children"]] == ["bad-child"]
        assert tree["span_count"] == 3


class TestOrderIndependence:
    def _build_spans(self) -> list[SpanRecord]:
        return [
            make_span("t1", "root", None, "gateway", 0, 1000),
            make_span("t1", "a", "root", "svc-a", 100, 900),
            make_span("t1", "b", "root", "svc-b", 200, 800),
            make_span("t1", "a1", "a", "svc-c", 150, 400),
            make_span("t1", "a2", "a", "svc-d", 500, 850),
            make_span("t1", "b1", "b", "svc-c", 300, 700),
        ]

    def test_tree_independent_of_arrival_order(self) -> None:
        """同一批片段无论上报顺序如何打乱，拼出的树结构必须一致。"""
        spans = self._build_spans()
        signatures = []
        for seed in range(10):
            asm = TraceAssembler(max_wait_ms=10_000.0)
            shuffled = spans[:]
            random.Random(seed).shuffle(shuffled)
            for i, span in enumerate(shuffled):
                asm.add_span(span, now_ms=float(i))
            tree = asm.build_tree("t1", now_ms=100.0)
            assert tree is not None
            signatures.append(tree_signature(tree["roots"]))
        assert all(sig == signatures[0] for sig in signatures)

    def test_critical_path_and_breakdown(self) -> None:
        """关键路径取根到最耗时叶子；服务占比按独占时间计算。"""
        asm = TraceAssembler()
        asm.add_span(make_span("t1", "root", None, "gateway", 0, 1000), now_ms=0.0)
        asm.add_span(make_span("t1", "fast", "root", "svc-a", 0, 100), now_ms=1.0)
        asm.add_span(make_span("t1", "slow", "root", "svc-b", 100, 900), now_ms=2.0)
        asm.add_span(make_span("t1", "slow-leaf", "slow", "svc-c", 200, 800), now_ms=3.0)

        tree = asm.build_tree("t1", now_ms=10.0)
        assert tree["critical_path"] == ["root", "slow", "slow-leaf"]
        # 权重 = root(1000) + slow(800) + slow-leaf(600)
        assert tree["critical_path_duration"] == 2400

        breakdown = {b["service"]: b for b in tree["service_breakdown"]}
        # gateway 独占 = 1000 - (fast 100 + slow 800) = 100
        assert breakdown["gateway"]["exclusive_ms"] == 100
        # svc-b 独占 = 800 - slow-leaf 600 = 200
        assert breakdown["svc-b"]["exclusive_ms"] == 200
        assert breakdown["svc-a"]["exclusive_ms"] == 100
        assert breakdown["svc-c"]["exclusive_ms"] == 600
        total_pct = sum(b["percent"] for b in tree["service_breakdown"])
        assert abs(total_pct - 100.0) < 0.01
