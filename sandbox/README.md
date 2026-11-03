# orders-api

Small order service: creates orders (charging a payment gateway first) and serves them from SQLite.

## Endpoints

- `GET /health`
- `GET /orders` — latest orders
- `GET /orders/{id}`
- `POST /orders` — `{"customer_id": 1, "amount_cents": 1999}`

## Configuration

Read from environment variables (see `deploy/app.env` for production values):
`PAYMENTS_URL`, `PAYMENTS_TIMEOUT_S`, `DB_POOL_SIZE`, `DB_ACQUIRE_TIMEOUT_S`, `MAX_THREADS`, `DATABASE_PATH`.
`APP_VERSION` is injected by the deploy pipeline.

## Run

```
uvicorn --factory app.main:create_app
```

Logs go to stdout and are shipped to `logs/app.log` on the host.
