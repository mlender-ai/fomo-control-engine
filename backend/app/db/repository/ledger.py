from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID
from app.db.models import (
    AgentOutput,
    AutonomyLog,
    CalibrationSuggestion,
    DecisionMemory,
    EngineParamVersion,
    JudgmentLedgerEntry,
    JudgmentScore,
    ResearchRun,
    ShadowProfile,
    ValidationRun,
    utc_now,
)
from .base import _aware_dt, _dump_model, _json_cache_default


class MemoryLedgerRepositoryMixin:
    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        entries = self.judgments.setdefault(judgment.position_id, [])
        entries = [item for item in entries if item.id != judgment.id and item.judgment_id != judgment.judgment_id]
        entries.insert(0, judgment)
        self.judgments[judgment.position_id] = entries
        return judgment

    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]:
        return sorted(
            self.judgments.get(position_id, []),
            key=lambda item: item.as_of,
            reverse=True,
        )[:limit]

    def list_judgments_all(self, since: datetime | None = None, limit: int = 10000) -> list[JudgmentLedgerEntry]:
        entries = [entry for rows in self.judgments.values() for entry in rows]
        if since is not None:
            entries = [entry for entry in entries if _aware_dt(entry.as_of) >= since]
        return sorted(entries, key=lambda item: item.as_of, reverse=True)[:limit]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        self.judgment_scores[score.id] = score
        return score

    def list_judgment_scores(
        self,
        position_id: UUID | None = None,
        trade_id: UUID | None = None,
        limit: int = 500,
    ) -> list[JudgmentScore]:
        scores = list(self.judgment_scores.values())
        if position_id is not None:
            scores = [score for score in scores if score.position_id == position_id]
        if trade_id is not None:
            scores = [score for score in scores if score.trade_id == trade_id]
        return sorted(scores, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
        self.calibration_suggestions[suggestion.id] = suggestion
        return suggestion

    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None:
        return self.calibration_suggestions.get(suggestion_id)

    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]:
        suggestions = list(self.calibration_suggestions.values())
        if status:
            suggestions = [suggestion for suggestion in suggestions if suggestion.status == status]
        return sorted(suggestions, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_engine_param_version(self, version: EngineParamVersion) -> EngineParamVersion:
        for key, existing in list(self.engine_params.items()):
            if existing.param == version.param and existing.status == "active" and existing.id != version.id:
                self.engine_params[key] = existing.model_copy(update={"status": "superseded"})
        self.engine_params[version.id] = version
        return version

    def latest_engine_param(self, param: str) -> EngineParamVersion | None:
        versions = [version for version in self.engine_params.values() if version.param == param and version.status == "active"]
        if not versions:
            return None
        return sorted(versions, key=lambda item: item.approved_at, reverse=True)[0]

    def list_engine_params(self, limit: int = 100) -> list[EngineParamVersion]:
        return sorted(self.engine_params.values(), key=lambda item: item.approved_at, reverse=True)[:limit]

    def add_autonomy_log(self, log: AutonomyLog) -> AutonomyLog:
        self.autonomy_logs[log.id] = log
        return log

    def list_autonomy_logs(
        self,
        signature_key: str | None = None,
        new_state: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AutonomyLog]:
        logs = list(self.autonomy_logs.values())
        if signature_key:
            logs = [log for log in logs if log.signature_key == signature_key]
        if new_state:
            logs = [log for log in logs if log.new_state == new_state]
        if since is not None:
            logs = [log for log in logs if _aware_dt(log.created_at) >= since]
        return sorted(logs, key=lambda item: item.created_at, reverse=True)[:limit]

    def latest_autonomy_states(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for log in sorted(self.autonomy_logs.values(), key=lambda item: item.created_at, reverse=True):
            result.setdefault(log.signature_key, log.new_state)
        return result

    def add_research_run(self, run: ResearchRun) -> ResearchRun:
        self.research_runs[run.id] = run
        return run

    def get_research_run(self, run_id: UUID) -> ResearchRun | None:
        return self.research_runs.get(run_id)

    def list_research_runs(self, symbol: str | None = None, limit: int = 20) -> list[ResearchRun]:
        runs = list(self.research_runs.values())
        if symbol:
            runs = [run for run in runs if run.symbol == symbol.upper()]
        return sorted(runs, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_agent_output(self, output: AgentOutput) -> AgentOutput:
        self.agent_outputs.setdefault(output.research_run_id, []).append(output)
        return output

    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]:
        return sorted(
            self.agent_outputs.get(research_run_id, []),
            key=lambda item: item.created_at,
        )

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        self.shadow_profiles[profile.shadow_id] = profile
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        return self.shadow_profiles.get(shadow_id)

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        return sorted(
            self.shadow_profiles.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory:
        self.decision_memories[memory.id] = memory
        return memory

    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]:
        memories = list(self.decision_memories.values())
        if symbol:
            memories = [memory for memory in memories if memory.symbol in {symbol.upper(), None}]
        return sorted(memories, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_validation_run(self, run: ValidationRun) -> ValidationRun:
        self.validation_runs[run.id] = run
        return run

    def get_validation_run(self, run_id: UUID) -> ValidationRun | None:
        return self.validation_runs.get(run_id)

    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]:
        return sorted(
            self.validation_runs.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def get_calibration_report_cache(self, report_key: str) -> dict | None:
        cached = self.calibration_report_cache.get(report_key)
        return dict(cached) if isinstance(cached, dict) else None

    def upsert_calibration_report_cache(self, report_key: str, payload: dict) -> dict:
        cached = {"payload": dict(payload), "computed_at": utc_now().isoformat()}
        self.calibration_report_cache[report_key] = cached
        return dict(cached)


class SQLiteLedgerRepositoryMixin:
    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO judgment_ledger
                    (id, position_id, judgment_id, as_of, type, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(judgment.id),
                    str(judgment.position_id),
                    judgment.judgment_id,
                    judgment.as_of.isoformat(),
                    judgment.type,
                    judgment.created_at.isoformat(),
                    _dump_model(judgment),
                ),
            )
        return judgment

    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM judgment_ledger WHERE position_id = ? ORDER BY as_of DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [JudgmentLedgerEntry.model_validate_json(row["payload"]) for row in rows]

    def list_judgments_all(self, since: datetime | None = None, limit: int = 10000) -> list[JudgmentLedgerEntry]:
        query = "SELECT payload FROM judgment_ledger"
        params: list[str | int] = []
        if since is not None:
            query += " WHERE as_of >= ?"
            params.append(since.isoformat())
        query += " ORDER BY as_of DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [JudgmentLedgerEntry.model_validate_json(row["payload"]) for row in rows]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO judgment_scores
                    (id, position_id, trade_id, judgment_id, outcome, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(score.id),
                    str(score.position_id),
                    str(score.trade_id) if score.trade_id else None,
                    score.judgment_id,
                    score.outcome,
                    score.created_at.isoformat(),
                    _dump_model(score),
                ),
            )
        return score

    def list_judgment_scores(
        self,
        position_id: UUID | None = None,
        trade_id: UUID | None = None,
        limit: int = 500,
    ) -> list[JudgmentScore]:
        query = "SELECT payload FROM judgment_scores"
        clauses: list[str] = []
        params: list[str | int] = []
        if position_id is not None:
            clauses.append("position_id = ?")
            params.append(str(position_id))
        if trade_id is not None:
            clauses.append("trade_id = ?")
            params.append(str(trade_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [JudgmentScore.model_validate_json(row["payload"]) for row in rows]

    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO calibration_suggestions
                    (id, status, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(suggestion.id),
                    suggestion.status,
                    suggestion.created_at.isoformat(),
                    _dump_model(suggestion),
                ),
            )
        return suggestion

    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM calibration_suggestions WHERE id = ?",
                (str(suggestion_id),),
            ).fetchone()
        return CalibrationSuggestion.model_validate_json(row["payload"]) if row else None

    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]:
        query = "SELECT payload FROM calibration_suggestions"
        params: tuple[str | int, ...]
        if status:
            query += " WHERE status = ?"
            params = (status, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [CalibrationSuggestion.model_validate_json(row["payload"]) for row in rows]

    def add_engine_param_version(self, version: EngineParamVersion) -> EngineParamVersion:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM engine_params WHERE param = ? AND status = 'active'",
                (version.param,),
            ).fetchall()
            for row in rows:
                existing = EngineParamVersion.model_validate_json(row["payload"])
                if existing.id == version.id:
                    continue
                superseded = existing.model_copy(update={"status": "superseded"})
                connection.execute(
                    "UPDATE engine_params SET status = ?, payload = ? WHERE id = ?",
                    (superseded.status, _dump_model(superseded), str(superseded.id)),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO engine_params
                    (id, param, status, approved_at, suggestion_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(version.id),
                    version.param,
                    version.status,
                    version.approved_at.isoformat(),
                    str(version.suggestion_id) if version.suggestion_id else None,
                    _dump_model(version),
                ),
            )
        return version

    def latest_engine_param(self, param: str) -> EngineParamVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM engine_params WHERE param = ? AND status = 'active' ORDER BY approved_at DESC LIMIT 1",
                (param,),
            ).fetchone()
        return EngineParamVersion.model_validate_json(row["payload"]) if row else None

    def list_engine_params(self, limit: int = 100) -> list[EngineParamVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM engine_params ORDER BY approved_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [EngineParamVersion.model_validate_json(row["payload"]) for row in rows]

    def add_autonomy_log(self, log: AutonomyLog) -> AutonomyLog:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO autonomy_log
                    (id, signature_key, new_state, transition, autonomous, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(log.id),
                    log.signature_key,
                    log.new_state,
                    log.transition,
                    1 if log.autonomous else 0,
                    log.created_at.isoformat(),
                    _dump_model(log),
                ),
            )
        return log

    def list_autonomy_logs(
        self,
        signature_key: str | None = None,
        new_state: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AutonomyLog]:
        query = "SELECT payload FROM autonomy_log"
        clauses: list[str] = []
        params: list[str | int] = []
        if signature_key:
            clauses.append("signature_key = ?")
            params.append(signature_key)
        if new_state:
            clauses.append("new_state = ?")
            params.append(new_state)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_aware_dt(since).isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [AutonomyLog.model_validate_json(row["payload"]) for row in rows]

    def latest_autonomy_states(self) -> dict[str, str]:
        # SQLite bare-column + MAX 집계: 각 그룹에서 MAX(created_at) 행의 컬럼을 반환.
        with self._connect() as connection:
            rows = connection.execute("SELECT signature_key, new_state, MAX(created_at) FROM autonomy_log GROUP BY signature_key").fetchall()
        return {str(row["signature_key"]): str(row["new_state"]) for row in rows}

    def add_research_run(self, run: ResearchRun) -> ResearchRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO research_runs
                    (id, symbol, timeframe, report_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.id),
                    run.symbol.upper(),
                    run.timeframe,
                    str(run.report_id),
                    run.created_at.isoformat(),
                    _dump_model(run),
                ),
            )
        return run

    def get_research_run(self, run_id: UUID) -> ResearchRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM research_runs WHERE id = ?", (str(run_id),)).fetchone()
        return ResearchRun.model_validate_json(row["payload"]) if row else None

    def list_research_runs(self, symbol: str | None = None, limit: int = 20) -> list[ResearchRun]:
        query = "SELECT payload FROM research_runs"
        params: tuple = ()
        if symbol:
            query += " WHERE symbol = ?"
            params = (symbol.upper(),)
        query += " ORDER BY created_at DESC LIMIT ?"
        params = (*params, limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ResearchRun.model_validate_json(row["payload"]) for row in rows]

    def add_agent_output(self, output: AgentOutput) -> AgentOutput:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_outputs
                    (id, research_run_id, agent_name, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(output.id),
                    str(output.research_run_id),
                    output.agent_name,
                    output.created_at.isoformat(),
                    _dump_model(output),
                ),
            )
        return output

    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_outputs WHERE research_run_id = ? ORDER BY created_at ASC",
                (str(research_run_id),),
            ).fetchall()
        return [AgentOutput.model_validate_json(row["payload"]) for row in rows]

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO shadow_profiles (shadow_id, created_at, payload) VALUES (?, ?, ?)",
                (
                    profile.shadow_id,
                    profile.created_at.isoformat(),
                    _dump_model(profile),
                ),
            )
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM shadow_profiles WHERE shadow_id = ?", (shadow_id,)).fetchone()
        return ShadowProfile.model_validate_json(row["payload"]) if row else None

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM shadow_profiles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ShadowProfile.model_validate_json(row["payload"]) for row in rows]

    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_memories
                    (id, symbol, memory_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(memory.id),
                    memory.symbol.upper() if memory.symbol else None,
                    memory.memory_type,
                    memory.created_at.isoformat(),
                    _dump_model(memory),
                ),
            )
        return memory

    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]:
        if symbol:
            query = "SELECT payload FROM decision_memories WHERE symbol = ? OR symbol IS NULL ORDER BY created_at DESC LIMIT ?"
            params = (symbol.upper(), limit)
        else:
            query = "SELECT payload FROM decision_memories ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DecisionMemory.model_validate_json(row["payload"]) for row in rows]

    def add_validation_run(self, run: ValidationRun) -> ValidationRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO validation_runs
                    (id, symbol, timeframe, strategy_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.id),
                    run.symbol.upper(),
                    run.timeframe,
                    run.strategy_type,
                    run.created_at.isoformat(),
                    _dump_model(run),
                ),
            )
        return run

    def get_validation_run(self, run_id: UUID) -> ValidationRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM validation_runs WHERE id = ?", (str(run_id),)).fetchone()
        return ValidationRun.model_validate_json(row["payload"]) if row else None

    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM validation_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ValidationRun.model_validate_json(row["payload"]) for row in rows]

    def get_calibration_report_cache(self, report_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, computed_at FROM calibration_report_cache WHERE report_key = ?",
                (report_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            return None
        return {"payload": payload, "computed_at": row["computed_at"]} if isinstance(payload, dict) else None

    def upsert_calibration_report_cache(self, report_key: str, payload: dict) -> dict:
        computed_at = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO calibration_report_cache (report_key, payload, computed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(report_key) DO UPDATE SET
                    payload = excluded.payload,
                    computed_at = excluded.computed_at
                """,
                (
                    report_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_cache_default),
                    computed_at,
                ),
            )
        return {"payload": dict(payload), "computed_at": computed_at}
