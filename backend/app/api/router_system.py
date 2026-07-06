from fastapi import APIRouter

from app.services import http_handlers as handlers

router = APIRouter()

router.add_api_route("/health", handlers.health, methods=["GET"])
router.add_api_route("/api/system/status", handlers.system_status, methods=["GET"])
router.add_api_route(
    "/api/system/bitget/test-connection",
    handlers.test_bitget_connection,
    methods=["POST"],
)
