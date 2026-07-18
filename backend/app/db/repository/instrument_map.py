from __future__ import annotations

from app.db.models import InstrumentMap
from app.db.repository.base import _dump_model


class MemoryInstrumentMapRepositoryMixin:
    def upsert_instrument_map(self, item: InstrumentMap) -> InstrumentMap:
        stored = item.model_copy(update={"bitget_symbol": item.bitget_symbol.upper(), "toss_symbol": item.toss_symbol.upper()})
        self.instrument_maps[stored.bitget_symbol] = stored
        return stored

    def get_instrument_map(self, bitget_symbol: str) -> InstrumentMap | None:
        return self.instrument_maps.get(bitget_symbol.upper())

    def list_instrument_maps(self, status: str | None = None) -> list[InstrumentMap]:
        items = list(self.instrument_maps.values())
        if status:
            items = [item for item in items if item.verification_status == status]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)


class SQLiteInstrumentMapRepositoryMixin:
    def upsert_instrument_map(self, item: InstrumentMap) -> InstrumentMap:
        stored = item.model_copy(update={"bitget_symbol": item.bitget_symbol.upper(), "toss_symbol": item.toss_symbol.upper()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO instrument_map
                    (bitget_symbol, bitget_type, toss_symbol, toss_market, verification_status, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bitget_symbol) DO UPDATE SET
                    bitget_type=excluded.bitget_type,
                    toss_symbol=excluded.toss_symbol,
                    toss_market=excluded.toss_market,
                    verification_status=excluded.verification_status,
                    updated_at=excluded.updated_at,
                    payload=excluded.payload
                """,
                (
                    stored.bitget_symbol,
                    stored.bitget_type,
                    stored.toss_symbol,
                    stored.toss_market,
                    stored.verification_status,
                    stored.updated_at.isoformat(),
                    _dump_model(stored),
                ),
            )
        return stored

    def get_instrument_map(self, bitget_symbol: str) -> InstrumentMap | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM instrument_map WHERE bitget_symbol = ?",
                (bitget_symbol.upper(),),
            ).fetchone()
        return InstrumentMap.model_validate_json(row["payload"]) if row else None

    def list_instrument_maps(self, status: str | None = None) -> list[InstrumentMap]:
        query = "SELECT payload FROM instrument_map"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE verification_status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [InstrumentMap.model_validate_json(row["payload"]) for row in rows]
