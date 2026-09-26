"""HTTP 路由：片段上报、请求检索、调用树、依赖图。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models import SpanBatchIn
from ..service import WINDOWS, TracingService
from ..validation import validate_batch
from .deps import get_service

router = APIRouter(prefix="/api")


@router.post("/spans", status_code=200)
def ingest_spans(
    batch: SpanBatchIn, service: TracingService = Depends(get_service)
) -> dict:
    """接收一批调用片段。

    合法片段入库并参与拼树；非法片段被拒绝，响应里带结构化错误，
    不影响同批其它片段。
    """
    accepted, rejected = validate_batch(batch.spans)
    result = service.ingest(accepted)
    return {
        "accepted": result["accepted"],
        "duplicates": result["duplicates"],
        "rejected": len(rejected),
        "errors": rejected,
        "results": result["results"],
    }


@router.get("/traces")
def list_traces(
    trace_id: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    svc: TracingService = Depends(get_service),
) -> dict:
    return {"traces": svc.list_traces(trace_id=trace_id, service=service, limit=limit)}


@router.get("/traces/{trace_id}")
def get_trace(
    trace_id: str, svc: TracingService = Depends(get_service)
) -> dict:
    tree = svc.get_tree(trace_id)
    if tree is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id} 不存在")
    return tree


@router.get("/graph")
def get_graph(
    window: str = Query("1h"),
    svc: TracingService = Depends(get_service),
) -> dict:
    if window not in WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的时间窗口 {window!r}，可选: {sorted(WINDOWS)}",
        )
    return svc.get_graph(window)
