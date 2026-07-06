# Bitget Integration

## API Key Setup

1. Create a Bitget system-generated API key.
2. Use read-only permissions only.
3. Do not enable withdrawal permissions.
4. Set a passphrase and store it in a password manager.
5. Use an IP whitelist when running outside local development.

## Environment

Create `backend/.env` from `backend/.env.example`.

```env
MARKET_DATA_PROVIDER=bitget
DATABASE_URL=sqlite:///./fomo_control_engine.db
BITGET_BASE_URL=https://api.bitget.com
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
BITGET_PRODUCT_TYPE=USDT-FUTURES
BITGET_MARGIN_COIN=USDT
BITGET_LOCALE=en-US
```

`BITGET_API_SECRET` and `BITGET_API_PASSPHRASE` are required for private read-only position sync. Public market data works without private credentials.

## Test Connection

```bash
curl -X POST http://127.0.0.1:8875/api/system/bitget/test-connection
```

Expected private statuses:

- `ok`: read-only position endpoint is reachable.
- `not_configured`: key, secret, or passphrase is missing.
- `permission_error`: API key does not have required futures position read permission.
- `error`: network, signature, or Bitget API error.

## Position Sync

```bash
curl -X POST http://127.0.0.1:8875/api/account/bitget/sync-positions
curl http://127.0.0.1:8875/api/account/bitget/positions
```

Sync creates or updates internal positions with `source=bitget`. Positions that disappear from Bitget open-position data are treated as exchange-side exits: the app creates an internal trade review record, closes the internal position, and uses the last received mark/current price as the review exit price. This is still read-only bookkeeping and does not submit an exchange order.

## Endpoints Used

- `GET /api/v2/mix/market/candles`
- `GET /api/v2/mix/market/ticker`
- `GET /api/v2/mix/market/current-fund-rate`
- `GET /api/v2/mix/market/open-interest`
- `GET /api/v2/mix/position/all-position`

No order or withdrawal endpoints are used.
