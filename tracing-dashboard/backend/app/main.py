"""ASGI 入口：装配应用、生命周期、后台周期任务。"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import http as http_routes
from .api import push as push_routes
from .config import Settings, settings
from .service import TracingService


def create_app(app_settings: Settings | None = None) -> FastAPI:
    cfg = app_settings or settings
    app = FastAPI(title="tracing-dashboard", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(http_routes.router)
    app.include_router(push_routes.router)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        service = TracingService(cfg)
        _app.state.tracing_service = service

        async def ticker() -> None:
            while True:
                await asyncio.sleep(1.0)
                service.tick()

        task = asyncio.create_task(ticker())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            service.close()

    app.router.lifespan_context = lifespan
    return app


app = create_app()
