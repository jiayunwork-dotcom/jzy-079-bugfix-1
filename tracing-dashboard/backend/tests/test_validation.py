"""片段校验测试：脏数据必须被拒绝并给出结构化错误。"""
from __future__ import annotations

import pytest

from app.models import SpanIn
from app.validation import (
    ERR_INVALID_TIME,
    ERR_MISSING_FIELD,
    ERR_TIME_INVERTED,
    validate_batch,
    validate_span,
)


def valid_span_dict(**overrides) -> dict:
    base = {
        "trace_id": "t1",
        "span_id": "s1",
        "parent_span_id": None,
        "service": "svc",
        "start_time": 100.0,
        "end_time": 200.0,
        "status_code": 200,
    }
    base.update(overrides)
    return base


class TestValidSpans:
    def test_valid_span_accepted(self) -> None:
        record = validate_span(SpanIn(**valid_span_dict()))
        assert record.trace_id == "t1"
        assert record.duration == 100.0

    def test_root_span_null_parent_ok(self) -> None:
        record = validate_span(SpanIn(**valid_span_dict(parent_span_id=None)))
        assert record.parent_span_id is None


class TestMissingFields:
    @pytest.mark.parametrize("field", ["trace_id", "span_id", "service"])
    def test_blank_id_rejected(self, field: str) -> None:
        accepted, rejected = validate_batch(
            [SpanIn(**valid_span_dict(**{field: "  "}))]
        )
        assert accepted == []
        assert len(rejected) == 1
        err = rejected[0]
        assert err["code"] == ERR_MISSING_FIELD
        assert field in err["fields"]
        assert err["message"]
        assert err["index"] == 0

    @pytest.mark.parametrize("field", ["trace_id", "span_id"])
    def test_missing_id_rejected(self, field: str) -> None:
        payload = valid_span_dict()
        del payload[field]
        accepted, rejected = validate_batch([SpanIn(**payload)])
        assert accepted == []
        assert rejected[0]["code"] == ERR_MISSING_FIELD
        assert field in rejected[0]["fields"]

    def test_empty_parent_string_rejected(self) -> None:
        """父编号要么 null（根），要么非空字符串；空串是脏数据。"""
        accepted, rejected = validate_batch(
            [SpanIn(**valid_span_dict(parent_span_id=""))]
        )
        assert accepted == []
        assert "parent_span_id" in rejected[0]["fields"]


class TestTimes:
    @pytest.mark.parametrize("field", ["start_time", "end_time"])
    def test_missing_time_rejected(self, field: str) -> None:
        payload = valid_span_dict()
        del payload[field]
        accepted, rejected = validate_batch([SpanIn(**payload)])
        assert accepted == []
        assert rejected[0]["code"] == ERR_MISSING_FIELD
        assert field in rejected[0]["fields"]

    def test_end_before_start_rejected(self) -> None:
        accepted, rejected = validate_batch(
            [SpanIn(**valid_span_dict(start_time=200.0, end_time=100.0))]
        )
        assert accepted == []
        err = rejected[0]
        assert err["code"] == ERR_TIME_INVERTED
        assert set(err["fields"]) == {"start_time", "end_time"}

    def test_nan_time_rejected(self) -> None:
        accepted, rejected = validate_batch(
            [SpanIn(**valid_span_dict(start_time=float("nan")))]
        )
        assert accepted == []
        assert rejected[0]["code"] == ERR_INVALID_TIME


class TestBatchIsolation:
    def test_bad_span_does_not_block_good_ones(self) -> None:
        """同批里一条脏数据不影响其它合法片段通过校验。"""
        spans = [
            SpanIn(**valid_span_dict(span_id="good-1")),
            SpanIn(**valid_span_dict(span_id="bad", end_time=1.0)),  # 时间倒置
            SpanIn(**valid_span_dict(span_id="good-2")),
        ]
        accepted, rejected = validate_batch(spans)
        assert [s.span_id for s in accepted] == ["good-1", "good-2"]
        assert len(rejected) == 1
        assert rejected[0]["span_id"] == "bad"
        assert rejected[0]["index"] == 1
