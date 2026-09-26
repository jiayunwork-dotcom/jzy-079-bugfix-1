"""FastAPI 依赖注入：从 app.state 取门面服务。"""
from __future__ import annotations

from fastapi import Request

from ..service import TracingService


def get_service(request: Request) -> TracingService:
    return request.app.state.tracing_service
