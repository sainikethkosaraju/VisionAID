import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import auth, devices, facility, gateway, incidents, people, ws
from app.core.config import get_settings
from app.database.session import session_factory
from app.services.realtime import hub
from app.workers import scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.bind_loop(asyncio.get_running_loop())
    stop = asyncio.Event()
    task = None
    if get_settings().run_workers_in_process:
        task = asyncio.create_task(scheduler.run_forever(stop))
    yield
    stop.set()
    if task:
        with contextlib.suppress(Exception):
            await task


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="VisionAID API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if s.env != "production" else None,
        redoc_url=None,
    )
    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if s.env == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

    api = APIRouter(prefix="/api/v1")
    for r in (
        auth.router,
        people.router,
        devices.router,
        gateway.router,
        incidents.router,
        facility.router,
        ws.router,
    ):
        api.include_router(r)
    app.include_router(api)

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz():
        with session_factory()() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
