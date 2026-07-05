from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.scout_routes import router as scout_router
from app.core.config import get_settings
from app.worker.manager import WorkerManager
from app.worker.routes import router as worker_router
from app.worker.runtime import set_worker_manager

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.include_router(router)
app.include_router(scout_router)
app.include_router(worker_router)
