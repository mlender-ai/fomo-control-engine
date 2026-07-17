from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID
from app.db.models import ArmedSetup, CatalogSymbol, EntryIntent, EntryScenario, ScoutSnapshot, UniverseDiscovery, WatchlistItem, utc_now
from .base import _dump_model, _same_directional_bar


class MemoryScoutRepositoryMixin:
    def add_scout_snapshot(self, snapshot: ScoutSnapshot) -> ScoutSnapshot:
        self.scout_snapshots[snapshot.id] = snapshot
        return snapshot

    def list_scout_snapshots(self, symbol: str | None = None, limit: int = 100) -> list[ScoutSnapshot]:
        snapshots = list(self.scout_snapshots.values())
        if symbol:
            snapshots = [snapshot for snapshot in snapshots if snapshot.symbol.upper() == symbol.upper()]
        return sorted(snapshots, key=lambda item: item.as_of, reverse=True)[:limit]

    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None) -> ScoutSnapshot | None:
        snapshots = self.list_scout_snapshots(symbol=symbol, limit=500)
        if timeframe:
            snapshots = [snapshot for snapshot in snapshots if snapshot.timeframe == timeframe]
        return snapshots[0] if snapshots else None

    def get_directional_state(self, symbol: str, timeframe: str) -> dict | None:
        state = self.directional_states.get((symbol.upper(), timeframe))
        return dict(state) if isinstance(state, dict) else None

    def upsert_directional_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        key = (symbol.upper(), timeframe)
        current = self.directional_states.get(key)
        if _same_directional_bar(current, state):
            return False
        persisted = dict(state)
        persisted["updated_at"] = utc_now().isoformat()
        self.directional_states[key] = persisted
        return True

    def list_directional_states(self, limit: int = 100) -> list[dict]:
        rows = [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "stance": state.get("stance"),
                "since": state.get("since"),
                "last_bar_at": state.get("last_bar_at"),
                "updated_at": state.get("updated_at"),
            }
            for (symbol, timeframe), state in self.directional_states.items()
        ]
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:limit]

    def upsert_armed_setup(self, setup: ArmedSetup) -> ArmedSetup:
        self.armed_setups[setup.id] = setup
        return setup

    def get_armed_setup(self, setup_id: UUID) -> ArmedSetup | None:
        return self.armed_setups.get(setup_id)

    def list_armed_setups(self, symbol: str | None = None, status: str | None = None, limit: int = 200) -> list[ArmedSetup]:
        setups = list(self.armed_setups.values())
        if symbol:
            setups = [setup for setup in setups if setup.symbol.upper() == symbol.upper()]
        if status:
            setups = [setup for setup in setups if setup.status == status]
        return sorted(setups, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_entry_intent(self, intent: EntryIntent) -> EntryIntent:
        normalized = intent.symbol.upper()
        self.entry_intents[intent.id] = intent.model_copy(update={"symbol": normalized})
        return self.entry_intents[intent.id]

    def get_entry_intent(self, intent_id: UUID) -> EntryIntent | None:
        return self.entry_intents.get(intent_id)

    def list_entry_intents(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[EntryIntent]:
        intents = list(self.entry_intents.values())
        if symbol:
            intents = [intent for intent in intents if intent.symbol.upper() == symbol.upper()]
        if status:
            intents = [intent for intent in intents if intent.status == status]
        return sorted(intents, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_universe_discovery(self, discovery: UniverseDiscovery) -> UniverseDiscovery:
        normalized = discovery.model_copy(update={"symbol": discovery.symbol.upper()})
        self.universe_discoveries[normalized.id] = normalized
        return normalized

    def list_universe_discoveries(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[UniverseDiscovery]:
        discoveries = list(self.universe_discoveries.values())
        if symbol:
            discoveries = [item for item in discoveries if item.symbol.upper() == symbol.upper()]
        if status:
            discoveries = [item for item in discoveries if item.status == status]
        return sorted(discoveries, key=lambda item: item.created_at, reverse=True)[:limit]

    def list_recent_gate_passed_universe_discoveries(self, limit: int = 500) -> list[UniverseDiscovery]:
        recent = sorted(self.universe_discoveries.values(), key=lambda item: item.created_at, reverse=True)[:limit]
        return [item for item in recent if item.gate_passed]

    def list_watchlist(self) -> list[WatchlistItem]:
        return sorted(self.watchlist.values(), key=lambda item: item.added_at, reverse=True)

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        normalized = item.symbol.upper()
        self.watchlist[normalized] = item.model_copy(update={"symbol": normalized})
        return item

    def remove_watchlist_item(self, symbol: str) -> bool:
        return self.watchlist.pop(symbol.upper(), None) is not None

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        self.symbol_catalog = {item.symbol.upper(): item for item in symbols}
        return len(self.symbol_catalog)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        matches = (
            [
                item
                for symbol, item in self.symbol_catalog.items()
                if needle in symbol or needle in item.base_coin.upper() or needle in item.quote_coin.upper() or needle in item.asset_class.upper()
            ]
            if needle
            else list(self.symbol_catalog.values())
        )
        return sorted(matches, key=lambda item: (len(item.symbol), item.symbol))[:limit]

    def symbol_catalog_updated_at(self) -> datetime | None:
        if not self.symbol_catalog:
            return None
        return max(item.updated_at for item in self.symbol_catalog.values())

    def add_entry_scenario(self, scenario: EntryScenario) -> EntryScenario:
        self.entry_scenarios[scenario.id] = scenario
        return scenario

    def get_entry_scenario(self, scenario_id: UUID) -> EntryScenario | None:
        return self.entry_scenarios.get(scenario_id)

    def list_entry_scenarios(self, symbol: str | None = None, limit: int = 50) -> list[EntryScenario]:
        items = list(self.entry_scenarios.values())
        if symbol:
            items = [item for item in items if item.symbol.upper() == symbol.upper()]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def find_matching_scenario(self, symbol: str, direction: str, within_hours: int = 72) -> EntryScenario | None:
        from datetime import timedelta

        cutoff = utc_now() - timedelta(hours=within_hours)
        candidates = [
            item
            for item in self.entry_scenarios.values()
            if item.symbol.upper() == symbol.upper() and item.direction.value == direction and item.linked_position_id is None and item.created_at >= cutoff
        ]
        return max(candidates, key=lambda item: item.created_at, default=None)

    def link_scenario_position(self, scenario_id: UUID, position_id: UUID) -> EntryScenario | None:
        scenario = self.entry_scenarios.get(scenario_id)
        if scenario is None:
            return None
        updated = scenario.model_copy(update={"linked_position_id": position_id})
        self.entry_scenarios[scenario_id] = updated
        return updated


class SQLiteScoutRepositoryMixin:
    def add_scout_snapshot(self, snapshot: ScoutSnapshot) -> ScoutSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO scout_snapshots
                    (id, symbol, timeframe, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.timeframe,
                    snapshot.as_of.isoformat(),
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def list_scout_snapshots(self, symbol: str | None = None, limit: int = 100) -> list[ScoutSnapshot]:
        query = "SELECT payload FROM scout_snapshots"
        params: tuple[str | int, ...]
        if symbol:
            query += " WHERE symbol = ?"
            params = (symbol.upper(), limit)
        else:
            params = (limit,)
        query += " ORDER BY as_of DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ScoutSnapshot.model_validate_json(row["payload"]) for row in rows]

    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None) -> ScoutSnapshot | None:
        query = "SELECT payload FROM scout_snapshots WHERE symbol = ?"
        params: list[str | int] = [symbol.upper()]
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        query += " ORDER BY as_of DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return ScoutSnapshot.model_validate_json(row["payload"]) if row else None

    def get_directional_state(self, symbol: str, timeframe: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM directional_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row["state"])
        except (TypeError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def upsert_directional_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        normalized_symbol = symbol.upper()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM directional_states WHERE symbol = ? AND timeframe = ?",
                (normalized_symbol, timeframe),
            ).fetchone()
            current: dict | None = None
            if row is not None:
                try:
                    decoded = json.loads(row["state"])
                    current = decoded if isinstance(decoded, dict) else None
                except (TypeError, json.JSONDecodeError):
                    current = None
            if _same_directional_bar(current, state):
                return False
            updated_at = utc_now().isoformat()
            persisted = dict(state)
            persisted["updated_at"] = updated_at
            connection.execute(
                """
                INSERT INTO directional_states (symbol, timeframe, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (normalized_symbol, timeframe, json.dumps(persisted, ensure_ascii=False, sort_keys=True), updated_at),
            )
        return True

    def list_directional_states(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol, timeframe, state, updated_at FROM directional_states ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            try:
                state = json.loads(row["state"])
            except (TypeError, json.JSONDecodeError):
                state = {}
            state = state if isinstance(state, dict) else {}
            result.append(
                {
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "stance": state.get("stance"),
                    "since": state.get("since"),
                    "last_bar_at": state.get("last_bar_at"),
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def upsert_armed_setup(self, setup: ArmedSetup) -> ArmedSetup:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO armed_setups
                    (id, symbol, timeframe, source, setup_type, status, trigger_price, updated_at, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(setup.id),
                    setup.symbol.upper(),
                    setup.timeframe,
                    setup.source,
                    setup.setup_type,
                    setup.status,
                    setup.trigger_price,
                    setup.updated_at.isoformat(),
                    setup.created_at.isoformat(),
                    _dump_model(setup),
                ),
            )
        return setup

    def get_armed_setup(self, setup_id: UUID) -> ArmedSetup | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM armed_setups WHERE id = ?", (str(setup_id),)).fetchone()
        return ArmedSetup.model_validate_json(row["payload"]) if row else None

    def list_armed_setups(self, symbol: str | None = None, status: str | None = None, limit: int = 200) -> list[ArmedSetup]:
        query = "SELECT payload FROM armed_setups"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [ArmedSetup.model_validate_json(row["payload"]) for row in rows]

    def upsert_entry_intent(self, intent: EntryIntent) -> EntryIntent:
        normalized = intent.symbol.upper()
        saved = intent.model_copy(update={"symbol": normalized})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO entry_intents
                    (id, symbol, timeframe, direction, status, zone_lower, zone_upper, expires_at, updated_at, created_at, payload, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(saved.id),
                    saved.symbol,
                    saved.timeframe,
                    saved.direction,
                    saved.status,
                    saved.zone_lower,
                    saved.zone_upper,
                    saved.expires_at.isoformat(),
                    saved.updated_at.isoformat(),
                    saved.created_at.isoformat(),
                    _dump_model(saved),
                    saved.kind,
                ),
            )
        return saved

    def get_entry_intent(self, intent_id: UUID) -> EntryIntent | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM entry_intents WHERE id = ?", (str(intent_id),)).fetchone()
        return EntryIntent.model_validate_json(row["payload"]) if row else None

    def list_entry_intents(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[EntryIntent]:
        query = "SELECT payload FROM entry_intents"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [EntryIntent.model_validate_json(row["payload"]) for row in rows]

    def upsert_universe_discovery(self, discovery: UniverseDiscovery) -> UniverseDiscovery:
        normalized = discovery.model_copy(update={"symbol": discovery.symbol.upper()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO universe_discoveries
                    (id, symbol, timeframe, asset_class, signature_key, status, gate_passed, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol.upper(),
                    normalized.timeframe,
                    normalized.asset_class,
                    normalized.signature_key,
                    normalized.status,
                    1 if normalized.gate_passed else 0,
                    normalized.created_at.isoformat(),
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_universe_discoveries(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[UniverseDiscovery]:
        query = "SELECT payload FROM universe_discoveries"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [UniverseDiscovery.model_validate_json(row["payload"]) for row in rows]

    def list_recent_gate_passed_universe_discoveries(self, limit: int = 500) -> list[UniverseDiscovery]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM (
                    SELECT payload, gate_passed, created_at
                    FROM universe_discoveries
                    ORDER BY created_at DESC
                    LIMIT ?
                ) AS recent
                WHERE gate_passed = 1
                ORDER BY created_at DESC
                """,
                (limit,),
            ).fetchall()
        return [UniverseDiscovery.model_validate_json(row["payload"]) for row in rows]

    def list_watchlist(self) -> list[WatchlistItem]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM watchlist ORDER BY added_at DESC").fetchall()
        return [WatchlistItem.model_validate_json(row["payload"]) for row in rows]

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        normalized = item.symbol.upper()
        stored = item.model_copy(update={"symbol": normalized})
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO watchlist (symbol, added_at, asset_class, payload) VALUES (?, ?, ?, ?)",
                (normalized, stored.added_at.isoformat(), stored.asset_class, _dump_model(stored)),
            )
        return stored

    def remove_watchlist_item(self, symbol: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        return cursor.rowcount > 0

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        with self._connect() as connection:
            connection.execute("DELETE FROM symbol_catalog")
            connection.executemany(
                "INSERT OR REPLACE INTO symbol_catalog (symbol, updated_at, asset_class, payload) VALUES (?, ?, ?, ?)",
                [
                    (
                        item.symbol.upper(),
                        item.updated_at.isoformat(),
                        item.asset_class,
                        _dump_model(item),
                    )
                    for item in symbols
                ],
            )
        return len(symbols)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        with self._connect() as connection:
            if needle:
                rows = connection.execute(
                    """
                    SELECT payload
                    FROM symbol_catalog
                    WHERE symbol LIKE ? OR asset_class LIKE ? OR payload LIKE ?
                    ORDER BY length(symbol), symbol
                    LIMIT ?
                    """,
                    (f"%{needle}%", f"%{needle.lower()}%", f"%{needle}%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM symbol_catalog ORDER BY length(symbol), symbol LIMIT ?",
                    (limit,),
                ).fetchall()
        return [CatalogSymbol.model_validate_json(row["payload"]) for row in rows]

    def symbol_catalog_updated_at(self) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(updated_at) AS updated_at FROM symbol_catalog").fetchone()
        value = row["updated_at"] if row else None
        return datetime.fromisoformat(value) if value else None

    def add_entry_scenario(self, scenario: EntryScenario) -> EntryScenario:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO entry_scenarios
                    (id, symbol, direction, linked_position_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(scenario.id),
                    scenario.symbol.upper(),
                    scenario.direction.value,
                    str(scenario.linked_position_id) if scenario.linked_position_id else None,
                    scenario.created_at.isoformat(),
                    _dump_model(scenario),
                ),
            )
        return scenario

    def get_entry_scenario(self, scenario_id: UUID) -> EntryScenario | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM entry_scenarios WHERE id = ?", (str(scenario_id),)).fetchone()
        return EntryScenario.model_validate_json(row["payload"]) if row else None

    def list_entry_scenarios(self, symbol: str | None = None, limit: int = 50) -> list[EntryScenario]:
        with self._connect() as connection:
            if symbol:
                rows = connection.execute(
                    "SELECT payload FROM entry_scenarios WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM entry_scenarios ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [EntryScenario.model_validate_json(row["payload"]) for row in rows]

    def find_matching_scenario(self, symbol: str, direction: str, within_hours: int = 72) -> EntryScenario | None:
        from datetime import timedelta

        cutoff = (utc_now() - timedelta(hours=within_hours)).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM entry_scenarios
                WHERE symbol = ? AND direction = ? AND linked_position_id IS NULL AND created_at >= ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (symbol.upper(), direction, cutoff),
            ).fetchone()
        return EntryScenario.model_validate_json(row["payload"]) if row else None

    def link_scenario_position(self, scenario_id: UUID, position_id: UUID) -> EntryScenario | None:
        scenario = self.get_entry_scenario(scenario_id)
        if scenario is None:
            return None
        updated = scenario.model_copy(update={"linked_position_id": position_id})
        return self.add_entry_scenario(updated)
