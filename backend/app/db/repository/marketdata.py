from __future__ import annotations

from datetime import datetime
import json

from app.db.models import DatabaseMaintenanceEvent, DerivativeDataSnapshot, DerivativeMetric, LiquidationEvent, MarketSnapshotRecord, WhaleEvent, WhaleWallet
from .base import _dump_model, _json_cache_default


class MemoryMarketdataRepositoryMixin:
    def upsert_whale_wallet(self, wallet: WhaleWallet) -> WhaleWallet:
        self.whale_wallets[wallet.address.lower()] = wallet
        return wallet

    def get_whale_wallet(self, address: str) -> WhaleWallet | None:
        return self.whale_wallets.get(address.lower())

    def list_whale_wallets(self, active: bool | None = None, limit: int = 20) -> list[WhaleWallet]:
        wallets = list(self.whale_wallets.values())
        if active is not None:
            wallets = [wallet for wallet in wallets if wallet.active is active]
        return sorted(wallets, key=lambda wallet: wallet.added_at)[:limit]

    def remove_whale_wallet(self, address: str) -> bool:
        return self.whale_wallets.pop(address.lower(), None) is not None

    def add_whale_event(self, event: WhaleEvent) -> bool:
        if event.id in self.whale_events:
            return False
        self.whale_events[event.id] = event
        return True

    def list_whale_events(
        self, symbol: str | None = None, wallet_address: str | None = None, since: datetime | None = None, limit: int = 500
    ) -> list[WhaleEvent]:
        events = list(self.whale_events.values())
        if symbol:
            events = [event for event in events if event.symbol.upper() == symbol.upper()]
        if wallet_address:
            events = [event for event in events if event.wallet_address.lower() == wallet_address.lower()]
        if since:
            events = [event for event in events if event.event_at >= since]
        return sorted(events, key=lambda event: event.event_at, reverse=True)[:limit]

    def get_whale_position_state(self, wallet_address: str, coin: str) -> dict | None:
        state = self.whale_position_states.get((wallet_address.lower(), coin.upper()))
        return dict(state) if state is not None else None

    def list_whale_position_states(self, wallet_address: str | None = None, limit: int = 500) -> list[dict]:
        rows = [dict(state) for (address, _coin), state in self.whale_position_states.items() if wallet_address is None or address == wallet_address.lower()]
        return sorted(rows, key=lambda row: str(row.get("coin") or ""))[:limit]

    def upsert_whale_position_state(self, wallet_address: str, coin: str, state: dict) -> bool:
        self.whale_position_states[(wallet_address.lower(), coin.upper())] = dict(state)
        return True

    def delete_whale_position_state(self, wallet_address: str, coin: str) -> bool:
        return self.whale_position_states.pop((wallet_address.lower(), coin.upper()), None) is not None

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        self.market_snapshots[snapshot.id] = snapshot
        return snapshot

    def add_derivative_snapshot(self, snapshot: DerivativeDataSnapshot) -> DerivativeDataSnapshot:
        self.derivative_snapshots[snapshot.id] = snapshot
        return snapshot

    def list_derivative_snapshots(self, symbol: str | None = None, provider: str | None = None, limit: int = 100) -> list[DerivativeDataSnapshot]:
        snapshots = list(self.derivative_snapshots.values())
        if symbol:
            snapshots = [snapshot for snapshot in snapshots if snapshot.symbol.upper() == symbol.upper()]
        if provider:
            snapshots = [snapshot for snapshot in snapshots if snapshot.provider == provider]
        return sorted(snapshots, key=lambda item: item.created_at, reverse=True)[:limit]

    def latest_derivative_snapshot(self, symbol: str, provider: str | None = None) -> DerivativeDataSnapshot | None:
        snapshots = self.list_derivative_snapshots(symbol=symbol, provider=provider, limit=1)
        return snapshots[0] if snapshots else None

    def delete_derivative_snapshots_before(self, cutoff: datetime) -> int:
        ids = [snapshot_id for snapshot_id, snapshot in self.derivative_snapshots.items() if snapshot.as_of < cutoff]
        for snapshot_id in ids:
            self.derivative_snapshots.pop(snapshot_id, None)
        return len(ids)

    def add_derivative_metric(self, metric: DerivativeMetric) -> DerivativeMetric:
        self.derivative_metrics[metric.id] = metric
        return metric

    def list_derivative_metrics(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[DerivativeMetric]:
        metrics = list(self.derivative_metrics.values())
        if symbol:
            metrics = [metric for metric in metrics if metric.symbol.upper() == symbol.upper()]
        if source:
            metrics = [metric for metric in metrics if metric.source == source]
        return sorted(metrics, key=lambda item: item.as_of, reverse=True)[:limit]

    def latest_derivative_metric(self, symbol: str, source: str | None = None) -> DerivativeMetric | None:
        metrics = self.list_derivative_metrics(symbol=symbol, source=source, limit=1)
        return metrics[0] if metrics else None

    def delete_derivative_metrics_before(self, cutoff: datetime) -> int:
        ids = [metric_id for metric_id, metric in self.derivative_metrics.items() if metric.as_of < cutoff]
        for metric_id in ids:
            self.derivative_metrics.pop(metric_id, None)
        return len(ids)

    def add_liquidation_event(self, event: LiquidationEvent) -> LiquidationEvent:
        self.liquidation_events[event.id] = event
        return event

    def list_liquidation_events(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[LiquidationEvent]:
        events = list(self.liquidation_events.values())
        if symbol:
            events = [event for event in events if event.symbol.upper() == symbol.upper()]
        if source:
            events = [event for event in events if event.source == source]
        return sorted(events, key=lambda item: item.bucket_start, reverse=True)[:limit]

    def delete_liquidation_events_before(self, cutoff: datetime) -> int:
        ids = [event_id for event_id, event in self.liquidation_events.items() if event.bucket_start < cutoff]
        for event_id in ids:
            self.liquidation_events.pop(event_id, None)
        return len(ids)

    def add_database_maintenance_event(self, event: DatabaseMaintenanceEvent) -> DatabaseMaintenanceEvent:
        self.database_maintenance_events[event.id] = event
        return event

    def list_database_maintenance_events(self, event_type: str | None = None, limit: int = 50) -> list[DatabaseMaintenanceEvent]:
        events = list(self.database_maintenance_events.values())
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        return sorted(events, key=lambda item: item.created_at, reverse=True)[:limit]


class SQLiteMarketdataRepositoryMixin:
    def upsert_whale_wallet(self, wallet: WhaleWallet) -> WhaleWallet:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO whale_wallets (address, active, added_at, payload) VALUES (?, ?, ?, ?)",
                (wallet.address.lower(), int(wallet.active), wallet.added_at.isoformat(), _dump_model(wallet)),
            )
        return wallet

    def get_whale_wallet(self, address: str) -> WhaleWallet | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM whale_wallets WHERE address = ?", (address.lower(),)).fetchone()
        return WhaleWallet.model_validate_json(row["payload"]) if row else None

    def list_whale_wallets(self, active: bool | None = None, limit: int = 20) -> list[WhaleWallet]:
        query = "SELECT payload FROM whale_wallets"
        params: tuple[object, ...]
        if active is None:
            query += " ORDER BY added_at ASC LIMIT ?"
            params = (limit,)
        else:
            query += " WHERE active = ? ORDER BY added_at ASC LIMIT ?"
            params = (int(active), limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [WhaleWallet.model_validate_json(row["payload"]) for row in rows]

    def remove_whale_wallet(self, address: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM whale_wallets WHERE address = ?", (address.lower(),))
        return cursor.rowcount > 0

    def add_whale_event(self, event: WhaleEvent) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO whale_events
                (id, wallet_address, symbol, event_type, event_at, size_usd, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(event.id),
                    event.wallet_address.lower(),
                    event.symbol.upper(),
                    event.event,
                    event.event_at.isoformat(),
                    event.size_usd,
                    _dump_model(event),
                ),
            )
        return cursor.rowcount > 0

    def list_whale_events(
        self, symbol: str | None = None, wallet_address: str | None = None, since: datetime | None = None, limit: int = 500
    ) -> list[WhaleEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if wallet_address:
            clauses.append("wallet_address = ?")
            params.append(wallet_address.lower())
        if since:
            clauses.append("event_at >= ?")
            params.append(since.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(f"SELECT payload FROM whale_events{where} ORDER BY event_at DESC LIMIT ?", tuple(params)).fetchall()
        return [WhaleEvent.model_validate_json(row["payload"]) for row in rows]

    def get_whale_position_state(self, wallet_address: str, coin: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM whale_position_states WHERE wallet_address = ? AND coin = ?", (wallet_address.lower(), coin.upper())
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_whale_position_states(self, wallet_address: str | None = None, limit: int = 500) -> list[dict]:
        with self._connect() as connection:
            if wallet_address:
                rows = connection.execute(
                    "SELECT payload FROM whale_position_states WHERE wallet_address = ? ORDER BY coin LIMIT ?", (wallet_address.lower(), limit)
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM whale_position_states ORDER BY wallet_address, coin LIMIT ?", (limit,)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def upsert_whale_position_state(self, wallet_address: str, coin: str, state: dict) -> bool:
        payload = json.dumps(state, ensure_ascii=False, default=_json_cache_default)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO whale_position_states (wallet_address, coin, payload) VALUES (?, ?, ?)", (wallet_address.lower(), coin.upper(), payload)
            )
        return True

    def delete_whale_position_state(self, wallet_address: str, coin: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM whale_position_states WHERE wallet_address = ? AND coin = ?", (wallet_address.lower(), coin.upper()))
        return cursor.rowcount > 0

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO market_snapshots
                    (id, symbol, timeframe, provider, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.timeframe,
                    snapshot.provider,
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def add_derivative_snapshot(self, snapshot: DerivativeDataSnapshot) -> DerivativeDataSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO derivative_snapshots
                    (id, symbol, provider, tier, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.provider,
                    snapshot.tier,
                    snapshot.as_of.isoformat(),
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def list_derivative_snapshots(self, symbol: str | None = None, provider: str | None = None, limit: int = 100) -> list[DerivativeDataSnapshot]:
        query = "SELECT payload FROM derivative_snapshots"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [DerivativeDataSnapshot.model_validate_json(row["payload"]) for row in rows]

    def latest_derivative_snapshot(self, symbol: str, provider: str | None = None) -> DerivativeDataSnapshot | None:
        snapshots = self.list_derivative_snapshots(symbol=symbol, provider=provider, limit=1)
        return snapshots[0] if snapshots else None

    def delete_derivative_snapshots_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM derivative_snapshots WHERE as_of < ?",
                (cutoff.isoformat(),),
            )
            return int(cursor.rowcount or 0)

    def add_derivative_metric(self, metric: DerivativeMetric) -> DerivativeMetric:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO deriv_metrics
                    (id, symbol, source, tier, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metric.id),
                    metric.symbol.upper(),
                    metric.source,
                    metric.tier,
                    metric.as_of.isoformat(),
                    metric.created_at.isoformat(),
                    _dump_model(metric),
                ),
            )
        return metric

    def list_derivative_metrics(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[DerivativeMetric]:
        query = "SELECT payload FROM deriv_metrics"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY as_of DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [DerivativeMetric.model_validate_json(row["payload"]) for row in rows]

    def latest_derivative_metric(self, symbol: str, source: str | None = None) -> DerivativeMetric | None:
        metrics = self.list_derivative_metrics(symbol=symbol, source=source, limit=1)
        return metrics[0] if metrics else None

    def delete_derivative_metrics_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM deriv_metrics WHERE as_of < ?", (cutoff.isoformat(),))
            return int(cursor.rowcount or 0)

    def add_liquidation_event(self, event: LiquidationEvent) -> LiquidationEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO liquidation_events
                    (id, symbol, source, interval, bucket_start, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.symbol.upper(),
                    event.source,
                    event.interval,
                    event.bucket_start.isoformat(),
                    event.created_at.isoformat(),
                    _dump_model(event),
                ),
            )
        return event

    def list_liquidation_events(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[LiquidationEvent]:
        query = "SELECT payload FROM liquidation_events"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY bucket_start DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [LiquidationEvent.model_validate_json(row["payload"]) for row in rows]

    def delete_liquidation_events_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM liquidation_events WHERE bucket_start < ?",
                (cutoff.isoformat(),),
            )
            return int(cursor.rowcount or 0)

    def add_database_maintenance_event(self, event: DatabaseMaintenanceEvent) -> DatabaseMaintenanceEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO database_maintenance_events
                    (id, event_type, status, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.event_type,
                    event.status,
                    event.created_at.isoformat(),
                    _dump_model(event),
                ),
            )
        return event

    def list_database_maintenance_events(self, event_type: str | None = None, limit: int = 50) -> list[DatabaseMaintenanceEvent]:
        query = "SELECT payload FROM database_maintenance_events"
        params: tuple[str | int, ...]
        if event_type:
            query += " WHERE event_type = ?"
            params = (event_type, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DatabaseMaintenanceEvent.model_validate_json(row["payload"]) for row in rows]
