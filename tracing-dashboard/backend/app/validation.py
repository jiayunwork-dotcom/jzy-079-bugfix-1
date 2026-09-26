"""片段校验：脏数据在这里被拒绝，绝不带着进入拼接逻辑。

校验规则：
* trace_id / span_id / service 缺失或为空白字符串
* parent_span_id 允许为 null（根片段），但不允许是空字符串
* start_time / end_time 缺失、非数值（NaN/Inf）
* end_time 早于 start_time
* status_code 缺失或不是整数

每个被拒绝的片段返回一条结构化错误：index（批次中的下标）、code、message、fields。
"""
from __future__ import annotations

import math
from numbers import Real
from typing import Optional

from .models import SpanIn, SpanRecord

# 结构化错误码，前端/调用方可据此做机器判断
ERR_MISSING_FIELD = "MISSING_FIELD"
ERR_INVALID_TIME = "INVALID_TIME"
ERR_TIME_INVERTED = "TIME_INVERTED"
ERR_INVALID_STATUS = "INVALID_STATUS"


class SpanValidationError(Exception):
    def __init__(self, code: str, message: str, fields: list[str]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields


def _is_blank(value: Optional[str]) -> bool:
    return value is None or not isinstance(value, str) or value.strip() == ""


def validate_span(span: SpanIn) -> SpanRecord:
    """校验单个片段，通过则返回 SpanRecord，否则抛 SpanValidationError。"""
    missing: list[str] = []
    if _is_blank(span.trace_id):
        missing.append("trace_id")
    if _is_blank(span.span_id):
        missing.append("span_id")
    if _is_blank(span.service):
        missing.append("service")
    # 根片段父编号为 null 是合法的；空字符串不是
    if span.parent_span_id is not None and _is_blank(span.parent_span_id):
        missing.append("parent_span_id")
    if span.start_time is None:
        missing.append("start_time")
    if span.end_time is None:
        missing.append("end_time")
    if span.status_code is None:
        missing.append("status_code")
    if missing:
        raise SpanValidationError(
            ERR_MISSING_FIELD,
            f"必填字段缺失或为空: {', '.join(missing)}",
            missing,
        )

    # bool 是 int 的子类，但 True/False 不是合法状态码
    if isinstance(span.status_code, bool) or not isinstance(span.status_code, int):
        raise SpanValidationError(
            ERR_INVALID_STATUS,
            "status_code 必须是整数",
            ["status_code"],
        )

    for value, field in (
        (span.start_time, "start_time"),
        (span.end_time, "end_time"),
    ):
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
            raise SpanValidationError(
                ERR_INVALID_TIME,
                f"{field} 不是有限数值",
                [field],
            )

    if span.end_time < span.start_time:  # type: ignore[operator]
        raise SpanValidationError(
            ERR_TIME_INVERTED,
            f"结束时间({span.end_time})早于开始时间({span.start_time})",
            ["start_time", "end_time"],
        )

    return SpanRecord.from_span_in(span)


def validate_batch(spans: list[SpanIn]) -> tuple[list[SpanRecord], list[dict]]:
    """校验整批片段，返回 (合法片段, 结构化错误列表)。

    单条非法不影响同批其它片段入树。
    """
    accepted: list[SpanRecord] = []
    rejected: list[dict] = []
    for index, span in enumerate(spans):
        try:
            accepted.append(validate_span(span))
        except SpanValidationError as exc:
            rejected.append(
                {
                    "index": index,
                    "span_id": span.span_id if not _is_blank(span.span_id) else None,
                    "trace_id": span.trace_id if not _is_blank(span.trace_id) else None,
                    "code": exc.code,
                    "message": exc.message,
                    "fields": exc.fields,
                }
            )
    return accepted, rejected
