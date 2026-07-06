from fastapi import APIRouter

from app.services import http_handlers as handlers

router = APIRouter()

router.add_api_route("/api/trades", handlers.list_trades, methods=["GET"])
router.add_api_route("/api/trades/{trade_id}", handlers.get_trade, methods=["GET"])
router.add_api_route("/api/trades/{trade_id}/review", handlers.review_trade, methods=["POST"])
router.add_api_route("/api/trades/{trade_id}/timeline", handlers.get_trade_timeline, methods=["GET"])
router.add_api_route("/api/trades/{trade_id}/memo", handlers.update_trade_memo, methods=["PATCH"])
router.add_api_route("/api/review/calibration", handlers.review_calibration, methods=["GET"])
router.add_api_route(
    "/api/review/calibration/weekly",
    handlers.review_weekly_calibration,
    methods=["GET"],
)
router.add_api_route(
    "/api/review/calibration/suggestions/{suggestion_id}/approve",
    handlers.approve_calibration_suggestion,
    methods=["POST"],
)
router.add_api_route(
    "/api/review/calibration/suggestions/{suggestion_id}/reject",
    handlers.reject_calibration_suggestion,
    methods=["POST"],
)
