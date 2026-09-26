"""SSE 实时事件总线。

后端每接收一批片段就向所有订阅者广播 span_update；
依赖图变化按防抖间隔广播 graph_update。前端据此增量刷新，
不用轮询。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self, graph_debounce_seconds: float = 1.0) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._graph_debounce = graph_debounce_seconds
        self._last_graph_push = 0.0
        self._graph_dirty = False

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者直接丢事件，不能阻塞上报链路
                pass

    def publish_spans(self, trace_ids: list[str], accepted: int) -> None:
        self._publish(
            {
                "event": "span_update",
                "data": {"trace_ids": trace_ids, "accepted": accepted},
            }
        )
        self._graph_dirty = True
        self._maybe_push_graph()

    def _maybe_push_graph(self) -> None:
        now = time.monotonic()
        if now - self._last_graph_push >= self._graph_debounce:
            self._last_graph_push = now
            self._graph_dirty = False
            self._publish({"event": "graph_update", "data": {}})

    def flush_graph(self) -> None:
        """强制把积压的图更新推出去（例如防抖窗口刚结束的定时 flush）。"""
        if self._graph_dirty:
            self._graph_dirty = False
            self._last_graph_push = time.monotonic()
            self._publish({"event": "graph_update", "data": {}})
