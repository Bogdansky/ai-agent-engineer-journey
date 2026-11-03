import logging
import time
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import orders
from .config import Settings, load_settings
from .db import ConnectionPool, PoolExhausted
from .logging_setup import configure_logging
from .payments import PaymentDeclined, PaymentError

http_log = logging.getLogger("app.http")
main_log = logging.getLogger("app.main")


class NewOrder(BaseModel):
    customer_id: int = Field(gt=0)
    amount_cents: int = Field(gt=0)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        anyio.to_thread.current_default_thread_limiter().total_tokens = settings.max_threads
        pool = ConnectionPool(settings.database_path, settings.db_pool_size, settings.db_acquire_timeout_s)
        orders.init_schema(pool)
        app.state.pool = pool
        main_log.info(
            "startup complete version=%s pool_size=%d payments_url=%s",
            settings.app_version,
            settings.db_pool_size,
            settings.payments_url,
        )
        yield
        pool.close()

    app = FastAPI(title="orders-api", lifespan=lifespan)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - started) * 1000)
        extra = ""
        if request.url.path != "/health":
            pool: ConnectionPool = request.app.state.pool
            extra = f" pool_in_use={pool.in_use}/{pool.size}"
        http_log.info(
            "method=%s path=%s status=%d duration_ms=%d%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra,
        )
        return response

    @app.exception_handler(orders.OrderNotFound)
    async def _not_found(_: Request, exc: orders.OrderNotFound):
        return JSONResponse({"error": "order_not_found"}, status_code=404)

    @app.exception_handler(PoolExhausted)
    async def _pool_exhausted(_: Request, exc: PoolExhausted):
        return JSONResponse({"error": "database_unavailable"}, status_code=503)

    @app.exception_handler(PaymentDeclined)
    async def _declined(_: Request, exc: PaymentDeclined):
        return JSONResponse({"error": "payment_declined"}, status_code=402)

    @app.exception_handler(PaymentError)
    async def _payment_error(_: Request, exc: PaymentError):
        return JSONResponse({"error": "payment_gateway_unavailable"}, status_code=502)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/orders/{order_id}")
    def get_order(order_id: int, request: Request):
        return orders.get_order(request.app.state.pool, order_id)

    @app.get("/orders")
    def list_orders(request: Request, limit: int = 50):
        return orders.list_orders(request.app.state.pool, limit)

    @app.post("/orders", status_code=201)
    def create_order(body: NewOrder, request: Request):
        return orders.create_order(request.app.state.pool, settings, body.customer_id, body.amount_cents)

    return app
