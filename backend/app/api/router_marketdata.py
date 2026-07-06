from fastapi import APIRouter

from app.services import http_handlers as handlers

router = APIRouter()

router.add_api_route("/api/market/summary", handlers.market_summary, methods=["GET"])
router.add_api_route("/api/reports", handlers.create_report, methods=["POST"])
router.add_api_route("/api/reports/{symbol}", handlers.get_report, methods=["GET"])
router.add_api_route("/api/research-runs", handlers.create_research_run_api, methods=["POST"])
router.add_api_route("/api/research-runs", handlers.list_research_runs, methods=["GET"])
router.add_api_route("/api/research-runs/compare", handlers.compare_research_runs, methods=["GET"])
router.add_api_route("/api/research-runs/{run_id}", handlers.get_research_run, methods=["GET"])
router.add_api_route("/api/liquidity/analyze", handlers.analyze_liquidity_api, methods=["POST"])
router.add_api_route("/api/shadow/extract", handlers.extract_shadow, methods=["POST"])
router.add_api_route("/api/shadow", handlers.list_shadow_profiles, methods=["GET"])
router.add_api_route("/api/shadow/{shadow_id}", handlers.get_shadow_profile, methods=["GET"])
router.add_api_route("/api/shadow/{shadow_id}/compare", handlers.compare_shadow, methods=["POST"])
router.add_api_route("/api/validation/run", handlers.run_validation_api, methods=["POST"])
router.add_api_route("/api/validation/runs", handlers.list_validation_runs, methods=["GET"])
router.add_api_route("/api/validation/runs/{run_id}", handlers.get_validation_run, methods=["GET"])
router.add_api_route("/api/memory", handlers.list_memory, methods=["GET"])
router.add_api_route("/api/memory/reflect", handlers.reflect_memory, methods=["POST"])
