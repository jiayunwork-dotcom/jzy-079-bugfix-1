"""门面服务：把存储、拼接器、聚合器、事件总线串起来。

HTTP 路由只跟 TracingService 打交道，不直接碰内部模块。
"""
from __future__ import annotations

import time
from typing import Optional

from .config import Settings
from .dependencies import aggregate_edges
from .events import EventBus
from .models import SpanRecord
from .storage import SpanStore
from .tree import TraceAssembler

WINDOWS = {"1h": 3600 * 1000.0, "1d": 24 * 3600 * 1000.0}


class TracingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = SpanStore(settings.database_path)
        self.assembler = TraceAssembler(settings.max_pending_wait_ms)
        self.bus = EventBus(settings.graph_push_debounce_seconds)
        # 重启后从持久化数据重建拼接状态
        self.assembler.load_from_storage(self.store.all_spans())

    # ---------------------------------------------------------------- 上报

    def ingest(self, spans: list[SpanRecord]) -> dict:
        """入库一批已校验片段，返回每条的接收结果。"""
        now_ms = time.time() * 1000.0
        results: list[dict] = []
        touched_traces: set[str] = set()
        for span in spans:
            stored = self.store.insert_span(span, now_ms)
            outcome = self.assembler.add_span(span, now_ms=now_ms)
            status = "added" if (stored and outcome == "added") else "duplicate"
            results.append(
                {"trace_id": span.trace_id, "span_id": span.span_id, "status": status}
            )
            if status == "added":
                touched_traces.add(span.trace_id)
        # 顺手推进一次超时检查，让等不到父片段的子片段及时落入占位节点
        flushed = self.assembler.flush_expired(now_ms)
        touched_traces.update(flushed)
        if touched_traces:
            self.bus.publish_spans(sorted(touched_traces), len(spans))
        return {
            "accepted": sum(1 for r in results if r["status"] == "added"),
            "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
            "results": results,
        }

    # ---------------------------------------------------------------- 查询

    def get_tree(self, trace_id: str) -> Optional[dict]:
        return self.assembler.build_tree(trace_id)

    def list_traces(
        self, trace_id: Optional[str], service: Optional[str], limit: int
    ) -> list[dict]:
        return self.store.list_traces(trace_id=trace_id, service=service, limit=limit)

    def get_graph(self, window: str) -> dict:
        """按时间窗口聚合依赖图。窗口外的片段完全不参与，新旧窗口不串。"""
        window_ms = WINDOWS[window]
        since = time.time() * 1000.0 - window_ms
        recent = self.store.spans_starting_since(since)
        # 只统计本次窗口内的 trace 的边；逐 trace 从拼接器取真实父子边
        edges: list[tuple[str, str, float]] = []
        seen_traces: set[str] = set()
        for span in recent:
            if span.trace_id in seen_traces:
                continue
            seen_traces.add(span.trace_id)
            edges.extend(self.assembler.extract_edges(span.trace_id))
        graph = aggregate_edges(edges)
        graph["window"] = window
        graph["since"] = since
        return graph

    # ---------------------------------------------------------------- 维护

    def tick(self) -> None:
        """后台周期任务：推进超时占位 + 冲刷防抖的图更新。"""
        flushed = self.assembler.flush_expired()
        if flushed:
            self.bus.publish_spans(sorted(flushed), 0)
        self.bus.flush_graph()

    def close(self) -> None:
        self.store.close()
