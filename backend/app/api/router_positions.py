from fastapi import APIRouter

from app.services import http_handlers as handlers

router = APIRouter()

router.add_api_route("/api/positions", handlers.list_positions, methods=["GET"])
router.add_api_route("/api/positions", handlers.create_position, methods=["POST"])
router.add_api_route("/api/account/bitget/positions", handlers.list_bitget_positions, methods=["GET"])
router.add_api_route(
    "/api/account/bitget/sync-positions",
    handlers.sync_bitget_positions,
    methods=["POST"],
)
router.add_api_route("/api/live/positions", handlers.list_live_positions, methods=["GET"])
router.add_api_route("/api/live/positions/sync", handlers.sync_live_positions, methods=["POST"])
router.add_api_route("/api/live/positions/{position_id}", handlers.get_live_position, methods=["GET"])
router.add_api_route(
    "/api/live/positions/{position_id}/chart-analysis",
    handlers.get_position_chart_analysis,
    methods=["GET"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/pattern-matrix",
    handlers.get_position_pattern_matrix,
    methods=["GET"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/deepdive",
    handlers.get_position_deepdive,
    methods=["GET"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/snapshots",
    handlers.get_position_snapshots,
    methods=["GET"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/analyze",
    handlers.analyze_live_position,
    methods=["POST"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/insight",
    handlers.create_position_insight,
    methods=["POST"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/events",
    handlers.get_position_events,
    methods=["GET"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/memo",
    handlers.update_position_memo,
    methods=["PATCH"],
)
router.add_api_route(
    "/api/live/positions/{position_id}/record-exit",
    handlers.record_live_position_exit,
    methods=["POST"],
)
router.add_api_route("/api/positions/{position_id}/monitor", handlers.monitor_position, methods=["POST"])
router.add_api_route("/api/positions/{position_id}/exit", handlers.exit_position, methods=["POST"])
