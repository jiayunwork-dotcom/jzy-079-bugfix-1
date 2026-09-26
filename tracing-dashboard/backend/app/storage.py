"""SQLite 持久化：只存片段本身。

依赖图是查询时按时间窗口实时聚合的，不做落库——窗口切换（1h/1d）时
直接重新聚合，从根上杜绝新旧窗口的边混在一起。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

from .models import SpanRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    trace_id       TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    parent_span_id TEXT,
    service        TEXT NOT NULL,
    start_time     REAL NOT NULL,
    end_time       REAL NOT NULL,
    status_code    INTEGER NOT NULL,
    received_at    REAL NOT NULL,
    PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans (trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_start ON spans (start_time);
"""


class SpanStore:
    def __init__(self, path: str) -> None:
        self._path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def insert_span(self, span: SpanRecord, received_at_ms: float) -> bool:
        """插入片段；(trace_id, span_id) 已存在则忽略。返回是否新插入。"""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO spans
                  (trace_id, span_id, parent_span_id, service,
                   start_time, end_time, status_code, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.trace_id,
                    span.span_id,
                    span.parent_span_id,
                    span.service,
                    span.start_time,
                    span.end_time,
                    span.status_code,
                    received_at_ms,
                ),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def load_trace(self, trace_id: str) -> list[SpanRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time, span_id",
                (trace_id,),
            ).fetchall()
        return [SpanRecord.from_row(dict(r)) for r in rows]

    def all_spans(self) -> list[SpanRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans ORDER BY start_time, span_id"
            ).fetchall()
        return [SpanRecord.from_row(dict(r)) for r in rows]

    def list_traces(
        self,
        trace_id: Optional[str] = None,
        service: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """检索请求列表，返回每个 trace 的摘要行。"""
        sql = "SELECT * FROM spans"
        where: list[str] = []
        params: list = []
        if trace_id:
            where.append("trace_id LIKE ?")
            params.append(f"%{trace_id}%")
        if service:
            where.append("service LIKE ?")
            params.append(f"%{service}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY start_time DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["trace_id"], []).append(row)

        summaries: list[dict] = []
        for tid, trace_rows in grouped.items():
            starts = min(r["start_time"] for r in trace_rows)
            ends = max(r["end_time"] for r in trace_rows)
            services = sorted({r["service"] for r in trace_rows})
            errors = sum(1 for r in trace_rows if r["status_code"] >= 400)
            summaries.append(
                {
                    "trace_id": tid,
                    "span_count": len(trace_rows),
                    "services": services,
                    "start_time": starts,
                    "duration": ends - starts,
                    "error_count": errors,
                }
            )
        summaries.sort(key=lambda s: -s["start_time"])
        return summaries[:limit]

    def spans_starting_since(self, since_ms: float) -> list[SpanRecord]:
        """取开始时间在窗口内的片段，供依赖图聚合。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans WHERE start_time >= ? ORDER BY start_time",
                (since_ms,),
            ).fetchall()
        return [SpanRecord.from_row(dict(r)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
