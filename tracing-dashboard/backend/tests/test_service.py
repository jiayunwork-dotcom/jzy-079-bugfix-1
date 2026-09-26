"""服务层测试：依赖图窗口切换、乱序归位端到端、重复上报。"""
from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.models import SpanRecord
from app.service import TracingService
from app.storage import SpanStore
from app.tree import placeholder_id


@pytest.fixture
def service() -> TracingService:
    svc = TracingService(
        Settings(database_path=":memory:", max_pending_wait_seconds=30)
    )
    yield svc
    svc.close()


def now_ms() -> float:
    return time.time() * 1000.0


def span(
    trace_id: str,
    span_id: str,
    parent: str | None,
    service_name: str,
    start: float,
    end: float,
) -> SpanRecord:
    return SpanRecord(trace_id, span_id, parent, service_name, start, end, 200)


def find_node(nodes: list[dict], span_id: str) -> dict | None:
    for node in nodes:
        if node["span_id"] == span_id:
            return node
        found = find_node(node["children"], span_id)
        if found is not None:
            return found
    return None


class TestGraphWindowSwitching:
    def test_old_window_edges_excluded_from_new_window(
        self, service: TracingService
    ) -> None:
        """切到最近 1 小时窗口后，只发生在一天前的调用边不能出现。"""
        now = now_ms()
        hour = 3600 * 1000.0
        day = 24 * hour

        # 一天前的旧调用：legacy -> archive
        old_start = now - day + hour  # 23 小时前，在 1d 窗口内、1h 窗口外
        service.ingest(
            [
                span("old-trace", "o1", None, "legacy", old_start, old_start + 100),
                span("old-trace", "o2", "o1", "archive", old_start + 10, old_start + 90),
            ]
        )
        # 最近的新调用：gateway -> auth
        new_start = now - 5 * 60 * 1000  # 5 分钟前
        service.ingest(
            [
                span("new-trace", "n1", None, "gateway", new_start, new_start + 100),
                span("new-trace", "n2", "n1", "auth", new_start + 10, new_start + 90),
            ]
        )

        graph_1h = service.get_graph("1h")
        edges_1h = {(e["source"], e["target"]) for e in graph_1h["edges"]}
        assert ("gateway", "auth") in edges_1h
        # 旧窗口独有的边不能混进新窗口
        assert ("legacy", "archive") not in edges_1h
        nodes_1h = {n["id"] for n in graph_1h["nodes"]}
        assert "legacy" not in nodes_1h
        assert "archive" not in nodes_1h

        graph_1d = service.get_graph("1d")
        edges_1d = {(e["source"], e["target"]) for e in graph_1d["edges"]}
        assert ("gateway", "auth") in edges_1d
        assert ("legacy", "archive") in edges_1d

    def test_window_switch_back_and_forth_is_stable(
        self, service: TracingService
    ) -> None:
        """反复切换窗口，结果各自独立、可重复。"""
        now = now_ms()
        service.ingest(
            [span("t", "a", None, "svc-a", now - 100, now),
             span("t", "b", "a", "svc-b", now - 90, now - 10)]
        )
        first = service.get_graph("1h")
        service.get_graph("1d")
        second = service.get_graph("1h")
        assert first["edges"] == second["edges"]
        assert first["nodes"] == second["nodes"]


class TestIngestEndToEnd:
    def test_out_of_order_then_parent_arrives(self, service: TracingService) -> None:
        """端到端：子片段先上报、父片段后上报，最终树结构正确。"""
        now = now_ms()
        service.ingest([span("t1", "child", "root", "svc-b", now, now + 50)])
        tree = service.get_tree("t1")
        assert tree is not None
        assert not tree["complete"]

        service.ingest([span("t1", "root", None, "svc-a", now - 10, now + 100)])
        tree = service.get_tree("t1")
        assert tree["complete"]
        root = tree["roots"][0]
        assert root["span_id"] == "root"
        assert [c["span_id"] for c in root["children"]] == ["child"]

    def test_duplicate_ingest_counted(self, service: TracingService) -> None:
        now = now_ms()
        s = span("t1", "s1", None, "svc", now, now + 10)
        first = service.ingest([s])
        second = service.ingest([s])
        assert first["accepted"] == 1
        assert second["duplicates"] == 1
        tree = service.get_tree("t1")
        assert tree["span_count"] == 1

    def test_edges_extracted_from_assembled_tree(
        self, service: TracingService
    ) -> None:
        """依赖边来自拼好的调用树：占位节点下的片段不产生边。"""
        now = now_ms()
        service.ingest(
            [
                span("t1", "root", None, "svc-a", now, now + 100),
                span("t1", "child", "root", "svc-b", now + 10, now + 50),
                # 父片段不存在，等超时后归占位节点，不应产生依赖边
                span("t1", "orphan", "ghost", "svc-c", now + 20, now + 40),
            ]
        )
        service.assembler.flush_expired(now_ms=now + 60_000)
        graph = service.get_graph("1h")
        pairs = {(e["source"], e["target"]) for e in graph["edges"]}
        assert ("svc-a", "svc-b") in pairs
        assert all("svc-c" not in p for p in pairs)


class TestPersistenceReload:
    def test_reload_from_storage(self, tmp_path) -> None:
        """重启后从 SQLite 重建，树结构保持一致。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=30)
        svc1 = TracingService(cfg)
        now = now_ms()
        svc1.ingest(
            [
                span("t1", "root", None, "svc-a", now, now + 100),
                span("t1", "child", "root", "svc-b", now + 10, now + 50),
            ]
        )
        tree_before = svc1.get_tree("t1")
        svc1.close()

        svc2 = TracingService(cfg)
        tree_after = svc2.get_tree("t1")
        svc2.close()
        assert tree_after["span_count"] == tree_before["span_count"]
        assert tree_after["critical_path"] == tree_before["critical_path"]


class TestRestartPendingState:
    """重启对等待计时透明：等待状态、剩余等待时间、已超时占位都要连续。"""

    def test_pending_state_consistent_across_restart(self, tmp_path) -> None:
        """重启前没等满的子片段，重启后仍是等待状态，不会被立刻归入占位节点。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=30)
        now = now_ms()
        svc1 = TracingService(cfg)
        # 片段自带的开始/结束时间比当前早两分钟，模拟攒批延迟上报
        svc1.ingest(
            [
                span("t1", "root", None, "gateway", now - 120_000, now - 119_000),
                span("t1", "child", "p1", "inventory", now - 120_000, now - 119_500),
            ]
        )
        tree = svc1.get_tree("t1")
        assert tree["pending_count"] == 1
        assert not tree["complete"]
        svc1.close()

        # 数据卷保留，重启后端（距收下远不到 30 秒最长等待）
        svc2 = TracingService(cfg)
        tree = svc2.get_tree("t1")
        svc2.close()
        assert tree["pending_count"] == 1
        assert not tree["complete"]
        placeholder = find_node(tree["roots"], placeholder_id("p1"))
        assert placeholder is not None
        assert not placeholder["committed"]

    def test_remaining_wait_runs_continuously_across_restart(self, tmp_path) -> None:
        """剩余等待时间连续计算：从收下时刻接着算，重启不清零也不提前到期。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=3)
        now = now_ms()
        # 落库一条 1 秒前收下的孤儿片段，等价于"收下 1 秒后重启"
        store = SpanStore(db)
        store.insert_span(
            span("t1", "child", "p1", "inventory", now - 120_000, now - 119_500),
            received_at_ms=now - 1_000,
        )
        store.close()

        svc = TracingService(cfg)
        # 重启后立刻查：仍在等待（才等了 1 秒，最长 3 秒）
        tree = svc.get_tree("t1")
        assert tree["pending_count"] == 1
        assert not tree["complete"]
        # 从收下算起 3.5 秒：已超时，归入已提交占位节点
        tree = svc.assembler.build_tree("t1", now_ms=now + 2_500)
        assert tree["pending_count"] == 0
        assert tree["complete"]
        placeholder = find_node(tree["roots"], placeholder_id("p1"))
        assert placeholder is not None
        assert placeholder["committed"]
        svc.close()

    def test_committed_placeholder_survives_restart(self, tmp_path) -> None:
        """重启前已超时归占位的片段，重启后仍在占位节点下；父片段补到后照常归位。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=3)
        now = now_ms()
        # 落库一条 10 秒前收下的孤儿片段：到重启时早已超过 3 秒最长等待
        store = SpanStore(db)
        store.insert_span(
            span("t1", "child", "p1", "inventory", now - 120_000, now - 119_500),
            received_at_ms=now - 10_000,
        )
        store.close()

        svc = TracingService(cfg)
        tree = svc.get_tree("t1")
        assert tree["pending_count"] == 0
        assert tree["complete"]
        placeholder = find_node(tree["roots"], placeholder_id("p1"))
        assert placeholder is not None
        assert placeholder["committed"]
        assert [c["span_id"] for c in placeholder["children"]] == ["child"]

        # 父片段补报：子片段照旧挪回真实父片段下，占位节点消失
        svc.ingest([span("t1", "p1", None, "gateway", now - 121_000, now - 119_000)])
        tree = svc.get_tree("t1")
        parent = find_node(tree["roots"], "p1")
        assert parent is not None
        assert [c["span_id"] for c in parent["children"]] == ["child"]
        assert find_node(tree["roots"], placeholder_id("p1")) is None
        svc.close()
