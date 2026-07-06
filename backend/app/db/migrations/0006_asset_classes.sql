ALTER TABLE watchlist ADD COLUMN asset_class TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE symbol_catalog ADD COLUMN asset_class TEXT NOT NULL DEFAULT 'unknown';
CREATE INDEX IF NOT EXISTS idx_symbol_catalog_asset_class
    ON symbol_catalog(asset_class, symbol);
