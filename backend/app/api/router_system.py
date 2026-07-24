from fastapi import APIRouter

from app.api.paper_diagnosis import paper_diagnosis
from app.core.config import get_settings
from app.services import http_handlers as handlers
from app.toss.auth_diagnosis import diagnose_toss_auth

router = APIRouter()

router.add_api_route("/health", handlers.health, methods=["GET"])
router.add_api_route("/api/system/status", handlers.system_status, methods=["GET"])
router.add_api_route("/api/system/paper/diagnosis", paper_diagnosis, methods=["GET"])
router.add_api_route(
    "/api/system/bitget/test-connection",
    handlers.test_bitget_connection,
    methods=["POST"],
)


@router.get("/api/system/toss/auth-diagnosis")
async def toss_auth_diagnosis() -> dict:
    return await diagnose_toss_auth(get_settings())
