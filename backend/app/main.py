from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router_marketdata import router as marketdata_router
from app.api.router_positions import router as positions_router
from app.api.router_review import router as review_router
from app.api.router_scout import router as scout_router
from app.api.router_system import router as system_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.derivatives.routes import router as derivatives_router
from app.notify.routes import router as notify_router
from app.onchain.routes import router as onchain_router
from app.paper.routes import router as paper_router
from app.toss.routes import router as toss_router
from app.stock_paper.routes import router as stock_paper_router
from app.services import runtime as service
from app.worker.manager import WorkerManager
from app.worker.routes import router as worker_router
from app.worker.runtime import set_worker_manager

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger("app.http")


@asynccontextmanager
async def lifespan(app: FastAPI):
    service.apply_engine_param_overrides()
    service.seed_demo_data()
    manager = WorkerManager(settings)
    set_worker_manager(manager)
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system_router)
app.include_router(marketdata_router)
app.include_router(positions_router)
app.include_router(review_router)
app.include_router(scout_router)
app.include_router(derivatives_router)
app.include_router(notify_router)
app.include_router(worker_router)
app.include_router(paper_router)
app.include_router(onchain_router)
app.include_router(toss_router)
app.include_router(stock_paper_router)


@app.middleware("http")
async def log_server_errors(request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "api request failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )
        raise
    if response.status_code >= 500:
        logger.error(
            "api returned 5xx",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "client": request.client.host if request.client else None,
            },
        )
    return response
