"""HTTP 层使用的请求/响应数据模型。

业务核心（拼接、聚合）不依赖这些模型，只使用轻量数据类 SpanRecord，
保证核心逻辑可以独立测试。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class SpanIn(BaseModel):
    # 字段都允许缺省为 None，统一交给 validation 层产出结构化错误
    model_config = ConfigDict(extra="forbid")

    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    service: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    status_code: Optional[int] = None


class SpanBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spans: list[SpanIn]


class SpanRecord:
    """核心逻辑里流转的已校验片段。"""

    __slots__ = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "service",
        "start_time",
        "end_time",
        "status_code",
    )

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        service: str,
        start_time: float,
        end_time: float,
        status_code: int,
    ) -> None:
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.service = service
        self.start_time = start_time
        self.end_time = end_time
        self.status_code = status_code

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "service": self.service,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status_code": self.status_code,
        }

    @classmethod
    def from_span_in(cls, span: SpanIn) -> "SpanRecord":
        return cls(
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            service=span.service,
            start_time=span.start_time,
            end_time=span.end_time,
            status_code=span.status_code,
        )

    @classmethod
    def from_row(cls, row: dict) -> "SpanRecord":
        return cls(
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            service=row["service"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            status_code=row["status_code"],
        )
