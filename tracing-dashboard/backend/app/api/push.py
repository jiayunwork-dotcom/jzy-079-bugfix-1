"""SSE 推送路由：新片段与依赖图更新实时推给前端。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ..service import TracingService
from .deps import get_service

router = APIRouter(prefix="/api")


@router.get("/stream")
async def stream(
    request: Request, service: TracingService = Depends(get_service)
) -> EventSourceResponse:
    queue = service.bus.subscribe()

    async def events():
        try:
            # 连接建立立刻发一个 hello，前端据此确认推送通道可用
            yield {"event": "hello", "data": json.dumps({"ok": True})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "event": event["event"],
                        "data": json.dumps(event["data"]),
                    }
                except asyncio.TimeoutError:
                    # 保活心跳，防止代理断连
                    yield {"event": "ping", "data": "{}"}
        finally:
            service.bus.unsubscribe(queue)

    return EventSourceResponse(events())
