"""服务层测试：依赖图窗口切换、乱序归位端到端、重复上报。"""
from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.models import SpanRecord
from app.service import TracingService


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


class TestRestartPendingWait:
    """重启对等待计时透明（端到端：真实 SQLite 文件 + 重建服务）。"""

    def test_pending_wait_is_transparent_across_restart(self, tmp_path) -> None:
        """最长等待 3 秒、收下 1 秒后重启：重启后仍在等待，收下约 3.5 秒后到期。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=3)
        svc1 = TracingService(cfg)
        # 开始/结束时间故意早两分钟，模拟客户端攒批、延迟上报
        start = now_ms() - 120_000
        svc1.ingest(
            [
                span("t1", "root", None, "gateway", start, start + 2_000),
                span("t1", "child", "p1", "inventory", start + 100, start + 900),
            ]
        )
        tree = svc1.get_tree("t1")
        assert tree["pending_count"] == 1
        assert not tree["complete"]

        time.sleep(1.0)  # 收下 1 秒后重启
        svc1.close()

        svc2 = TracingService(cfg)
        try:
            # 重启后立刻查：仍在等待，不能一重启就到期
            tree = svc2.get_tree("t1")
            assert tree["pending_count"] == 1
            assert not tree["complete"]
            placeholder = next(n for n in tree["roots"] if n["type"] == "placeholder")
            assert placeholder["committed"] is False

            # 从收下算起约 3.5 秒：已到期归占位节点——剩余时间连续计算，
            # 既不是重启瞬间到期，也不是从重启重新计满 3 秒
            time.sleep(2.5)
            tree = svc2.get_tree("t1")
            assert tree["pending_count"] == 0
            assert tree["complete"]
            placeholder = next(n for n in tree["roots"] if n["type"] == "placeholder")
            assert placeholder["committed"] is True
            assert [c["span_id"] for c in placeholder["children"]] == ["child"]

            # 父片段补报上来，子片段照旧挪回真实父片段下
            svc2.ingest(
                [span("t1", "p1", "root", "checkout", start + 50, start + 1_500)]
            )
            tree = svc2.get_tree("t1")
            assert tree["complete"]
            assert all(n["type"] != "placeholder" for n in tree["roots"])
            root = next(n for n in tree["roots"] if n["span_id"] == "root")
            p1 = next(c for c in root["children"] if c["span_id"] == "p1")
            assert [c["span_id"] for c in p1["children"]] == ["child"]
        finally:
            svc2.close()

    def test_expired_span_stays_under_placeholder_after_restart(
        self, tmp_path
    ) -> None:
        """重启前已等超时落占位节点的片段，重启后仍在占位节点下。"""
        db = str(tmp_path / "trace.db")
        cfg = Settings(database_path=db, max_pending_wait_seconds=1)
        svc1 = TracingService(cfg)
        start = now_ms() - 120_000
        svc1.ingest(
            [span("t1", "orphan", "ghost", "inventory", start, start + 500)]
        )
        time.sleep(1.2)  # 等满 1 秒最长等待，重启前就已落占位节点
        tree = svc1.get_tree("t1")
        assert tree["complete"]
        assert tree["roots"][0]["committed"] is True
        svc1.close()

        svc2 = TracingService(cfg)
        try:
            tree = svc2.get_tree("t1")
            assert tree["pending_count"] == 0
            assert tree["complete"]
            placeholder = tree["roots"][0]
            assert placeholder["type"] == "placeholder"
            assert placeholder["committed"] is True
            assert [c["span_id"] for c in placeholder["children"]] == ["orphan"]

            # 父片段在重启后补到，照旧挪回真实父片段下
            svc2.ingest(
                [span("t1", "ghost", None, "gateway", start - 100, start + 1_000)]
            )
            tree = svc2.get_tree("t1")
            ghost = tree["roots"][0]
            assert ghost["span_id"] == "ghost"
            assert [c["span_id"] for c in ghost["children"]] == ["orphan"]
        finally:
            svc2.close()
